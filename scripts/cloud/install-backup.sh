#!/usr/bin/env bash
# Install the Cloud Core backup on the host (ADR-0122): restic from Ubuntu's signed archive,
# a root-only repository password generated here and never printed, the scripts pinned
# under /opt/pagentos-backup with a digest systemd checks before every run, and two timers -
# the nightly backup and the weekly restore drill.
#
# It ends by taking one backup and restoring it in a drill. An install whose first backup
# cannot be restored has installed nothing worth having, so the timers are enabled only
# after both have passed.
#
# Usage (root, on the host, from the deployed tree):
#   /opt/pagentos/app/scripts/cloud/install-backup.sh
#
# The password must also exist OFF the host, or losing the host loses every backup with it:
#   (owner's PC)  scripts\cloud\escrow-backup-key.ps1
#
# Exit: 0 installed and proven; 1 not root; 3 restic could not be installed; 4 the proof
# backup failed; 5 the proof drill failed (the timers are NOT enabled in either case).

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
base=${PAGENTOS_BASE:-/opt/pagentos}
bin_root=${PAGENTOS_BACKUP_BIN:-/opt/pagentos-backup}
backup_root=${PAGENTOS_BACKUP_ROOT:-/var/lib/pagentos-backup}
systemd_dir=${PAGENTOS_SYSTEMD_DIR:-/etc/systemd/system}
systemctl_bin=${PAGENTOS_SYSTEMCTL:-systemctl}
apt_bin=${PAGENTOS_APT:-apt-get}
restic_bin=${PAGENTOS_RESTIC:-restic}
password_file=${RESTIC_PASSWORD_FILE:-$base/backup.password}
units=(pagentos-backup.service pagentos-backup.timer pagentos-restore-drill.service pagentos-restore-drill.timer)

if [[ $EUID -ne 0 && "${PAGENTOS_ALLOW_NONROOT:-0}" != "1" ]]; then
    echo "run as root: this installs a package, a secret and system timers" >&2
    exit 1
fi

say() { printf 'install-backup: %s\n' "$*"; }

# ---- restic -----------------------------------------------------------------------------
if ! command -v "$restic_bin" >/dev/null 2>&1; then
    say "installing restic from the Ubuntu archive"
    DEBIAN_FRONTEND=noninteractive "$apt_bin" update -qq >/dev/null \
        && DEBIAN_FRONTEND=noninteractive "$apt_bin" install -y -qq restic >/dev/null \
        || { echo "restic could not be installed" >&2; exit 3; }
fi
say "restic: $("$restic_bin" version 2>/dev/null | head -1)"

# ---- the repository password: generated once, root-only, never printed -------------------
if [ ! -s "$password_file" ]; then
    (umask 077; head -c 48 /dev/urandom | base64 | tr -d '\n' > "$password_file")
    say "generated a new repository password at $password_file (not shown; escrow it off the host)"
fi
chmod 0600 "$password_file"
[ "${PAGENTOS_ALLOW_NONROOT:-0}" = "1" ] || chown root:root "$password_file"

# ---- the pinned scripts, and the digest systemd checks before every run ------------------
mkdir -p "$bin_root" "$backup_root"
chmod 0700 "$bin_root" "$backup_root"
install -m 0700 "$repo_root/scripts/cloud/backup-cloud-core.sh" "$bin_root/backup-cloud-core.sh"
install -m 0700 "$repo_root/scripts/cloud/restore-cloud-core.sh" "$bin_root/restore-cloud-core.sh"
install -m 0600 "$repo_root/scripts/cloud/backup_manifest.py" "$bin_root/backup_manifest.py"
sha256sum "$bin_root/backup-cloud-core.sh" "$bin_root/restore-cloud-core.sh" \
    "$bin_root/backup_manifest.py" > "$bin_root/SHA256SUMS"
chmod 0600 "$bin_root/SHA256SUMS"
for unit in "${units[@]}"; do
    install -m 0644 "$repo_root/infra/systemd/$unit" "$systemd_dir/$unit"
done
"$systemctl_bin" daemon-reload

# ---- prove it: one backup, restored in one drill, before anything is scheduled -----------
say "proof backup"
PAGENTOS_BACKUP_ROOT="$backup_root" bash "$bin_root/backup-cloud-core.sh" --kind manual --label install-proof \
    || { echo "the proof backup failed; the timers were not enabled" >&2; exit 4; }
say "proof drill"
PAGENTOS_BACKUP_ROOT="$backup_root" bash "$bin_root/restore-cloud-core.sh" --drill \
    || { echo "the proof drill failed; the timers were not enabled" >&2; exit 5; }

"$systemctl_bin" enable --now pagentos-backup.timer pagentos-restore-drill.timer
"$systemctl_bin" is-enabled pagentos-backup.timer pagentos-restore-drill.timer >/dev/null

echo "BACKUP INSTALLED: nightly backup and weekly restore drill enabled; repository $backup_root/restic"
echo "the repository password is NOT off this host yet unless escrowed: scripts\\cloud\\escrow-backup-key.ps1 (owner's PC)"
echo "pause: systemctl disable --now pagentos-backup.timer pagentos-restore-drill.timer"
