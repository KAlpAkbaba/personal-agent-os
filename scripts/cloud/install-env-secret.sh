#!/usr/bin/env bash
# Host side of scripts/cloud/set-cloud-secret.ps1: install ONE secret into the production
# env file and prove the running API workload actually carries it (ADR-0042).
#
#   install-env-secret.sh NAME ENV_FILE REPO_ROOT EXPECT_PROVIDER RECREATE [VERIFY_CMD]
#
# The value arrives on STDIN (one line). Nothing here prints a value: runtime
# verification reports PRESENT/MISSING, the length and a 12-hex SHA-256 fingerprint.
#
# Transaction, in order - each step fails closed with its own exit code:
#   atomic env-file update (temp + mv, 0600 root)                      64 no value / 65 unsafe / 66 env file missing
#   env-file posture verification                                      72
#   docker compose config validation                                    71
#   compose wires NAME into a service (otherwise a restart is a no-op)  67  -> release the Cloud Core first
#   recreate ONLY the api workload (--no-deps --force-recreate --wait)
#   NAME is PRESENT inside the actual running container                68  (a restart that changed nothing FAILS)
#   /v1/system/health lists EXPECT_PROVIDER (when non-empty)           69
#   VERIFY_CMD runs inside the container - one real provider call     70
#
# Env overrides for tests: PAGENTOS_API_CONTAINER (default pagentos-prod-api),
# PAGENTOS_HEALTH_URL (default http://127.0.0.1:8001/v1/system/health).
set -eu
umask 077

name=${1:?NAME}
envf=${2:?ENV_FILE}
repo=${3:?REPO_ROOT}
expect=${4:-}
recreate=${5:-1}
verify_cmd=${6:-}
container=${PAGENTOS_API_CONTAINER:-pagentos-prod-api}
health_url=${PAGENTOS_HEALTH_URL:-http://127.0.0.1:8001/v1/system/health}

if ! [[ $name =~ ^[A-Za-z_][A-Za-z0-9_]{0,127}$ ]]; then
    echo "invalid secret name" >&2
    exit 65
fi

IFS= read -r value || true
value=${value%$'\r'}
if [ -z "$value" ]; then
    echo "no value received on stdin" >&2
    exit 64
fi
case "$value" in
    *[[:space:]]*|*'#'*|*'"'*|*"'"*|*'$'*|*'\'*)
        echo "value contains characters unsafe for an env file" >&2
        exit 65;;
esac
if [ ! -f "$envf" ]; then
    echo "$envf missing; deploy first" >&2
    exit 66
fi

tmp="$(mktemp "$envf.XXXXXX")"
grep -v "^$name=" "$envf" > "$tmp" || true
printf '%s=%s\n' "$name" "$value" >> "$tmp"
unset value
chmod 600 "$tmp"
chown root:root "$tmp" 2>/dev/null || true
mv -f "$tmp" "$envf"

posture="$(stat -c '%a:%U' "$envf")"
if [ "$posture" != "600:root" ] && [ -z "${PAGENTOS_ALLOW_NONROOT_ENV:-}" ]; then
    echo "env file posture is $posture, expected 600:root" >&2
    exit 72
fi
echo "installed $name into $envf (value not shown; posture $posture)"

compose=(docker compose -f "$repo/infra/docker/docker-compose.prod.yml" --env-file "$envf")
if ! "${compose[@]}" config -q; then
    echo "docker compose config is INVALID for $repo; nothing recreated" >&2
    exit 71
fi
if ! "${compose[@]}" config 2>/dev/null | grep -Eq "^[[:space:]]*$name:"; then
    echo "the compose definition in $repo does not wire $name into any service: a restart would change nothing. Release the current Cloud Core first (scripts/cloud/release-cloud-core.ps1), then re-run." >&2
    exit 67
fi
echo "compose valid; $name is wired"

# A BLUE/GREEN host does not have a container called "api" serving anything: one of
# api-blue / api-green is behind pagentos-prod-edge, and the edge owns port 8001. The
# single-container "api" service binds 8001 DIRECTLY, so recreating it here cannot start
# ("Bind for ...:8001 failed: port is already allocated"), leaves a dead container behind,
# and changes nothing about what is actually serving. That is what happened to the owner
# installing an Anthropic key on 2026-09-10.
active=""
for colour in blue green; do
    if [ -n "$(docker ps --filter "name=pagentos-prod-api-$colour" --filter status=running --format '{{.Names}}' 2>/dev/null)" ]; then
        active="pagentos-prod-api-$colour"
    fi
done

if [ -n "$active" ] && [ "$recreate" = "1" ]; then
    echo "$name is INSTALLED in $envf (posture verified), but this host runs blue/green:"
    echo "  $active is serving behind pagentos-prod-edge, which owns port 8001."
    echo "This installer recreates the single-container 'api' service, which binds 8001"
    echo "directly - so recreating it here would fail and change nothing that is serving."
    echo "Finish with the zero-downtime path, which brings the IDLE colour up on the new"
    echo "environment, verifies it, hands the device sessions over and switches:"
    echo "    .\scripts\cloud\release-cloud-core.ps1 -BlueGreen -Force"
    exit 73
fi

if [ "$recreate" = "1" ]; then
    # Only the api workload. Never PostgreSQL/Redis/MinIO/Temporal.
    "${compose[@]}" up -d --no-deps --force-recreate --wait api 2>&1 | tail -3
    echo "recreated $container"
fi

present="$(docker exec "$container" sh -c "printenv $name >/dev/null 2>&1 && echo PRESENT || echo MISSING")"
if [ "$present" != "PRESENT" ]; then
    echo "$name is in $envf but MISSING inside the running $container - the workload did not pick it up" >&2
    exit 68
fi
length="$(docker exec "$container" sh -c "printenv $name | tr -d '\n' | wc -c" | tr -d '[:space:]')"
fingerprint="$(docker exec "$container" sh -c "printenv $name | tr -d '\n' | sha256sum | cut -c1-12" | tr -d '[:space:]')"
echo "in-container: PRESENT length=$length sha256=$fingerprint"

if [ -n "$expect" ]; then
    health="$(curl -fsS "$health_url" 2>/dev/null || true)"
    providers="$(printf '%s' "$health" | grep -o '"providers":[[:space:]]*\[[^]]*\]' | head -1 || true)"
    case "$providers" in
        *"\"$expect\""*) echo "health lists provider $expect ($providers)";;
        *) echo "health at $health_url does not list $expect (${providers:-no voice_realtime check in this build})" >&2; exit 69;;
    esac
fi

if [ -n "$verify_cmd" ]; then
    err="$(mktemp)"
    if docker exec "$container" sh -c "$verify_cmd" >/dev/null 2>"$err"; then
        echo "provider self-test from this host: OK ($verify_cmd)"
        rm -f "$err"
    else
        echo "provider self-test FAILED: $(tail -c 600 "$err" | grep -iv 'bearer' | tr '\n' ' ')" >&2
        rm -f "$err"
        exit 70
    fi
fi
echo "SECRET OK: $name is live in $container"
