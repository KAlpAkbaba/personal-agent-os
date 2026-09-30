#!/usr/bin/env bash
# The Cloud Core maintenance window as one command (ADR-0223, steps 1-11).
#
#   maintenance-reboot.sh --preflight            steps 1-5 as named checks; exit 0 only when
#                                                all hold; changes NOTHING
#   maintenance-reboot.sh --preflight --run      the preflight, then steps 6-10: backup, stop
#                                                the reconcile timer, remove the never-started
#                                                leftover container, apt upgrade, wait for the
#                                                containers, write the marker, reboot.
#                                                --run alone is refused (64).
#   maintenance-reboot.sh --verify               step 11 after boot: kernel changed,
#                                                reboot-required gone, containers, reconcile
#                                                said OK, health ok; measures downtime from the
#                                                marker to the first good probe, writes
#                                                LAST_MAINTENANCE.json, removes the marker.
#
# Exit codes: 0 ok; 10 a preflight check failed; 11 a window step failed; 20 --verify found no
# maintenance marker; 21 a --verify check failed (the marker stays, so --verify can be re-run);
# 64 usage. Every check prints one line: "ok|FAIL <name>: <what was read> ...".
# Step 3 (no team cycle, no owner mid-task) is the lead's and lives on the home PC: the script
# can only see a release in flight (the blue/green operation lock) and says so.
# Step 13 (the report) is the lead's; LAST_MAINTENANCE.json is its host-side input.
#
# House style: set -eu -o pipefail; status lines to stderr; output is captured, never piped
# into a reader that truncates, so a failing command's status is its own.
# Env overrides (tests): PAGENTOS_BASE, PAGENTOS_BACKUP_ROOT, PAGENTOS_RECOVERY_ROOT,
# PAGENTOS_HEALTH_URL, PAGENTOS_REBOOT_REQUIRED, PAGENTOS_DISK_MAX_PCT, PAGENTOS_BACKUP_MAX_AGE_S,
# PAGENTOS_WAIT_TRIES / PAGENTOS_WAIT_STEP_S (bounded waits), PAGENTOS_BACKUP_SCRIPT.
set -eu -o pipefail

base=${PAGENTOS_BASE:-/opt/pagentos}
backup_root=${PAGENTOS_BACKUP_ROOT:-/var/lib/pagentos-backup}
recovery_root=${PAGENTOS_RECOVERY_ROOT:-/opt/pagentos-recovery}
health_url=${PAGENTOS_HEALTH_URL:-http://127.0.0.1:8001/v1/system/health}
reboot_required=${PAGENTOS_REBOOT_REQUIRED:-/var/run/reboot-required}
disk_max=${PAGENTOS_DISK_MAX_PCT:-80}
backup_max_age=${PAGENTOS_BACKUP_MAX_AGE_S:-86400}
wait_tries=${PAGENTOS_WAIT_TRIES:-60}
wait_step=${PAGENTOS_WAIT_STEP_S:-5}
backup_script=${PAGENTOS_BACKUP_SCRIPT:-$base/app/scripts/cloud/backup-cloud-core.sh}
marker="$base/MAINTENANCE_MARKER"
record="$base/LAST_MAINTENANCE.json"
timer=pagentos-bluegreen-reconcile.timer
reconcile_service=pagentos-bluegreen-reconcile.service
leftover=pagentos-prod-api

say() { echo "$*" >&2; }
pre_failed=0
check_ok() { say "ok   $1: $2"; }
check_fail() { say "FAIL $1: $2"; pre_failed=1; }

do_preflight=0; do_run=0; do_verify=0
for a in "$@"; do
    case "$a" in
        --preflight) do_preflight=1;;
        --run) do_run=1;;
        --verify) do_verify=1;;
        *) say "usage: $0 --preflight [--run] | --verify"; exit 64;;
    esac
done
if [ "$do_run" = 1 ] && [ "$do_preflight" = 0 ]; then
    say "--run is refused without --preflight in the same invocation"; exit 64
fi
if [ "$do_preflight$do_run$do_verify" = "000" ] || { [ "$do_verify" = 1 ] && [ "$do_preflight$do_run" != "00" ]; }; then
    say "usage: $0 --preflight [--run] | --verify"; exit 64
fi

# --- helpers ---------------------------------------------------------------------------
health_body() { curl -fsS --max-time 10 "$health_url" 2>/dev/null; }
health_good() { # $1 = body
    case "$1" in *'"status":"ok"'*) ;; *'"status": "ok"'*) ;; *) return 1;; esac
    case "$1" in *'"failing_checks":""'*|*'"failing_checks": ""'*|*'"failing_checks":[]'*|*'"failing_checks": []'*) return 0;; esac
    case "$1" in *failing_checks*) return 1;; esac
    return 0
}
sha_of() { tr -d '[:space:]' < "$1" 2>/dev/null || true; }
is_sha() { printf '%s' "$1" | grep -Eq '^[0-9a-f]{40}$'; }

