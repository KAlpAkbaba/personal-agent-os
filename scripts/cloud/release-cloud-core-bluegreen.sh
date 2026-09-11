#!/usr/bin/env bash
# Host side of a ZERO-DOWNTIME Cloud Core release (docs/M18_4_SELF_EVOLUTION_SPEC.md §6;
# ADR-0081 + addenda): two api colours behind the edge, the idle colour brought up on the
# new tree, verified, handed the device sessions, switched to, the old colour drained and
# stopped. Rollback is the switch in reverse.
#
#   release-cloud-core-bluegreen.sh SHA [--preflight]
#   release-cloud-core-bluegreen.sh SHA --rollback     switch to the other colour
#   release-cloud-core-bluegreen.sh SHA --reconcile    after a crash/reboot: rebuild the
#                                                      canonical state from the markers, the
#                                                      env file and the containers; discard a
#                                                      half-promoted candidate; never make one
#                                                      live silently
#   (--rollback / --reconcile also work as the only argument)
#
# Transaction, in order:
#   env-file present + posture 600:root                                  66 / 72
#   docker compose config validation on the NEW tree                     71
#   (--preflight stops here: reports, removes app.next, changes nothing)
#   swap trees: app -> app.prev, app.next -> app (rollback restores them)
#   build the image for THIS sha (pagentos/cloud-core:SHA; the old image is untouched)
#   pre-migration backup (ADR-0122; a warning, not a stop, when not installed)     74
#   alembic upgrade head (expand-only migrations, gated by test_migration_compatibility)
#   record the idle colour's image + release sha in the env file
#   up the IDLE colour only (--no-deps --wait); the active colour keeps serving
#   health on the idle colour (in-container; it has no published port)          75
#   served realtime contract_version == the tree's CONTRACT_VERSION           73
#   served release == SHA (the version model, spec §2)                          76
#   DEVICE HANDOFF (M18.4 gap 1): the device upstream -> idle colour, the active colour
#     drains its device sessions (the agent reconnects through the edge within ~1 s),
#     wait until the idle colour holds them                                       79
#   switch the HTTP upstream to the idle colour + nginx reload                     77
#   health THROUGH the edge shows release == SHA
#   drain: keep the old colour up for PAGENTOS_DRAIN_S (default 60), then stop it
#   record RELEASE, LAST_KNOWN_GOOD (the previous sha) and the active colour
# A release is COMPLETE only when RELEASE names the active colour's sha; --reconcile
# treats anything else as an interrupted promotion and returns to the last completed one.
# --reconcile exits: 0 consistent and ok; 80 neither colour serves (nothing switched);
# 81 the canonical colour did not serve and the recorded other one took over (loud);
# 82 another release/recovery holds the lock; 83 no tree matches the pinned recovery inputs;
# 84 the canonical colour serves its release but reports degraded - kept, never switched,
# because a colour switch cannot repair a dependency both colours share.
# Any failure after the switch switches back (devices first, then HTTP; the old colour is
# still up during the drain; after it, the old colour is started again first). Any failure
# before the switch stops the idle colour and restores the trees. The database is never
# downgraded.
#
# Env overrides for tests: PAGENTOS_BASE (default /opt/pagentos), PAGENTOS_EDGE_DIR
# (default /mnt/pagentos-data/edge), PAGENTOS_HEALTH_URL, PAGENTOS_DRAIN_S,
# PAGENTOS_HANDOFF_WAIT_S, PAGENTOS_IMAGE_REPO, PAGENTOS_INTERRUPT_AT (a controlled crash
# for the recovery proof: after_idle_up | after_device_handoff | after_switch | after_drain).
set -eu

first_arg=${1:?SHA, or --rollback / --reconcile}
case "$first_arg" in
    --rollback|--reconcile) sha="(none)"; mode="$first_arg";;
    *) sha="$first_arg"; mode=${2:-release};;
esac
base=${PAGENTOS_BASE:-/opt/pagentos}
lock_file="$base/.bluegreen-operation.lock"
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
handoff_wait_s=${PAGENTOS_HANDOFF_WAIT_S:-30}
image_repo=${PAGENTOS_IMAGE_REPO:-pagentos/cloud-core}
legacy_container=${PAGENTOS_API_CONTAINER:-pagentos-prod-api}
interrupt_at=${PAGENTOS_INTERRUPT_AT:-}
recovery_bundle=${PAGENTOS_RECOVERY_BUNDLE:-}
recovery_input_tree=""

