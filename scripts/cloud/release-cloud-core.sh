#!/usr/bin/env bash
# Host side of scripts/cloud/release-cloud-core.ps1: put the already-extracted tree at
# $BASE/app.next into production as the api workload (ADR-0042).
#
#   release-cloud-core.sh SHA [--preflight]
#
# Transaction, in order:
#   env-file present + posture 600:root                                  66 / 72
#   docker compose config validation on the NEW tree                     71
#   (--preflight stops here: reports, removes app.next, changes nothing)
#   keep the previous image as pagentos/cloud-core:prev
#   swap trees: app -> app.prev, app.next -> app
#   build api image
#   pre-migration backup (ADR-0122; a warning, not a stop, when not installed)     67
#   alembic upgrade head (additive migrations; never downgraded on rollback)
#   recreate ONLY the api workload (--no-deps --force-recreate --wait)
#   health on loopback
#   served realtime contract_version == the tree's CONTRACT_VERSION           73
#   PAGENTOS_VOICE_OPENAI_API_KEY PRESENT inside the container when the env file has it  68
#   with the key: marin and cedar each minted and echoed unchanged by the vendor  74
#   health lists openai-realtime when the key is present                  69
#   one real provider call from THIS host (realtime_smoke --mode minimal)  70
# Any failure after the swap rolls back: previous tree and image restored, api recreated.
#
# Env overrides for tests: PAGENTOS_BASE (default /opt/pagentos), PAGENTOS_API_CONTAINER,
# PAGENTOS_HEALTH_URL, PAGENTOS_IMAGE (default pagentos/cloud-core:local).
set -eu

sha=${1:?SHA}
mode=${2:-release}
base=${PAGENTOS_BASE:-/opt/pagentos}
envf="$base/.env"
next="$base/app.next"
cur="$base/app"
prev="$base/app.prev"
container=${PAGENTOS_API_CONTAINER:-pagentos-prod-api}
image=${PAGENTOS_IMAGE:-pagentos/cloud-core:local}
health_url=${PAGENTOS_HEALTH_URL:-http://127.0.0.1:8001/v1/system/health}
key_name=PAGENTOS_VOICE_OPENAI_API_KEY
provider=openai-realtime

if [ ! -d "$next" ]; then
    echo "$next is missing; the driver extracts the tree there first" >&2
    exit 65
fi
echo "$sha" > "$next/RELEASE"
if [ ! -f "$envf" ]; then
    echo "$envf missing; run the first deployment (deploy-cloud-core.sh) before a release" >&2
    exit 66
fi
posture="$(stat -c '%a:%U' "$envf")"
if [ "$posture" != "600:root" ] && [ -z "${PAGENTOS_ALLOW_NONROOT_ENV:-}" ]; then
    echo "env file posture is $posture, expected 600:root" >&2
    exit 72
fi

compose_next=(docker compose -f "$next/infra/docker/docker-compose.prod.yml" --env-file "$envf")
if ! "${compose_next[@]}" config -q; then
    echo "docker compose config is INVALID for $sha; nothing changed" >&2
    rm -rf "$next"
    exit 71
fi
wired="$("${compose_next[@]}" config 2>/dev/null | grep -Ec "^[[:space:]]*$key_name:" || true)"
key_on_host="$(grep -c "^$key_name=" "$envf" || true)"
echo "preflight: tree $sha extracted, compose valid, env posture $posture, $key_name on host: $([ "$key_on_host" -gt 0 ] && echo PRESENT || echo ABSENT), wired in compose: $wired service(s)"
if [ "$mode" = "--preflight" ]; then
    rm -rf "$next"
    echo "preflight only; nothing changed"
    exit 0
fi

rollback() {
    echo "ROLLBACK: restoring the previous tree and image" >&2
    if [ -d "$prev" ]; then
        rm -rf "$cur"
        mv "$prev" "$cur"
    fi
    docker tag "${image%:*}:prev" "$image" 2>/dev/null || true
    if [ -d "$cur/infra/docker" ]; then
        (cd "$cur/infra/docker" && docker compose -f docker-compose.prod.yml --env-file "$envf" \
            up -d --no-deps --force-recreate --wait api) || true
    fi
}
on_exit() {
    rc=$?
    if [ "$rc" -ne 0 ]; then rollback; fi
}
trap on_exit EXIT

docker tag "$image" "${image%:*}:prev" 2>/dev/null || true
rm -rf "$prev"
if [ -d "$cur" ]; then mv "$cur" "$prev"; fi
mv "$next" "$cur"
echo "tree swapped: $cur is $sha (previous kept at $prev)"

cd "$cur/infra/docker"
compose=(docker compose -f docker-compose.prod.yml --env-file "$envf")
echo "building the api image..."
"${compose[@]}" build api 2>&1 | tail -2
# ADR-0122: a safety point before any schema change; the same rule as the blue/green path.
backup_bin=${PAGENTOS_BACKUP_BIN:-/opt/pagentos-backup}/backup-cloud-core.sh
if [ -f "$backup_bin" ]; then
    echo "pre-migration backup..."
    backup_log="$base/.pre-migration-backup.log"
    if ! bash "$backup_bin" --kind pre-migration --label "release-$(printf '%s' "$sha" | cut -c1-12)" > "$backup_log" 2>&1; then
        tail -5 "$backup_log" >&2
        echo "pre-migration backup FAILED; the release stops before any migration" >&2
        exit 67
    fi
    tail -1 "$backup_log"
else
    echo "WARNING: no backup tooling at $backup_bin; migrating WITHOUT a safety point (install-backup.sh)" >&2
fi
echo "applying migrations..."
"${compose[@]}" run --rm --no-deps --entrypoint uv api run alembic upgrade head 2>&1 | tail -2
echo "recreating the api workload (dependencies untouched)..."
"${compose[@]}" up -d --no-deps --force-recreate --wait api 2>&1 | tail -2

health="$(curl -fsS "$health_url")"
case "$health" in
    *'"status"'*) echo "health ok on $health_url";;
    *) echo "health FAILED on $health_url" >&2; exit 1;;