# --- step 1-5 --------------------------------------------------------------------------
preflight() {
    pre_failed=0
    local body release lkg pin
    # 1. health ok, failing_checks empty (read: $health_url)
    if body=$(health_body) && health_good "$body"; then check_ok health "read $health_url: status ok, failing_checks empty"
    else check_fail health "read $health_url: unreachable, status not ok or failing_checks set"; fi
    # 1. RELEASE / LAST_KNOWN_GOOD / recovery pin agree
    release=$(sha_of "$base/RELEASE"); lkg=$(sha_of "$base/LAST_KNOWN_GOOD"); pin=$(sha_of "$recovery_root/APPROVED_SHA")
    if is_sha "$release" && is_sha "$lkg"; then check_ok release "read $base/RELEASE and $base/LAST_KNOWN_GOOD: both 40 hex"
    else check_fail release "read $base/RELEASE and $base/LAST_KNOWN_GOOD: not both 40 hex"; fi
    if is_sha "$pin" && [ "$pin" = "$release" ]; then check_ok pin "read $recovery_root/APPROVED_SHA: equals RELEASE"
    else check_fail pin "read $recovery_root/APPROVED_SHA: '$pin' is not RELEASE '$release'"; fi
    # 2. backup younger than 24 h
    local fin fin_s now_s
    fin=$(sed -n 's/.*"finished_at": *"\([^"]*\)".*/\1/p' "$backup_root/LAST_BACKUP.json" 2>/dev/null || true)
    now_s=$(date +%s)
    if [ -n "$fin" ] && fin_s=$(date -d "$fin" +%s 2>/dev/null) && [ $((now_s - fin_s)) -lt "$backup_max_age" ]; then
        check_ok backup-age "read $backup_root/LAST_BACKUP.json: finished_at $fin, $(( (now_s - fin_s) / 60 )) min ago"
    else check_fail backup-age "read $backup_root/LAST_BACKUP.json: finished_at '${fin:-missing}' is not within ${backup_max_age}s"; fi
    local failures=""
    for f in "$backup_root"/failures/*.json; do [ -e "$f" ] && failures="$failures $f"; done
    if [ -z "$failures" ]; then check_ok failure-marker "read $backup_root/failures/: none"
    else check_fail failure-marker "read $backup_root/failures/:$failures"; fi
    # 3. a release in flight (the blue/green operation lock); the team cycle is the lead's check
    local lockf="$base/.bluegreen-operation.lock"
    if [ ! -e "$lockf" ] || flock -n "$lockf" true 2>/dev/null; then check_ok no-release "read $lockf: not held (team cycle / owner mid-task: the lead's check, home PC)"
    else check_fail no-release "read $lockf: held, a release or recovery is running"; fi
    # 4. nothing removed by the upgrade; docker-ce not held; disk under the limit
    local sim held pct
    if sim=$(apt-get -s upgrade 2>&1); then
        if printf '%s\n' "$sim" | grep -q '^Remv '; then check_fail apt-simulate "read apt-get -s upgrade: it removes a package"
        else check_ok apt-simulate "read apt-get -s upgrade: no removal"; fi
    else check_fail apt-simulate "read apt-get -s upgrade: the simulation itself failed"; fi
    held=$(apt-mark showhold 2>/dev/null || true)
    if printf '%s\n' "$held" | grep -q '^docker-ce'; then check_fail apt-hold "read apt-mark showhold: docker-ce is held"
    else check_ok apt-hold "read apt-mark showhold: docker-ce is not held"; fi
    pct=$(df -P / | awk 'NR==2 { gsub("%","",$5); print $5 }')
    if [ -n "$pct" ] && [ "$pct" -lt "$disk_max" ]; then check_ok disk "read df -P /: ${pct}% used (< ${disk_max}%)"
    else check_fail disk "read df -P /: '${pct:-?}'% used (limit ${disk_max}%)"; fi
    # 5. presence of the devices is written into the marker at --run (the health body is stored)
    if [ "$pre_failed" = 0 ]; then say "PREFLIGHT OK"; return 0; fi
    say "PREFLIGHT FAILED: the window is postponed"; return 10
}

wait_containers() {
    local i names missing
    for i in $(seq 1 "$wait_tries"); do
        names=$(docker ps --format '{{.Names}}' 2>/dev/null || true)
        missing=""
        for want in postgres redis minio temporal edge api-blue godseye; do
            printf '%s\n' "$names" | grep -q -- "$want" || missing="$missing $want"
        done
        [ -z "$missing" ] && return 0
        sleep "$wait_step"
    done
    say "containers still missing:$missing"; return 1
}

run_window() {
    local kernel_before start_epoch body
    say "step 6: pre-maintenance backup ($backup_script)"
    bash "$backup_script" || { say "backup failed; nothing else was touched"; exit 11; }
    say "step 7: stopping $timer for the window"
    systemctl stop "$timer" || { say "could not stop $timer"; exit 11; }
    say "step 8: removing the never-started leftover $leftover"
    if [ "$(docker inspect -f '{{.State.Status}}' "$leftover" 2>/dev/null || true)" = "created" ]; then
        docker rm "$leftover" >/dev/null || say "warning: could not remove $leftover"
    else say "no '$leftover' in state created; nothing to remove"; fi
    say "step 9: apt-get update && upgrade (Docker restarts inside this step)"
    apt-get update >/dev/null || { say "apt-get update failed"; exit 11; }
    DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::=--force-confold upgrade >/dev/null || { say "apt-get upgrade failed; the timer is still stopped: systemctl start $timer"; exit 11; }
    wait_containers || { say "containers did not return; not rebooting; the timer is still stopped"; exit 11; }
    say "step 10: marker, then reboot"
    kernel_before=$(uname -r); start_epoch=$(date +%s)
    body=$(health_body || true)
    printf 'start_epoch=%s\nstart_iso=%s\nkernel_before=%s\nrelease=%s\nhealth_before=%s\n' \
        "$start_epoch" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$kernel_before" "$(sha_of "$base/RELEASE")" "$body" > "$marker.next"
    mv "$marker.next" "$marker"
    reboot
    say "reboot issued; after boot run: $0 --verify"
}

# --- step 11 ---------------------------------------------------------------------------
verify() {
    if [ ! -f "$marker" ]; then say "no maintenance marker at $marker: nothing to verify"; exit 20; fi
    local start_epoch kernel_before release kernel_now
    start_epoch=$(sed -n 's/^start_epoch=//p' "$marker"); kernel_before=$(sed -n 's/^kernel_before=//p' "$marker")
    release=$(sed -n 's/^release=//p' "$marker"); kernel_now=$(uname -r)
    pre_failed=0
    if [ "$kernel_now" != "$kernel_before" ]; then check_ok kernel "read uname -r: $kernel_now (was $kernel_before)"
    else check_fail kernel "read uname -r: still $kernel_now"; fi
    if [ ! -e "$reboot_required" ]; then check_ok reboot-required "read $reboot_required: gone"
    else check_fail reboot-required "read $reboot_required: still present"; fi
    if wait_containers; then check_ok containers "read docker ps: postgres redis minio temporal edge api-blue godseye up"
    else check_fail containers "read docker ps: a container is missing"; fi
    if systemctl is-active "$timer" >/dev/null 2>&1; then check_ok timer "read systemctl is-active $timer: active"
    else check_fail timer "read systemctl is-active $timer: not active"; fi
    local rec
    rec=$(journalctl -u "$reconcile_service" -n 50 --no-pager 2>/dev/null || true)
    if printf '%s\n' "$rec" | grep -q 'RECONCILE OK'; then check_ok reconcile "read journalctl -u $reconcile_service: RECONCILE OK"
    else check_fail reconcile "read journalctl -u $reconcile_service: no RECONCILE OK line"; fi
    local z
    z=$(ps -eo stat | awk '$1 ~ /^Z/ { n++ } END { print n+0 }')
    if [ "$z" = 0 ]; then check_ok zombies "read ps -eo stat: 0 defunct"; else check_fail zombies "read ps -eo stat: $z defunct"; fi
    # the first good health probe (bounded wait); its time ends the downtime
    local i body good_epoch="" served=""
    for i in $(seq 1 "$wait_tries"); do
        if body=$(health_body) && health_good "$body"; then good_epoch=$(date +%s); break; fi
        sleep "$wait_step"
    done
    if [ -n "$good_epoch" ]; then
        served=$(printf '%s' "$body" | sed -n 's/.*"version": *"\([0-9a-f]\{40\}\)".*/\1/p')
        if [ -z "$served" ] || [ "$served" = "$release" ]; then check_ok health "read $health_url: ok, release ${served:-unreported}"
        else check_fail health "read $health_url: serves $served, expected $release"; fi
    else check_fail health "read $health_url: no good probe within the wait"; fi
    if [ "$pre_failed" != 0 ]; then
        say "VERIFY FAILED: ADR-0223 step 12 applies (the reconcile timer first, then the LKG colour by hand); the marker stays"; exit 21
    fi
    local downtime=$((good_epoch - start_epoch))
    printf '{"window_start":"%s","kernel_before":"%s","kernel_after":"%s","release":"%s","downtime_seconds":%s,"verified_at":"%s"}\n' \
        "$(sed -n 's/^start_iso=//p' "$marker")" "$kernel_before" "$kernel_now" "$release" "$downtime" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$record.next"
    mv "$record.next" "$record"
    rm -f "$marker"
    say "VERIFY OK: downtime ${downtime}s (marker to first good probe); record $record"
}

if [ "$do_verify" = 1 ]; then verify; exit 0; fi
preflight || exit 10
[ "$do_run" = 1 ] && run_window
exit 0
