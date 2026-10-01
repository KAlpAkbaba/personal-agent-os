#!/usr/bin/env bash
# A read-only snapshot of the Cloud Core host, as ONE JSON document on stdout.
#
# It is what the fake hosts of the test suites are built from (scripts/tests/fixtures/
# host-snapshot.json): three defects of 2026-10-01 were green on a fake and red on this host
# because the fake did not model the serving colour, how long the operation lock is held,
# or a column's width (QUALIFICATION 38.12, 38.17, 38.15). The lead collects it from the
# home PC with scripts/cloud/collect-host-snapshot.ps1 (ssh, this file on stdin of 'bash -s').
#
# It READS and nothing else, so it is safe to run a hundred times:
#   docker ps -a                              names and states
#   the active-colour marker, RELEASE, LAST_KNOWN_GOOD, the recovery pin
#                                             a colour / 40 hex, or 'missing' / 'invalid';
#                                             a marker's content is never printed otherwise
#   uname -r, whether reboot-required exists
#   systemctl list-timers / is-active         the pagentos-* timers
#   flock -n <the operation lock> true        sixty one-second samples of "is it held": the
#                                             count and the longest run. A missing lock file
#                                             is NOT probed: flock would create it
#   information_schema.columns                table / column / data_type / max length, in a
#                                             session that is read-only on the server
# No row of any application table, no secret, no environment value, no path under the base
# beyond the markers above. scripts/tests/host-snapshot.tests.ps1 records every command this
# file runs and fails on anything outside that list - so there is no '<' or '>' redirection
# here either (bash's trace does not show one): files are read with cat, status goes to
# stderr, and only the finished document is printed.
#
# Exit codes: 0 the document was printed; 3 a read failed - NOTHING is printed on stdout.
# Env overrides (tests): PAGENTOS_BASE, PAGENTOS_EDGE_DIR, PAGENTOS_RECOVERY_ROOT,
# PAGENTOS_REBOOT_REQUIRED, PAGENTOS_PG_CONTAINER, PAGENTOS_PG_USER, PAGENTOS_PG_DB,
# PAGENTOS_LOCK_SAMPLES, PAGENTOS_LOCK_STEP_S.
set -eu -o pipefail

base=${PAGENTOS_BASE:-/opt/pagentos}
edge_dir=${PAGENTOS_EDGE_DIR:-/mnt/pagentos-data/edge}
recovery_root=${PAGENTOS_RECOVERY_ROOT:-/opt/pagentos-recovery}
reboot_required=${PAGENTOS_REBOOT_REQUIRED:-/var/run/reboot-required}
pg_container=${PAGENTOS_PG_CONTAINER:-pagentos-prod-postgres}
pg_user=${PAGENTOS_PG_USER:-pagentos}
pg_db=${PAGENTOS_PG_DB:-pagentos_prod}
lock_samples=${PAGENTOS_LOCK_SAMPLES:-60}
lock_step=${PAGENTOS_LOCK_STEP_S:-1}
lock_file="$base/.bluegreen-operation.lock"
nl=$'
'

say() { echo "snapshot: $*" >&2; }
fail() { echo "SNAPSHOT FAILED: $*" >&2; exit 3; }