# Release, rollback and periodic recovery inspect and mutate the same markers, colours and
# edge upstream. They must be one serial operation. A periodic reconcile that overlaps a
# legitimate release would otherwise classify its idle colour as an interrupted candidate
# and stop it. The production host's util-linux `flock` holds the kernel lock on fd 9 for
# this shell's lifetime; there is no create/write ownership window and SIGKILL releases it.
exec 9>"$lock_file"
if ! flock -n 9; then
    echo "another blue/green release or recovery operation is running; retry later" >&2
    exit 82
fi

# The root timer may use only deployment inputs approved with its own pinned bundle. The
# application tree remains the source of versioned code for normal releases, but it cannot
# replace Compose mounts/commands or nginx policy underneath the monitor that judges it.
if [ "$mode" = "--reconcile" ] && [ -n "$recovery_bundle" ]; then
    for candidate_tree in "$cur" "$prev"; do
        if cmp -s "$candidate_tree/infra/docker/docker-compose.prod.yml" "$recovery_bundle/docker-compose.prod.yml" \
            && cmp -s "$candidate_tree/infra/docker/edge/nginx.conf" "$recovery_bundle/nginx.conf"; then
            recovery_input_tree="$candidate_tree"
            break
        fi
    done
    if [ -z "$recovery_input_tree" ]; then
        echo "no app/app.prev tree matches the pinned recovery Compose and nginx inputs" >&2
        exit 83
    fi
fi

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

env_value() { grep "^$1=" "$envf" 2>/dev/null | head -1 | sed 's/^[^=]*=//' || true; }

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
    # write_upstream HTTP_COLOUR DEVICES_COLOUR: the two lines the edge reads, atomically,
    # plus the active marker (= the HTTP-authoritative colour).
    local http=$1 devices=${2:-$1}
    mkdir -p "$edge_dir"
    {
        printf 'upstream pagentos_api { server api-%s:8001; }\n' "$http"
        printf 'upstream pagentos_devices { server api-%s:8001; }\n' "$devices"
    } > "$edge_dir/upstream.conf.next"
    mv "$edge_dir/upstream.conf.next" "$edge_dir/upstream.conf"
    printf '%s\n' "$http" > "$edge_dir/active.txt"
}

compose() {
    local input_tree="${recovery_input_tree:-$cur}"
    docker compose --profile bluegreen -f "$input_tree/infra/docker/docker-compose.prod.yml" --env-file "$envf" "$@"
}

in_container_health() {
    # in_container_health COLOUR: the colour's own health JSON, from inside the container
    # (the colours publish no port; only the edge does).
    compose exec -T "api-$1" python3 -c 'import sys, urllib.request; sys.stdout.write(urllib.request.urlopen("http://127.0.0.1:8001/v1/system/health", timeout=10).read().decode())'
}

service_running() {
    # service_running SERVICE: compose says the service (api-blue, api-green, edge) runs.
    compose ps --status running --services 2>/dev/null | grep -qx "$1"
}

sessions_of() {
    # sessions_of COLOUR: the device sessions the colour holds (health.checks.broker).
    local body
    body="$(in_container_health "$1" 2>/dev/null || true)"
    printf '%s' "$body" | grep -oE '"active_sessions":[[:space:]]*[0-9]+' | head -1 | grep -oE '[0-9]+$' || echo 0
}

post_loopback() {
    # post_loopback COLOUR PATH: a loopback-only POST from inside the colour's container.
    # First line "STATUS <http code>" (or "STATUS none" when nothing answered), then the body:
    # a colour released before M18.4 gap 1 has no drain route and answers 404/405, and the
    # caller must be able to tell that from a drained colour. One line of python (exec of
    # an escaped source), so the call is one line in every log and every fake.
    local code
    code='exec("import sys, urllib.request, urllib.error\nreq = urllib.request.Request(\"http://127.0.0.1:8001'"$2"'\", data=b\"{}\", method=\"POST\", headers={\"Content-Type\": \"application/json\"})\ntry:\n    r = urllib.request.urlopen(req, timeout=10)\n    sys.stdout.write(\"STATUS %d\\n%s\" % (r.status, r.read().decode()))\nexcept urllib.error.HTTPError as e:\n    sys.stdout.write(\"STATUS %d\\n\" % e.code)\nexcept Exception:\n    sys.stdout.write(\"STATUS none\\n\")")'
    compose exec -T "api-$1" python3 -c "$code" 2>/dev/null || echo "STATUS none"
}

