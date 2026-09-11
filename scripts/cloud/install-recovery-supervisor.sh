#!/usr/bin/env bash
# Install the out-of-process Cloud Core recovery monitor.
#
# The monitored action is the already-qualified blue/green reconcile path. Running it
# repeatedly adds the missing post-release detection: if the canonical colour becomes
# unhealthy later, the recorded alternate is started and selected, with a loud exit 81
# and journal evidence. The model and the API process have no authority over this timer.

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
systemd_dir="${PAGENTOS_SYSTEMD_DIR:-/etc/systemd/system}"
systemctl_bin="${PAGENTOS_SYSTEMCTL:-systemctl}"
app_root="${PAGENTOS_APP_ROOT:-/opt/pagentos/app}"
recovery_root="${PAGENTOS_RECOVERY_ROOT:-/opt/pagentos-recovery}"
service_name="pagentos-bluegreen-reconcile.service"
timer_name="pagentos-bluegreen-reconcile.timer"

if [[ $EUID -ne 0 && "${PAGENTOS_ALLOW_NONROOT:-0}" != "1" ]]; then
    echo "run as root: installing a system service is a privileged operation" >&2
    exit 1
fi

for source in \
    "$repo_root/infra/systemd/$service_name" \
    "$repo_root/infra/systemd/$timer_name" \
    "$app_root/scripts/cloud/release-cloud-core-bluegreen.sh" \
    "$app_root/infra/docker/docker-compose.prod.yml" \
    "$app_root/infra/docker/edge/nginx.conf"; do
    if [[ ! -f "$source" ]]; then
        echo "required recovery input is absent: $source" >&2
        exit 2
    fi
done

if ! cmp -s \
    "$repo_root/scripts/cloud/release-cloud-core-bluegreen.sh" \
    "$app_root/scripts/cloud/release-cloud-core-bluegreen.sh"; then
    echo "refusing: the installed recovery action is not the reviewed candidate tree" >&2
    exit 3
fi
if ! cmp -s "$repo_root/infra/docker/docker-compose.prod.yml" "$app_root/infra/docker/docker-compose.prod.yml" \
    || ! cmp -s "$repo_root/infra/docker/edge/nginx.conf" "$app_root/infra/docker/edge/nginx.conf"; then
    echo "refusing: installed recovery configuration is not the reviewed candidate tree" >&2
    exit 3
fi

mkdir -p "$systemd_dir" "$recovery_root"
chmod 0700 "$recovery_root"

# Preserve the installed monitor as one transaction. In particular, an upgrade whose
# proof fails must restore and restart the previously enabled timer instead of leaving
# either the broken candidate or a silently stopped recovery path behind.
backup_dir="$(mktemp -d)"
destinations=(
    "$systemd_dir/$service_name"
    "$systemd_dir/$timer_name"
    "$recovery_root/reconcile.sh"
    "$recovery_root/docker-compose.prod.yml"
    "$recovery_root/nginx.conf"
    "$recovery_root/reconcile.sha256"
)
for destination in "${destinations[@]}"; do
    if [[ -f "$destination" ]]; then
        cp -p "$destination" "$backup_dir/$(basename "$destination")"
    fi
done
was_enabled=0
was_active=0
if "$systemctl_bin" is-enabled --quiet "$timer_name" >/dev/null 2>&1; then was_enabled=1; fi
if "$systemctl_bin" is-active --quiet "$timer_name" >/dev/null 2>&1; then was_active=1; fi

cleanup_backup() {
    for saved in "$backup_dir"/*; do [[ -e "$saved" ]] && rm -f "$saved"; done
    rmdir "$backup_dir" 2>/dev/null || true
}

rollback_install() {
    rc=$?
    trap - ERR
    set +e
    for destination in "${destinations[@]}"; do
        saved="$backup_dir/$(basename "$destination")"
        if [[ -f "$saved" ]]; then cp -p "$saved" "$destination"; else rm -f "$destination"; fi
    done
    "$systemctl_bin" daemon-reload
    if [[ "$was_enabled" = "1" ]]; then
        "$systemctl_bin" enable "$timer_name"
        [[ "$was_active" = "1" ]] && "$systemctl_bin" start "$timer_name"
    else
        "$systemctl_bin" disable --now "$timer_name"
    fi
    cleanup_backup
    echo "recovery supervisor installation failed; previous monitor restored" >&2
    exit "$rc"
}
trap rollback_install ERR

"$systemctl_bin" stop "$timer_name" >/dev/null 2>&1 || true
# A timer may already have launched the oneshot. Do not replace its code or let `start`
# join that old job and masquerade as proof of the new pinned action.
waited=0
while "$systemctl_bin" is-active --quiet "$service_name" >/dev/null 2>&1; do
    if [[ "$waited" -ge "${PAGENTOS_RECOVERY_WAIT_TRIES:-120}" ]]; then
        echo "running recovery service did not finish; pinned files were not replaced" >&2
        false
    fi
    sleep "${PAGENTOS_RECOVERY_WAIT_STEP_S:-1}"
    waited=$((waited + 1))
done
install -m 0755 "$repo_root/scripts/cloud/release-cloud-core-bluegreen.sh" "$recovery_root/reconcile.sh"
install -m 0600 "$repo_root/infra/docker/docker-compose.prod.yml" "$recovery_root/docker-compose.prod.yml"
install -m 0600 "$repo_root/infra/docker/edge/nginx.conf" "$recovery_root/nginx.conf"
sha256sum \
    "$recovery_root/reconcile.sh" \
    "$recovery_root/docker-compose.prod.yml" \
    "$recovery_root/nginx.conf" > "$recovery_root/reconcile.sha256"
chmod 0600 "$recovery_root/reconcile.sha256"
install -m 0644 "$repo_root/infra/systemd/$service_name" "$systemd_dir/$service_name"
install -m 0644 "$repo_root/infra/systemd/$timer_name" "$systemd_dir/$timer_name"

"$systemctl_bin" daemon-reload
# Prove the pinned action once before scheduling it.
"$systemctl_bin" start "$service_name"
"$systemctl_bin" enable --now "$timer_name"
"$systemctl_bin" is-enabled "$timer_name"
"$systemctl_bin" is-active "$timer_name"

trap - ERR
cleanup_backup

echo "RECOVERY SUPERVISOR INSTALLED: $timer_name checks the live blue/green state every minute"