json_str() { # $1 as a JSON string
    local s=${1//\\/\\\\}
    s=${s//\"/\\\"}
    printf '"%s"' "$s"
}

marker_sha() { # $1 = a marker file; says its 40 hex, 'missing' or 'invalid'
    local v
    if [ ! -f "$1" ]; then echo missing; return 0; fi
    v=$(cat "$1") || fail "cannot read $1"
    v=${v//[[:space:]]/}
    if [[ "$v" =~ ^[0-9a-f]{40}$ ]]; then echo "$v"; else echo invalid; fi
}

read_colour() { # the edge's active marker: blue, green, missing or invalid
    local v
    if [ ! -f "$edge_dir/active.txt" ]; then echo missing; return 0; fi
    v=$(cat "$edge_dir/active.txt") || fail "cannot read $edge_dir/active.txt"
    v=${v//[[:space:]]/}
    case "${v,,}" in blue|green) echo "${v,,}";; *) echo invalid;; esac
}

lock_present=false; lock_taken=0; lock_held=0; lock_longest=0
sample_lock() {
    # The minute reconcile takes this lock for a few seconds; a release holds it for minutes.
    # '-n ... true' asks and lets go at once; it never waits and runs nothing under the lock.
    local i=0 run=0
    if [ ! -e "$lock_file" ]; then return 0; fi
    lock_present=true
    while [ "$i" -lt "$lock_samples" ]; do
        i=$((i + 1))
        if flock -n "$lock_file" true 2>/dev/null; then run=0
        else
            lock_held=$((lock_held + 1)); run=$((run + 1))
            if [ "$run" -gt "$lock_longest" ]; then lock_longest=$run; fi
        fi
        lock_taken=$i
        if [ "$lock_step" != 0 ] && [ "$i" -lt "$lock_samples" ]; then sleep "$lock_step"; fi
    done
}

[[ "$lock_samples" =~ ^[0-9]+$ && "$lock_step" =~ ^[0-9]+$ ]] || fail "PAGENTOS_LOCK_SAMPLES / PAGENTOS_LOCK_STEP_S must be whole numbers"

# --- the host ----------------------------------------------------------------------------
say "kernel, reboot-required, markers"
kernel=$(uname -r) || fail "uname -r"
if [ -e "$reboot_required" ]; then reboot=true; else reboot=false; fi
colour=$(read_colour)
release=$(marker_sha "$base/RELEASE")
lkg=$(marker_sha "$base/LAST_KNOWN_GOOD")
pin=$(marker_sha "$recovery_root/APPROVED_SHA")

say "containers (docker ps -a)"
listing=$(docker ps -a --format '{{.Names}}|{{.State}}') || fail "docker ps"
containers=""
while IFS='|' read -r name state; do
    [ -n "$name" ] || continue
    [[ "$name" =~ ^[A-Za-z0-9_.-]+$ && "$state" =~ ^[a-z]+$ ]] || fail "an unexpected docker ps line"
    containers="$containers${containers:+,$nl}    {\"name\": $(json_str "$name"), \"state\": $(json_str "$state")}"
done <<< "$listing"
[ -n "$containers" ] || fail "docker ps listed no container"

say "timers (systemctl list-timers 'pagentos-*')"
listing=$(systemctl list-timers 'pagentos-*' --all --no-legend --no-pager) || fail "systemctl list-timers"
timers=""
while read -r line; do
    for word in $line; do
        [[ "$word" =~ ^pagentos-[A-Za-z0-9_.@-]+\.timer$ ]] || continue
        active=$(systemctl is-active "$word" 2>/dev/null) || true
        [[ "$active" =~ ^[a-z]+$ ]] || active=unknown
        timers="$timers${timers:+,$nl}    {\"name\": $(json_str "$word"), \"active\": $(json_str "$active")}"
    done
done <<< "$listing"

say "the operation lock: $lock_samples samples, ${lock_step}s apart"
sample_lock

say "column shapes (information_schema.columns, read-only session)"
statement="SELECT table_name, column_name, data_type, character_maximum_length FROM information_schema.columns WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
listing=$(docker exec -e "PGOPTIONS=-c default_transaction_read_only=on" "$pg_container" psql -U "$pg_user" -d "$pg_db" -Atc "$statement") || fail "psql information_schema.columns"
columns=""
while IFS='|' read -r table column data_type length; do
    [ -n "$table" ] || continue
    [[ "$table" =~ ^[A-Za-z_][A-Za-z0-9_]*$ && "$column" =~ ^[A-Za-z_][A-Za-z0-9_]*$ && "$data_type" =~ ^[A-Za-z][A-Za-z\ -]*$ && "$length" =~ ^[0-9]*$ ]] || fail "an unexpected information_schema line"
    columns="$columns${columns:+,$nl}    {\"table\": $(json_str "$table"), \"column\": $(json_str "$column"), \"data_type\": $(json_str "$data_type"), \"character_maximum_length\": ${length:-null}}"
done <<< "$listing"
[ -n "$columns" ] || fail "information_schema listed no column"

# --- the document, printed once and whole ------------------------------------------------
printf '{\n  "schema_version": 1,\n  "host": {"kernel": %s, "reboot_required": %s},\n  "serving_colour": %s,\n' \
    "$(json_str "$kernel")" "$reboot" "$(json_str "$colour")"
printf '  "markers": {"release": %s, "last_known_good": %s, "recovery_pin": %s},\n' \
    "$(json_str "$release")" "$(json_str "$lkg")" "$(json_str "$pin")"
printf '  "containers": [\n%s\n  ],\n  "timers": [%s\n  ],\n' "$containers" "${timers:+$nl$timers}"
printf '  "operation_lock": {"present": %s, "samples": %s, "interval_s": %s, "held": %s, "longest_run": %s},\n' \
    "$lock_present" "$lock_taken" "$lock_step" "$lock_held" "$lock_longest"
printf '  "columns": [\n%s\n  ]\n}\n' "$columns"
say "done"