drain_colour() { post_loopback "$1" /v1/devices/drain; }
undrain_colour() { post_loopback "$1" /v1/devices/undrain; }
status_of() { printf '%s\n' "$1" | head -1 | awk '{print $2}'; }

wait_for_sessions() {
    # wait_for_sessions COLOUR WANTED SECONDS: until the colour holds at least WANTED
    # device sessions; prints "N/WANTED after Xs"; returns 1 when it never got there.
    local colour=$1 wanted=$2 limit=$3 waited=0 have=0
    while :; do
        have="$(sessions_of "$colour")"
        if [ "$have" -ge "$wanted" ]; then echo "$have/$wanted after ${waited}s"; return 0; fi
        if [ "$waited" -ge "$limit" ]; then echo "$have/$wanted after ${waited}s"; return 1; fi
        sleep 1
        waited=$((waited + 1))
    done
}

handoff_legacy=0
handoff_devices() {
    # handoff_devices FROM TO: device authority FROM -> TO. The device upstream names TO,
    # the edge reloads, FROM hands its sessions over, and we wait until TO holds as many.
    # The HTTP upstream is left where it is (the caller moves it afterwards).
    # A FROM that has no drain route (a release older than M18.4 gap 1 answers 404) cannot
    # hand anything over: the switch falls back to the legacy shape, said out loud - the
    # device moves when FROM stops, with a presence gap of up to the drain window. That is
    # the one-time cost of the first release past this line, never a health override.
    local from=$1 to=$2 http had moved drained status
    http="$(active_colour)"
    had="$(sessions_of "$from")"
    undrain_colour "$to" >/dev/null
    write_upstream "$http" "$to"
    reload_edge || return 77
    drained="$(drain_colour "$from")"
    status="$(status_of "$drained")"
    case "$status" in
        200) ;;
        404|405)
            handoff_legacy=1
            echo "device handoff UNAVAILABLE: api-$from has no drain route (a release before M18.4 gap 1); LEGACY switch: the device moves when api-$from stops, a presence gap of up to ${drain_s}s is expected"
            return 0;;
        *)
            echo "device handoff FAILED: api-$from did not answer the drain (status ${status:-none})" >&2
            return 79;;
    esac
    if [ "$had" -gt 0 ]; then
        if moved="$(wait_for_sessions "$to" "$had" "$handoff_wait_s")"; then
            echo "device handoff: $moved device session(s) on api-$to"
        else
            echo "device handoff INCOMPLETE: $moved device session(s) on api-$to; the new colour would be authoritative for devices it cannot reach" >&2
            return 79
        fi
    else
        echo "device handoff: no device session on api-$from; nothing to move"
    fi
}

wait_for_colour() {
    # wait_for_colour COLOUR EXPECTED_SHA: up to ~120 s for exact healthy provenance.
    # The API deliberately returns HTTP 200 for degraded health, so transport success or
    # the mere presence of a status field proves neither health nor the running version.
    local colour=$1 expected_sha=$2 tries=0 body="" status="" actual_sha=""
    while [ "$tries" -lt 40 ]; do
        body="$(in_container_health "$colour" 2>/dev/null || true)"
        status="$(top_health_status "$body")"
        actual_sha="$(served_release "$body")"
        if [ "$status" = "ok" ] && [ "$actual_sha" = "$expected_sha" ]; then
            printf '%s' "$body"
            return 0
        fi
        tries=$((tries + 1))
        sleep "${PAGENTOS_WAIT_STEP_S:-3}"
    done
    return 1
}

serving_status_of() {
    # serving_status_of COLOUR EXPECTED_SHA: ONE probe. Prints the colour's top-level health
    # status when it answers AND names EXPECTED_SHA as its release, whatever that status
    # is; returns 1 when it does not answer, or answers as some other release.
    local body status
    body="$(in_container_health "$1" 2>/dev/null || true)"
    status="$(top_health_status "$body")"
    if [ -n "$status" ] && [ "$(served_release "$body")" = "$2" ]; then
        printf '%s' "$status"
        return 0
    fi
    return 1
}

