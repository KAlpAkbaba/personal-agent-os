#!/usr/bin/env bash
# Install the out-of-process Cloud Core recovery monitor.
#
# The monitored action is the already-qualified blue/green reconcile path. Running it
# repeatedly adds the missing post-release detection: if the canonical colour becomes
# unhealthy later, the recorded alternate is started and selected, with a loud exit 81
# and journal evidence. The model and the API process have no authority over this timer.
#
# Usage (root, on the host, after the approved commit has been released):
#   install-recovery-supervisor.sh <approved 40-hex commit sha>
#
# Provenance is an EXACT commit named by whoever approved it. The recovery law is
# installed only when the last COMPLETED promotion ($base/RELEASE) and the deployed tree
# ($app_root/RELEASE) are both that commit, and everything installed is taken from that
# tree. A byte comparison against "the reviewed candidate" is not provenance on its own:
# run from the deployed tree - the natural way on the host - the candidate and the tree
# are the same directory, and the comparison is a file compared with itself.
#
# Off-switch (stops every periodic recovery; release and rollback keep working without it):
#   systemctl disable --now pagentos-bluegreen-reconcile.timer
# Removal: scripts/cloud/uninstall-recovery-supervisor.sh

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
systemd_dir="${PAGENTOS_SYSTEMD_DIR:-/etc/systemd/system}"
systemctl_bin="${PAGENTOS_SYSTEMCTL:-systemctl}"
flock_bin="${PAGENTOS_FLOCK:-flock}"
base="${PAGENTOS_BASE:-/opt/pagentos}"
app_root="${PAGENTOS_APP_ROOT:-$base/app}"
recovery_root="${PAGENTOS_RECOVERY_ROOT:-/opt/pagentos-recovery}"
service_name="pagentos-bluegreen-reconcile.service"
timer_name="pagentos-bluegreen-reconcile.timer"
expected_sha="${1:-${PAGENTOS_RECOVERY_EXPECTED_SHA:-}}"

if [[ $EUID -ne 0 && "${PAGENTOS_ALLOW_NONROOT:-0}" != "1" ]]; then
    echo "run as root: installing a system service is a privileged operation" >&2
    exit 1
fi

if [[ ! "$expected_sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "usage: install-recovery-supervisor.sh <approved 40-hex commit sha>" >&2
    echo "refusing: the recovery law is installed only from an exact, approved commit" >&2
    exit 4
fi

# Every file this installs, relative to a tree. The installer itself is on the list: its
# rollback and wait logic is part of the law being installed.
relative_inputs=(
    "infra/systemd/$service_name"
    "infra/systemd/$timer_name"
    "scripts/cloud/release-cloud-core-bluegreen.sh"
    "scripts/cloud/install-recovery-supervisor.sh"
    "infra/docker/docker-compose.prod.yml"
    "infra/docker/edge/nginx.conf"
)
for relative in "${relative_inputs[@]}"; do
    if [[ ! -f "$app_root/$relative" ]]; then
        echo "required recovery input is absent: $app_root/$relative" >&2
        exit 2
    fi
done

# Everything from the provenance check to the last copied byte happens under the SAME
# kernel lock release, rollback and reconcile hold (release-cloud-core-bluegreen.sh). Without
# it a release finishing during the wait below would swap app/ to another commit after the
# check had passed, and the bundle would be copied from that tree while APPROVED_SHA named
# the approved one. Waiting for the lock is waiting for any release/rollback/reconcile in
# flight; it is released before the proof run, which takes it itself.
lock_file="$base/.bluegreen-operation.lock"
exec 9>"$lock_file"
if ! "$flock_bin" -w "${PAGENTOS_RECOVERY_LOCK_WAIT_S:-1200}" 9; then
    echo "refusing: a release, rollback or reconcile still holds $lock_file; nothing was changed" >&2
    exit 6
fi

completed_sha="$(tr -d '[:space:]' 2>/dev/null < "$base/RELEASE" || true)"
tree_sha="$(tr -d '[:space:]' 2>/dev/null < "$app_root/RELEASE" || true)"
if [[ "$completed_sha" != "$expected_sha" || "$tree_sha" != "$expected_sha" ]]; then
    echo "refusing: approved $expected_sha, but the completed release is '${completed_sha:-none}' and the deployed tree is '${tree_sha:-none}'" >&2
    exit 5
fi

# Run from a separate checkout, that checkout must agree byte for byte with the approved
# tree it is about to install from. (Run from the deployed tree this is trivially true;
# the RELEASE check above is what binds provenance there.)
for relative in "${relative_inputs[@]}"; do
    if ! cmp -s "$repo_root/$relative" "$app_root/$relative"; then
        echo "refusing: $relative differs between this installer's tree ($repo_root) and the approved release tree ($app_root)" >&2
        exit 3
    fi
done
if ! cmp -s "${BASH_SOURCE[0]}" "$app_root/scripts/cloud/install-recovery-supervisor.sh"; then
    echo "refusing: this installer is not the approved release's installer" >&2
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
    "$recovery_root/APPROVED_SHA"
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
    # The unit allows a run 600 s (TimeoutStartSec); a slow run is not a stuck one.
    if [[ "$waited" -ge "${PAGENTOS_RECOVERY_WAIT_TRIES:-660}" ]]; then
        echo "running recovery service did not finish; pinned files were not replaced" >&2
        false
    fi
    sleep "${PAGENTOS_RECOVERY_WAIT_STEP_S:-1}"
    waited=$((waited + 1))
done
# Everything installed comes from the tree whose RELEASE is the approved commit.
install -m 0755 "$app_root/scripts/cloud/release-cloud-core-bluegreen.sh" "$recovery_root/reconcile.sh"
install -m 0600 "$app_root/infra/docker/docker-compose.prod.yml" "$recovery_root/docker-compose.prod.yml"
install -m 0600 "$app_root/infra/docker/edge/nginx.conf" "$recovery_root/nginx.conf"
sha256sum \
    "$recovery_root/reconcile.sh" \
    "$recovery_root/docker-compose.prod.yml" \
    "$recovery_root/nginx.conf" > "$recovery_root/reconcile.sha256"
chmod 0600 "$recovery_root/reconcile.sha256"
printf '%s\n' "$expected_sha" > "$recovery_root/APPROVED_SHA"
chmod 0600 "$recovery_root/APPROVED_SHA"
install -m 0644 "$app_root/infra/systemd/$service_name" "$systemd_dir/$service_name"
install -m 0644 "$app_root/infra/systemd/$timer_name" "$systemd_dir/$timer_name"

# The copy is done; the proof run below is a reconcile, and a reconcile takes this lock.
exec 9>&-

"$systemctl_bin" daemon-reload
# Prove the pinned action once before scheduling it.
"$systemctl_bin" start "$service_name"
"$systemctl_bin" enable --now "$timer_name"
"$systemctl_bin" is-enabled "$timer_name"
"$systemctl_bin" is-active "$timer_name"

trap - ERR
cleanup_backup

echo "RECOVERY SUPERVISOR INSTALLED from $expected_sha: $timer_name checks the live blue/green state every minute"
echo "off-switch: systemctl disable --now $timer_name"
