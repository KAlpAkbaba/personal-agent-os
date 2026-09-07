#!/usr/bin/env bash
# Host side of a ZERO-DOWNTIME Cloud Core release (docs/M18_4_SELF_EVOLUTION_SPEC.md §6):
# two api colours behind the edge, the idle colour brought up on the new tree, verified,
# switched to, the old colour drained and stopped. Rollback is the switch in reverse.
#
#   release-cloud-core-bluegreen.sh SHA [--preflight] [--rollback]
#
# Transaction, in order:
#   env-file present + posture 600:root                                  66 / 72
#   docker compose config validation on the NEW tree                     71
#   (--preflight stops here: reports, removes app.next, changes nothing)
#   swap trees: app -> app.prev, app.next -> app (rollback restores them)
#   build the image for THIS sha (pagentos/cloud-core:SHA; the old image is untouched)
#   alembic upgrade head (expand-only migrations, gated by test_migration_compatibility)
#   record the idle colour's image + release sha in the env file
#   up the IDLE colour only (--no-deps --wait); the active colour keeps serving
#   health on the idle colour (in-container; it has no published port)          75
#   served realtime contract_version == the tree's CONTRACT_VERSION           73
#   served release == SHA (the version model, spec §2)                          76
#   edge present (first cutover: the legacy api is stopped, the edge started)
#   switch the upstream to the idle colour + nginx reload                       77
#   health THROUGH the edge shows release == SHA
#   drain: keep the old colour up for PAGENTOS_DRAIN_S (default 60), then stop it
#   record RELEASE, LAST_KNOWN_GOOD (the previous sha) and the active colour
# Any failure after the switch switches back (the old colour is still up during the
# drain; after it, the old colour is started again first). Any failure before the switch
# stops the idle colour and restores the trees. The database is never downgraded.
#
# Env overrides for tests: PAGENTOS_BASE (default /opt/pagentos), PAGENTOS_EDGE_DIR
# (default $BASE/edge), PAGENTOS_HEALTH_URL, PAGENTOS_DRAIN_S, PAGENTOS_IMAGE_REPO.
set -eu

sha=${1:?SHA}
mode=${2:-release}
base=${PAGENTOS_BASE:-/opt/pagentos}
envf="$base/.env"
next="$base/app.next"
cur="$base/app"
prev="$base/app.prev"
# The edge reads its upstream file from the persistent volume (docker-compose.prod.yml
# mounts ${PAGENTOS_EDGE_DIR:-/mnt/pagentos-data/edge}); the script writes where compose
# mounts, and pins the path into the env file so the two can never disagree.
edge_dir=${PAGENTOS_EDGE_DIR:-/mnt/pagentos-data/edge}
health_url=${PAGENTOS_HEALTH_URL:-http://127.0.0.1:8001/v1/system/health}
drain_s=${PAGENTOS_DRAIN_S:-60}
image_repo=${PAGENTOS_IMAGE_REPO:-pagentos/cloud-core}
legacy_container=${PAGENTOS_API_CONTAINER:-pagentos-prod-api}

# ----------------------------------------------------------------- helpers

upsert_env() {
    # upsert_env KEY VALUE: one line per key in the env file, no duplicates.
    local key=$1 value=$2
    if grep -q "^$key=" "$envf"; then
        sed -i "s|^$key=.*|$key=$value|" "$envf"
    else
        printf '%s=%s\n' "$key" "$value" >> "$envf"
    fi
}

upper() { printf '%s' "$1" | tr '[:lower:]' '[:upper:]'; }

active_colour() {
    if [ -f "$edge_dir/active.txt" ]; then
        tr -d '[:space:]' < "$edge_dir/active.txt" | tr '[:upper:]' '[:lower:]'
    else
        echo ""
    fi
}

other_colour() { if [ "$1" = "blue" ]; then echo green; else echo blue; fi; }

write_upstream() {
    # write_upstream COLOUR: the one line the edge reads, plus the active marker.
    mkdir -p "$edge_dir"
    printf 'upstream pagentos_api { server api-%s:8001; }\n' "$1" > "$edge_dir/upstream.conf.next"
    mv "$edge_dir/upstream.conf.next" "$edge_dir/upstream.conf"
    printf '%s\n' "$1" > "$edge_dir/active.txt"
}

compose() { docker compose --profile bluegreen -f "$cur/infra/docker/docker-compose.prod.yml" --env-file "$envf" "$@"; }

in_container_health() {
    # in_container_health COLOUR: the colour's own health JSON, from inside the container
    # (the colours publish no port; only the edge does).
    compose exec -T "api-$1" python3 -c 'import sys, urllib.request; sys.stdout.write(urllib.request.urlopen("http://127.0.0.1:8001/v1/system/health", timeout=10).read().decode())'
}

wait_for_colour() {
    # wait_for_colour COLOUR: up to ~120 s for the colour's health to answer ok.
    local colour=$1 tries=0 body=""
    while [ "$tries" -lt 40 ]; do
        body="$(in_container_health "$colour" 2>/dev/null || true)"
        case "$body" in
            *'"status"'*) printf '%s' "$body"; return 0;;
        esac
        tries=$((tries + 1))
        sleep "${PAGENTOS_WAIT_STEP_S:-3}"
    done
    return 1
}