install_edge_config() {
    # install_edge_config [TREE]: the tree's nginx.conf becomes the edge's (atomic copy into
    # the edge dir the container reads with -c). A missing tree file leaves the edge's alone.
    local src="${1:-$cur}/infra/docker/edge/nginx.conf"
    if [ -n "$recovery_input_tree" ]; then src="$recovery_bundle/nginx.conf"; fi
    if [ -f "$src" ]; then
        mkdir -p "$edge_dir"
        cp "$src" "$edge_dir/nginx.conf.next"
        mv "$edge_dir/nginx.conf.next" "$edge_dir/nginx.conf"
    fi
}

reload_edge() {
    local test_out
    if ! test_out="$(compose exec -T edge nginx -t -c /etc/nginx/edge/nginx.conf 2>&1)"; then
        echo "edge config test FAILED: $(printf '%s' "$test_out" | tail -3 | tr '\n' ' ')" >&2
        return 1
    fi
    # -c on the signal too: nginx finds the master's pid file through the configuration it
    # is given, and the edge's (pid /tmp/nginx.pid) is not the image default's. Without it
    # the reload looked for /run/nginx.pid and failed on the real host (run 4, 2026-09-07).
    compose exec -T edge nginx -s reload -c /etc/nginx/edge/nginx.conf
}

ensure_edge() {
    # ensure_edge: the edge container matches the tree's compose definition (compose
    # recreates it only when the definition changed - that is one short gap, said out
    # loud; an unchanged edge is left running) and runs the tree's nginx.conf.
    local out
    install_edge_config
    out="$(compose up -d --no-deps --wait edge 2>&1 || true)"
    if printf '%s' "$out" | grep -qi "recreat"; then
        echo "edge RECREATED: its compose definition changed (one gap while the new container took the socket)"
    fi
}

served_release() {
    # served_release BODY: the release sha a health body names, or empty.
    printf '%s' "$1" | grep -oE '"release":[[:space:]]*\{[^}]*' | grep -oE '"version":[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/' || true
}

top_health_status() {
    # The application emits top-level status first. Anchoring at the opening object keeps
    # a nested provider's "status":"ok" from masking top-level degraded health.
    printf '%s' "$1" | sed -nE 's/^[[:space:]]*\{[[:space:]]*"status"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p' | head -1
}

maybe_interrupt() {
    # maybe_interrupt POINT: the controlled crash for the recovery proof - SIGKILL skips
    # every trap, exactly like a host that lost power here. Test hook only.
    if [ -n "$interrupt_at" ] && [ "$interrupt_at" = "$1" ]; then
        echo "INTERRUPT: simulated crash at '$1' (PAGENTOS_INTERRUPT_AT)" >&2
        kill -9 $$
        sleep 5
    fi
}

# ---------------------------------------------------------------- preflight

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

# ---------------------------------------------------------------- reconcile

