#!/usr/bin/env bash
# Remove the Cloud Core recovery monitor that install-recovery-supervisor.sh installed.
#
# Removes exactly what the installer placed - the two unit files and the named files of the
# pinned bundle - and nothing else: never an application tree, a release marker, the env
# file or the edge state, which release and rollback own. Idempotent: on a host without the
# monitor it changes nothing and says so.
#
# To PAUSE recovery without removing it (the off-switch):
#   systemctl disable --now pagentos-bluegreen-reconcile.timer

set -Eeuo pipefail

systemd_dir="${PAGENTOS_SYSTEMD_DIR:-/etc/systemd/system}"
systemctl_bin="${PAGENTOS_SYSTEMCTL:-systemctl}"
recovery_root="${PAGENTOS_RECOVERY_ROOT:-/opt/pagentos-recovery}"
service_name="pagentos-bluegreen-reconcile.service"
timer_name="pagentos-bluegreen-reconcile.timer"

if [[ $EUID -ne 0 && "${PAGENTOS_ALLOW_NONROOT:-0}" != "1" ]]; then
    echo "run as root: removing a system service is a privileged operation" >&2
    exit 1
fi

# First, no new run can start.
"$systemctl_bin" disable --now "$timer_name" >/dev/null 2>&1 || true

# A run already in progress keeps its files. Removing the script under a reconcile that is
# mid-way through a colour switch would leave the host half-reconciled by code that no
# longer exists; the timer is disabled, so waiting cannot start another run.
waited=0
while "$systemctl_bin" is-active --quiet "$service_name" >/dev/null 2>&1; do
    if [[ "$waited" -ge "${PAGENTOS_RECOVERY_WAIT_TRIES:-120}" ]]; then
        echo "a recovery run is still active; nothing was removed (the timer is disabled, retry when it finishes)" >&2
        exit 6
    fi
    sleep "${PAGENTOS_RECOVERY_WAIT_STEP_S:-1}"
    waited=$((waited + 1))
done

removed=0
for unit in "$systemd_dir/$service_name" "$systemd_dir/$timer_name"; do
    if [[ -f "$unit" ]]; then
        rm -f "$unit"
        removed=$((removed + 1))
    fi
done
for name in reconcile.sh docker-compose.prod.yml nginx.conf reconcile.sha256 APPROVED_SHA; do
    if [[ -f "$recovery_root/$name" ]]; then
        rm -f "$recovery_root/$name"
        removed=$((removed + 1))
    fi
done
# The directory goes only if it is now empty: anything else in it was not ours to remove.
if [[ -d "$recovery_root" ]]; then
    rmdir "$recovery_root" 2>/dev/null || true
fi

"$systemctl_bin" daemon-reload
"$systemctl_bin" reset-failed "$service_name" >/dev/null 2>&1 || true

if "$systemctl_bin" is-enabled --quiet "$timer_name" >/dev/null 2>&1 \
    || "$systemctl_bin" is-active --quiet "$timer_name" >/dev/null 2>&1; then
    echo "the timer is still enabled or active after removal; inspect: systemctl status $timer_name" >&2
    exit 7
fi

if [[ "$removed" -eq 0 ]]; then
    echo "RECOVERY SUPERVISOR NOT INSTALLED: nothing to remove"
else
    echo "RECOVERY SUPERVISOR REMOVED ($removed files); release and rollback are unaffected"
fi