reload_edge() {
    compose exec -T edge nginx -t >/dev/null 2>&1 || { echo "edge config test FAILED" >&2; return 1; }
    compose exec -T edge nginx -s reload
}

json_field() {
    # json_field NAME BODY: the first "name": <number|"string"> value, or empty.
    printf '%s' "$2" | grep -oE "\"$1\":[[:space:]]*(\"[^\"]*\"|[0-9]+)" | head -1 | sed -E 's/^"[^"]*":[[:space:]]*//; s/^"//; s/"$//'
}

# --------------------------------------------------------------- preflight

if [ ! -d "$next" ] && [ "$mode" != "--rollback" ]; then
    echo "$next is missing; the driver extracts the tree there first" >&2
    exit 65
fi
if [ ! -f "$envf" ]; then
    echo "$envf missing; run the first deployment (deploy-cloud-core.sh) before a release" >&2
    exit 66
fi
posture="$(stat -c '%a:%U' "$envf")"
if [ "$posture" != "600:root" ] && [ -z "${PAGENTOS_ALLOW_NONROOT_ENV:-}" ]; then
    echo "env file posture is $posture, expected 600:root" >&2
    exit 72
fi

active="$(active_colour)"
if [ -z "$active" ]; then
    # The first blue/green cutover: nothing is behind the edge yet. blue becomes active
    # after this release; the legacy single container is stopped at the switch.
    first_cutover=1
    active="green"
else
    first_cutover=0
fi
case "$active" in
    blue|green) ;;
    *) echo "active colour marker is '$active'; expected blue or green" >&2; exit 78;;
esac
idle="$(other_colour "$active")"
IDLE="$(upper "$idle")"

# ----------------------------------------------------------------- rollback

do_rollback() {
    # Switch back to the colour that was active before this release started.
    echo "ROLLBACK: switching the edge back to api-$active" >&2
    if [ -d "$prev" ]; then
        rm -rf "$cur"
        mv "$prev" "$cur"
    fi
    if [ "$first_cutover" = "1" ]; then
        echo "ROLLBACK: first cutover failed; the legacy api container is started again" >&2
        (cd "$cur/infra/docker" && docker compose -f docker-compose.prod.yml --env-file "$envf" up -d --no-deps --wait api) || true
        rm -f "$edge_dir/active.txt"
        return 0
    fi
    compose up -d --no-deps --wait "api-$active" >/dev/null 2>&1 || true
    write_upstream "$active"
    reload_edge || true
    compose stop "api-$idle" >/dev/null 2>&1 || true
}

if [ "$mode" = "--rollback" ]; then
    if [ "$first_cutover" = "1" ]; then
        echo "nothing to roll back: no blue/green release has happened yet" >&2
        exit 65
    fi
    prev_colour="$(other_colour "$active")"
    PREV="$(upper "$prev_colour")"
    # The colour we return to runs the sha the env file recorded for it when it was last
    # released; that sha becomes RELEASE, and the sha we leave becomes LAST_KNOWN_GOOD
    # (the bookkeeping must name what is actually running, not what a file remembers).
    target_sha="$(grep "^PAGENTOS_RELEASE_$PREV=" "$envf" | head -1 | sed 's/^[^=]*=//' || true)"
    leaving_sha="$(cat "$base/RELEASE" 2>/dev/null || true)"
    echo "rolling back: edge -> api-$prev_colour (${target_sha:-sha unknown}; leaving ${leaving_sha:-unknown})"
    compose up -d --no-deps --wait "api-$prev_colour"
    body="$(wait_for_colour "$prev_colour")" || { echo "api-$prev_colour did not become healthy; the edge stays on api-$active" >&2; exit 75; }
    write_upstream "$prev_colour"
    reload_edge
    compose stop "api-$active" >/dev/null 2>&1 || true
    [ -n "$target_sha" ] && echo "$target_sha" > "$base/RELEASE"
    [ -n "$leaving_sha" ] && echo "$leaving_sha" > "$base/LAST_KNOWN_GOOD"
    [ -n "$leaving_sha" ] && upsert_env "PAGENTOS_LAST_KNOWN_GOOD" "$leaving_sha"
    echo "ROLLBACK OK: api-$prev_colour is active"
    exit 0
fi

echo "$sha" > "$next/RELEASE"
compose_next=(docker compose --profile bluegreen -f "$next/infra/docker/docker-compose.prod.yml" --env-file "$envf")
if ! "${compose_next[@]}" config -q; then
    echo "docker compose config is INVALID for $sha; nothing changed" >&2
    rm -rf "$next"
    exit 71