if [ "$mode" = "--reconcile" ]; then
    # After a crash or a reboot. Facts: the marker (the colour the edge was last pointed
    # at), RELEASE (the sha of the last COMPLETED promotion), the env file (which sha each
    # colour was released with), the containers, the colours' own health. Rule: the last
    # completed promotion is canonical; a colour running a sha that RELEASE does not name
    # is a half-promoted candidate and is drained and stopped, never made live by this
    # path (a promotion is re-run through the release, which verifies it). Only when the
    # canonical colour cannot come up does the other recorded colour take over, loudly
    # (exit 81), so the host still answers.
    cd "$base"
    if [ "$first_cutover" = "1" ]; then
        echo "reconcile: no active marker; no blue/green state to rebuild (legacy layout or first cutover pending)"
        exit 0
    fi
    tries=0
    until docker info >/dev/null 2>&1; do
        tries=$((tries + 1))
        if [ "$tries" -ge 30 ]; then echo "reconcile: docker does not answer; giving up" >&2; exit 80; fi
        sleep "${PAGENTOS_WAIT_STEP_S:-3}"
    done
    marker="$active"
    release_sha="$(cat "$base/RELEASE" 2>/dev/null | tr -d '[:space:]' || true)"
    lkg_sha="$(cat "$base/LAST_KNOWN_GOOD" 2>/dev/null | tr -d '[:space:]' || true)"
    sha_blue="$(env_value PAGENTOS_RELEASE_BLUE)"
    sha_green="$(env_value PAGENTOS_RELEASE_GREEN)"
    running=""
    for c in blue green; do
        if service_running "api-$c"; then running="$running api-$c"; fi
    done
    echo "reconcile: marker=$marker RELEASE=${release_sha:-none} LKG=${lkg_sha:-none} blue=${sha_blue:-none} green=${sha_green:-none} running=[${running# }]"
    canonical="$marker"
    marker_sha="$(env_value "PAGENTOS_RELEASE_$(upper "$marker")")"
    if [ -n "$release_sha" ] && [ "$marker_sha" != "$release_sha" ]; then
        other="$(other_colour "$marker")"
        other_sha="$(env_value "PAGENTOS_RELEASE_$(upper "$other")")"
        if [ "$other_sha" = "$release_sha" ]; then
            canonical="$other"
            echo "reconcile: the marker names api-$marker (${marker_sha:-no sha}) but the last COMPLETED promotion is $release_sha on api-$other: the promotion of api-$marker was interrupted; api-$other is canonical"
        else
            echo "reconcile: RELEASE ($release_sha) matches neither colour's recorded sha; the marker colour api-$marker (${marker_sha:-no sha}) stays canonical"
        fi
    fi
    other="$(other_colour "$canonical")"
    canonical_sha="$(env_value "PAGENTOS_RELEASE_$(upper "$canonical")")"
    other_sha="$(env_value "PAGENTOS_RELEASE_$(upper "$other")")"
    emergency=0
    degraded=""
    if ! service_running "api-$canonical"; then
        if [ -n "$canonical_sha" ]; then
            echo "reconcile: api-$canonical is not running; starting it from its recorded image ($canonical_sha)"
            compose up -d --no-deps --wait "api-$canonical" 2>&1 | tail -1 || true
        else
            echo "reconcile: api-$canonical is not running and has no recorded release"
        fi
    fi
    body=""
    if [ -n "$canonical_sha" ] && body="$(wait_for_colour "$canonical" "$canonical_sha")"; then
        echo "reconcile: api-$canonical healthy (release $(served_release "$body")) -> canonical"
    elif [ -n "$canonical_sha" ] && degraded="$(serving_status_of "$canonical" "$canonical_sha")"; then
        # It ANSWERS, as its own recorded release, and reports itself not ok. Most checks
        # behind that status (db, redis, object store, temporal, the providers) are shared
        # with the other colour, so a switch cannot repair them - and a dependency that
        # recovers while the other colour starts would make an OLDER build live and rewrite
        # RELEASE to it. The serving colour stays canonical; the state is reported loudly
        # at the end (84). Only a colour that does not serve at all is replaced.
        echo "reconcile: api-$canonical serves $canonical_sha but reports '$degraded'; it stays canonical (a colour switch cannot repair a shared dependency)" >&2
    else
        degraded=""
        if [ -n "$other_sha" ]; then
            echo "reconcile: api-$canonical will not come up; api-$other ($other_sha) is the only recorded alternative - taking over LOUDLY (not a completed promotion)" >&2
            other_was_running=0
            if service_running "api-$other"; then other_was_running=1; fi
            compose up -d --no-deps --wait "api-$other" 2>&1 | tail -1 || true
            if body="$(wait_for_colour "$other" "$other_sha")"; then
                canonical="$other"; other="$(other_colour "$canonical")"; canonical_sha="$other_sha"; other_sha="$(env_value "PAGENTOS_RELEASE_$(upper "$other")")"
                emergency=1
            else
                # This reconcile started it and it never became healthy: it does not stay
                # up beside the canonical colour (a second routine clock, a second worker).
                if [ "$other_was_running" = "0" ]; then
                    echo "reconcile: api-$other did not become healthy either; stopping it again (it was not running before this reconcile)" >&2
                    compose stop "api-$other" 2>&1 | tail -1 || true
                fi
                echo "reconcile: NEITHER colour answers; nothing is switched; operator attention required" >&2
                exit 80
            fi
        else
            echo "reconcile: api-$canonical will not come up and no other release is recorded; operator attention required" >&2
            exit 80
        fi
    fi
    undrain_colour "$canonical" >/dev/null
    write_upstream "$canonical" "$canonical"
    # the edge runs the CANONICAL tree's nginx.conf (app.prev when the crash had swapped it in)
    if [ -n "$canonical_sha" ] && [ "$(cat "$prev/RELEASE" 2>/dev/null | tr -d '[:space:]')" = "$canonical_sha" ] && [ "$(cat "$cur/RELEASE" 2>/dev/null | tr -d '[:space:]')" != "$canonical_sha" ]; then
        install_edge_config "$prev"
    else
        install_edge_config
    fi
    if service_running edge; then
        reload_edge || { echo "reconcile: edge reload FAILED" >&2; exit 77; }
    else
        echo "reconcile: the edge is not running; starting it"
        compose up -d --no-deps --wait edge 2>&1 | tail -1
    fi
    echo "reconcile: edge -> api-$canonical (both upstreams)"
    if service_running "api-$other"; then
        echo "reconcile: api-$other (${other_sha:-no sha}) runs without a completed promotion: a half-promoted candidate; draining and stopping it (it does not become live)"
        drain_colour "$other" >/dev/null
        sleep "${PAGENTOS_WAIT_STEP_S:-3}"
        compose stop "api-$other" 2>&1 | tail -1 || true
    fi
    if [ -n "$canonical_sha" ]; then
        echo "$canonical_sha" > "$base/RELEASE"
    fi
    # The tree: an interrupted release had already swapped app.next in. The canonical
    # release's tree (app.prev, named by its own RELEASE file) comes back; the candidate's
    # is kept aside as app.interrupted for inspection - the image itself is retained anyway.
    if [ -n "$canonical_sha" ] && [ -d "$prev" ] && [ "$(cat "$cur/RELEASE" 2>/dev/null | tr -d '[:space:]')" != "$canonical_sha" ] && [ "$(cat "$prev/RELEASE" 2>/dev/null | tr -d '[:space:]')" = "$canonical_sha" ]; then
        echo "reconcile: tree: app is the interrupted candidate's ($(cat "$cur/RELEASE" 2>/dev/null)); restoring the canonical tree from app.prev (candidate kept as app.interrupted)"
        rm -rf "$base/app.interrupted"
        mv "$cur" "$base/app.interrupted"
        mv "$prev" "$cur"
    fi
    date -u +%Y-%m-%dT%H:%M:%SZ > "$base/LAST_RECONCILE"
    if [ "$emergency" = "1" ]; then
        echo "RECONCILE EMERGENCY: api-$canonical ($canonical_sha) is live because the canonical release could not start; not a completed promotion - review required" >&2
        exit 81
    fi
    if [ -n "$degraded" ]; then
        echo "RECONCILE DEGRADED: api-$canonical ($canonical_sha) stays canonical but reports '$degraded'; no colour was switched - operator attention required" >&2
        exit 84
    fi
    echo "RECONCILE OK: api-$canonical is canonical (release ${canonical_sha:-unknown}); markers, upstreams and containers agree"
    exit 0