esac

# The tree says which wire contract it serves; the running api must say the same. A
# release that leaves an older contract in production is refused and rolled back.
version_file="$cur/services/api/app/voice/realtime_sessions/contract_version.py"
if [ -f "$version_file" ]; then
    expected_contract="$(grep -oE '^CONTRACT_VERSION[[:space:]]*=[[:space:]]*[0-9]+' "$version_file" | grep -oE '[0-9]+$' || true)"
    served_contract="$(printf '%s' "$health" | grep -oE '"contract_version":[[:space:]]*[0-9]+' | head -1 | grep -oE '[0-9]+$' || true)"
    if [ -z "$expected_contract" ] || [ "$served_contract" != "$expected_contract" ]; then
        echo "served realtime contract_version is '${served_contract:-absent}', the tree expects '${expected_contract:-?}'" >&2
        exit 73
    fi
    echo "realtime contract_version $served_contract served (matches the tree)"
fi

if [ "$key_on_host" -gt 0 ]; then
    present="$(docker exec "$container" sh -c "printenv $key_name >/dev/null 2>&1 && echo PRESENT || echo MISSING")"
    if [ "$present" != "PRESENT" ]; then
        echo "$key_name is on the host but MISSING inside the running $container" >&2
        exit 68
    fi
    length="$(docker exec "$container" sh -c "printenv $key_name | tr -d '\n' | wc -c" | tr -d '[:space:]')"
    fingerprint="$(docker exec "$container" sh -c "printenv $key_name | tr -d '\n' | sha256sum | cut -c1-12" | tr -d '[:space:]')"
    echo "in-container: $key_name PRESENT length=$length sha256=$fingerprint"
    providers="$(printf '%s' "$health" | grep -o '"providers":[[:space:]]*\[[^]]*\]' | head -1 || true)"
    case "$providers" in
        *"\"$provider\""*) echo "health lists provider $provider ($providers)";;
        *) echo "health does not list $provider (${providers:-no voice_realtime check})" >&2; exit 69;;
    esac
    err="$(mktemp)"
    if docker exec "$container" sh -c "uv run python scripts/realtime_smoke.py --mode minimal" >/dev/null 2>"$err"; then
        echo "provider self-test from this host: OK (one real client-secret mint)"
        rm -f "$err"
    else
        echo "provider self-test FAILED: $(tail -c 600 "$err" | grep -iv 'bearer' | tr '\n' ' ')" >&2
        rm -f "$err"
        exit 70
    fi
    # The owner's A/B candidates must reach the vendor unchanged (ADR-0043): one real
    # minimal mint per voice; the vendor echoes the session's audio.output.voice.
    for candidate in marin cedar; do
        out="$(mktemp)"
        if docker exec "$container" sh -c "uv run python scripts/realtime_smoke.py --mode minimal --voice $candidate" >"$out" 2>/dev/null \
           && grep -Eq "\"voice\":[[:space:]]*\"$candidate\"" "$out"; then
            echo "voice $candidate: minted and echoed unchanged by the provider"
            rm -f "$out"
        else
            echo "voice $candidate: not echoed unchanged by the provider" >&2
            rm -f "$out"
            exit 74
        fi
    done
else
    echo "note: $key_name is not on the host; realtime provider verification skipped"
fi

trap - EXIT
echo "RELEASE OK: $sha is running as $container"