fi
if [ "$first_cutover" = "1" ]; then
    cutover_note="(first cutover: no colour is behind the edge yet) "
else
    cutover_note=""
fi
echo "preflight: tree $sha extracted, compose valid, env posture $posture, active colour $cutover_note$active, idle $idle, drain ${drain_s}s"
if [ "$mode" = "--preflight" ]; then
    rm -rf "$next"
    echo "preflight only; nothing changed"
    exit 0
fi

switched=0
on_exit() {
    rc=$?
    if [ "$rc" -ne 0 ]; then
        if [ "$switched" = "1" ]; then
            do_rollback
        else
            echo "ROLLBACK: the release failed before the switch; api-$active kept serving throughout" >&2
            compose stop "api-$idle" >/dev/null 2>&1 || true
            if [ -d "$prev" ]; then rm -rf "$cur"; mv "$prev" "$cur"; fi
        fi
    fi
}
trap on_exit EXIT

# ------------------------------------------------------------------- build

previous_sha="$(cat "$base/RELEASE" 2>/dev/null || true)"
rm -rf "$prev"
if [ -d "$cur" ]; then mv "$cur" "$prev"; fi
mv "$next" "$cur"
echo "tree swapped: $cur is $sha (previous kept at $prev)"

cd "$cur/infra/docker"
echo "building $image_repo:$sha ..."
docker build -t "$image_repo:$sha" "$cur/services/api" 2>&1 | tail -2
upsert_env "PAGENTOS_IMAGE_$IDLE" "$sha"
upsert_env "PAGENTOS_RELEASE_$IDLE" "$sha"
upsert_env "PAGENTOS_EDGE_DIR" "$edge_dir"
[ -n "$previous_sha" ] && upsert_env "PAGENTOS_LAST_KNOWN_GOOD" "$previous_sha"

echo "applying migrations (expand-only)..."
compose run --rm --no-deps --entrypoint uv "api-$idle" run alembic upgrade head 2>&1 | tail -2

echo "starting the idle colour api-$idle on $sha (api-$active keeps serving)..."
compose up -d --no-deps --wait "api-$idle" 2>&1 | tail -2

body="$(wait_for_colour "$idle")" || { echo "api-$idle never answered health" >&2; exit 75; }
echo "health ok on api-$idle"

version_file="$cur/services/api/app/voice/realtime_sessions/contract_version.py"
if [ -f "$version_file" ]; then
    expected_contract="$(grep -oE '^CONTRACT_VERSION[[:space:]]*=[[:space:]]*[0-9]+' "$version_file" | grep -oE '[0-9]+$' || true)"
    served_contract="$(printf '%s' "$body" | grep -oE '"contract_version":[[:space:]]*[0-9]+' | head -1 | grep -oE '[0-9]+$' || true)"
    if [ -z "$expected_contract" ] || [ "$served_contract" != "$expected_contract" ]; then
        echo "served realtime contract_version is '${served_contract:-absent}', the tree expects '${expected_contract:-?}'" >&2
        exit 73
    fi
    echo "realtime contract_version $served_contract served (matches the tree)"
fi
served_release="$(printf '%s' "$body" | grep -oE '"release":[[:space:]]*\{[^}]*' | grep -oE '"version":[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/' || true)"
if [ "$served_release" != "$sha" ]; then
    echo "api-$idle reports release '${served_release:-absent}', expected '$sha' (the version model is not wired)" >&2
    exit 76
fi
echo "api-$idle reports release $sha"

# ------------------------------------------------------------------ switch

if [ "$first_cutover" = "1" ]; then
    echo "first cutover: stopping the legacy $legacy_container and starting the edge (one last gap)"
    docker stop "$legacy_container" >/dev/null 2>&1 || true
    write_upstream "$idle"
    compose up -d --no-deps --wait edge 2>&1 | tail -2
else
    write_upstream "$idle"
    reload_edge || { echo "edge reload FAILED" >&2; exit 77; }
fi
switched=1
echo "edge -> api-$idle"

health="$(curl -fsS "$health_url")"
edge_release="$(printf '%s' "$health" | grep -oE '"release":[[:space:]]*\{[^}]*' | grep -oE '"version":[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/' || true)"
if [ "$edge_release" != "$sha" ]; then
    echo "through the edge the release is '${edge_release:-absent}', expected '$sha'" >&2
    exit 76
fi
echo "health through the edge: release $sha"

if [ "$first_cutover" != "1" ]; then
    echo "draining api-$active for ${drain_s}s (in-flight requests, device reconnects, the voice session's next tool call)..."
    sleep "$drain_s"
    compose stop "api-$active" 2>&1 | tail -1
    echo "api-$active stopped"
fi

echo "$sha" > "$base/RELEASE"
[ -n "$previous_sha" ] && echo "$previous_sha" > "$base/LAST_KNOWN_GOOD"
trap - EXIT
echo "RELEASE OK: $sha is running as api-$idle behind the edge (previous ${previous_sha:-none} kept as last known good)"