fi

# ----------------------------------------------------------------- rollback

do_rollback() {
    # Switch back to the colour that was active before this release started. Devices
    # first (the active colour takes them again, the idle colour drains), then HTTP.
    # First step: leave the tree. compose() uses absolute paths, so $base is a safe home;
    # removing the directory the script stands in made every compose call fail on the
    # real host (getwd) and left the edge unswitched.
    cd "$base" || true
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
    compose up -d --no-deps --wait "api-$active" 2>&1 | tail -1 || true
    local had
    had="$(sessions_of "$idle")"
    undrain_colour "$active" >/dev/null
    write_upstream "$active" "$active"
    install_edge_config
    if reload_edge; then echo "ROLLBACK: edge -> api-$active (both upstreams)" >&2; else echo "ROLLBACK: edge reload FAILED; the edge may still point at api-$idle, which is left RUNNING" >&2; return 0; fi
    drain_colour "$idle" >/dev/null
    if [ "$had" -gt 0 ]; then
        echo "ROLLBACK: device sessions returning to api-$active: $(wait_for_sessions "$active" "$had" "$handoff_wait_s" || true)" >&2
    fi
    compose stop "api-$idle" 2>&1 | tail -1 || true
    echo "ROLLBACK: api-$idle stopped" >&2
}

if [ "$mode" = "--rollback" ]; then
    if [ "$first_cutover" = "1" ]; then
        echo "nothing to roll back: no blue/green release has happened yet" >&2
        exit 65
    fi
    cd "$base"
    prev_colour="$(other_colour "$active")"
    PREV="$(upper "$prev_colour")"
    # The colour we return to runs the sha the env file recorded for it when it was last
    # released; that sha becomes RELEASE, and the sha we leave becomes LAST_KNOWN_GOOD
    # (the bookkeeping must name what is actually running, not what a file remembers).
    target_sha="$(env_value "PAGENTOS_RELEASE_$PREV")"
    leaving_sha="$(cat "$base/RELEASE" 2>/dev/null || true)"
    echo "rolling back: edge -> api-$prev_colour (${target_sha:-sha unknown}; leaving ${leaving_sha:-unknown})"
    compose up -d --no-deps --wait "api-$prev_colour"
    body="$(wait_for_colour "$prev_colour" "$target_sha")" || { echo "api-$prev_colour did not become healthy at ${target_sha:-unknown}; the edge stays on api-$active" >&2; exit 75; }
    install_edge_config
    # devices first, then HTTP - the same handoff a release does
    handoff_devices "$active" "$prev_colour" || { rc=$?; echo "device handoff to api-$prev_colour failed ($rc); restoring api-$active" >&2; undrain_colour "$active" >/dev/null; write_upstream "$active" "$active"; reload_edge || true; drain_colour "$prev_colour" >/dev/null; compose stop "api-$prev_colour" >/dev/null 2>&1 || true; exit "$rc"; }
    write_upstream "$prev_colour" "$prev_colour"
    reload_edge
    compose stop "api-$active" >/dev/null 2>&1 || true
    [ -n "$target_sha" ] && echo "$target_sha" > "$base/RELEASE"
    [ -n "$leaving_sha" ] && echo "$leaving_sha" > "$base/LAST_KNOWN_GOOD"
    [ -n "$leaving_sha" ] && upsert_env "PAGENTOS_LAST_KNOWN_GOOD" "$leaving_sha"
    echo "ROLLBACK OK: api-$prev_colour is active"
    exit 0
fi

# ------------------------------------------------------------------ release

if [ ! -d "$next" ]; then
    echo "$next is missing; the driver extracts the tree there first" >&2
    exit 65
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
echo "preflight: tree $sha extracted, compose valid, env posture $posture, active colour $cutover_note$active, idle $idle, drain ${drain_s}s, device handoff wait ${handoff_wait_s}s"
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
            cd "$base" || true
            if [ "$first_cutover" != "1" ]; then
                # the device handoff may have begun: the active colour takes devices again
                undrain_colour "$active" >/dev/null 2>&1 || true
                write_upstream "$active" "$active"
                [ -d "$prev" ] && install_edge_config "$prev"
                reload_edge >/dev/null 2>&1 || true
                drain_colour "$idle" >/dev/null 2>&1 || true
            fi
            compose stop "api-$idle" >/dev/null 2>&1 || true
            if [ -d "$prev" ]; then rm -rf "$cur"; mv "$prev" "$cur"; fi
        fi
    fi
}
trap on_exit EXIT

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

# ADR-0122: a safety point before any schema change. The pinned backup (install-backup.sh)
# takes a pre-migration snapshot; if it cannot, the release stops HERE - before the
# migration, with the active colour untouched and the trees restored by on_exit.
backup_bin=${PAGENTOS_BACKUP_BIN:-/opt/pagentos-backup}/backup-cloud-core.sh
if [ -f "$backup_bin" ]; then
    echo "pre-migration backup..."
    backup_log="$base/.pre-migration-backup.log"
    if ! bash "$backup_bin" --kind pre-migration --label "release-$(printf '%s' "$sha" | cut -c1-12)" > "$backup_log" 2>&1; then
        tail -5 "$backup_log" >&2
        echo "pre-migration backup FAILED; the release stops before any migration" >&2
        exit 74
    fi
    tail -1 "$backup_log"
else
    echo "WARNING: no backup tooling at $backup_bin; migrating WITHOUT a safety point (install-backup.sh)" >&2
fi

echo "applying migrations (expand-only)..."
compose run --rm --no-deps --entrypoint uv "api-$idle" run alembic upgrade head 2>&1 | tail -2

echo "starting the idle colour api-$idle on $sha (api-$active keeps serving)..."
compose up -d --no-deps --wait "api-$idle" 2>&1 | tail -2
cd "$base"
maybe_interrupt after_idle_up

body="$(wait_for_colour "$idle" "$sha")" || { echo "api-$idle never answered healthy at $sha" >&2; exit 75; }
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
if [ "$(served_release "$body")" != "$sha" ]; then
    echo "api-$idle reports release '$(served_release "$body")', expected '$sha' (the version model is not wired)" >&2
    exit 76
fi
echo "api-$idle reports release $sha"

# ------------------------------------------------------------------ switch

if [ "$first_cutover" = "1" ]; then
    echo "first cutover: stopping the legacy $legacy_container and starting the edge (one last gap)"
    docker stop "$legacy_container" >/dev/null 2>&1 || true
    install_edge_config
    write_upstream "$idle" "$idle"
    compose up -d --no-deps --wait edge 2>&1 | tail -2
else
    # 0. The edge runs this tree's configuration (a copy + reload, or a recreation when
    #    its compose definition changed - before the switch, while api-$active serves).
    ensure_edge
    reload_edge || { echo "edge reload FAILED (new configuration)" >&2; exit 77; }
    # 1. Device authority moves first: the idle colour must hold the device sessions
    #    before it is given HTTP (and with it, device commands).
    handoff_devices "$active" "$idle" || exit $?
    maybe_interrupt after_device_handoff
    # 2. HTTP authority follows.
    write_upstream "$idle" "$idle"
    reload_edge || { echo "edge reload FAILED" >&2; exit 77; }
fi
switched=1
echo "edge -> api-$idle"
maybe_interrupt after_switch

# `nginx -s reload` only SENDS the signal: the master parses the new configuration and
# starts new workers a moment later, and a probe fired in that moment still reaches the
# old colour (run 6, 2026-09-07: "through the edge the release is c109302" one second after
# the switch, then a needless rollback). Bounded wait for the edge to answer with the sha.
tries=0
edge_release=""
edge_status=""
while [ "$tries" -lt "${PAGENTOS_EDGE_SETTLE_TRIES:-20}" ]; do
    health="$(curl -fsS "$health_url" 2>/dev/null || true)"
    edge_release="$(served_release "$health")"
    edge_status="$(top_health_status "$health")"
    if [ "$edge_status" = "ok" ] && [ "$edge_release" = "$sha" ]; then break; fi
    tries=$((tries + 1))
    sleep "${PAGENTOS_EDGE_SETTLE_STEP_S:-0.5}"
done
if [ "$edge_status" != "ok" ] || [ "$edge_release" != "$sha" ]; then
    echo "through the edge health is '${edge_status:-absent}' and release is '${edge_release:-absent}', expected ok / '$sha' (after $tries probes)" >&2
    exit 76
fi
echo "health through the edge: release $sha (settled after $tries retr$( [ "$tries" = "1" ] && echo y || echo ies))"

if [ "$first_cutover" != "1" ]; then
    if [ "$handoff_legacy" = "1" ]; then
        echo "draining api-$active for ${drain_s}s (in-flight requests; the device still sits on it and moves when it stops - legacy switch)..."
    else
        echo "draining api-$active for ${drain_s}s (in-flight requests; device sessions already moved)..."
    fi
    sleep "$drain_s"
    compose stop "api-$active" 2>&1 | tail -1
    echo "api-$active stopped"
    maybe_interrupt after_drain
fi

echo "$sha" > "$base/RELEASE"
[ -n "$previous_sha" ] && echo "$previous_sha" > "$base/LAST_KNOWN_GOOD"
trap - EXIT
# The recovery timer runs only with the Compose file and edge policy its owner-approved
# bundle pinned (exit 83 otherwise). A release that changes either leaves that bundle behind:
# the timer reconciles with the PREVIOUS tree's inputs while app.prev still matches, and
# refuses every run after the next such release. Said now, loudly, and left as a marker a
# health reader can see - never discovered later as a silent 83 in the journal.
recovery_root=${PAGENTOS_RECOVERY_ROOT:-/opt/pagentos-recovery}
if [ -f "$recovery_root/docker-compose.prod.yml" ]; then
    if cmp -s "$cur/infra/docker/docker-compose.prod.yml" "$recovery_root/docker-compose.prod.yml" \
        && cmp -s "$cur/infra/docker/edge/nginx.conf" "$recovery_root/nginx.conf"; then
        rm -f "$base/RECOVERY_BUNDLE_STALE"
    else
        date -u +%Y-%m-%dT%H:%M:%SZ > "$base/RECOVERY_BUNDLE_STALE"
        echo "RECOVERY BUNDLE STALE: $sha changed the Compose file or the edge policy the recovery timer pinned; it reconciles with the previous tree's inputs until the owner re-runs install-recovery-supervisor.sh $sha, and refuses (83) after the next such release" >&2
    fi
fi
echo "RELEASE OK: $sha is running as api-$idle behind the edge (previous ${previous_sha:-none} kept as last known good)"
