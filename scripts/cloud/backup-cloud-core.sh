#!/usr/bin/env bash
# Back up the production Cloud Core: every database, the artefact store, and the host's own
# configuration and release/recovery metadata - into an encrypted, deduplicated,
# integrity-checked restic repository on the root disk (not the data volume it protects),
# and into a second repository off the host when one is configured (ADR-0122;
# docs/CLOUD_INFRASTRUCTURE.md section 7).
#
# Usage (root, on the host; normally the pinned copy the installer placed):
#   backup-cloud-core.sh [--kind scheduled|pre-migration|manual] [--label TEXT]
#
# A snapshot is one directory tree, described by its own MANIFEST.json:
#   postgres/globals.sql                  roles and grants (pg_dumpall --globals-only)
#   postgres/<db>.dump                    every non-template database, pg_dump -Fc
#   postgres/<db>.fingerprint.json        per table: rows + sha256 of its rows, from the DUMP
#   minio/<bucket>/...                    every object, mirrored through the MinIO API
#   config/opt-pagentos/...               .env (secrets: the repository is encrypted),
#                                         RELEASE, LAST_KNOWN_GOOD, LAST_RECONCILE, markers
#   config/pagentos-data/{identity,edge}  the owner-credential root and the edge state
#   config/opt-pagentos-recovery/...      the pinned recovery bundle, when installed
#   config/systemd/pagentos-*             the host's own unit files
#   MANIFEST.json                         size + sha256 of every file above
# Not in it: container images (every release image is rebuilt from its commit) and the
# repository password itself (it would be locked inside what it opens - it is escrowed
# off the host instead, scripts/cloud/escrow-backup-key.ps1).
#
# Retention by kind: scheduled - daily 14 / weekly 8 / monthly 6; pre-migration - last 10;
# manual - last 5. Integrity: `restic check --read-data-subset` after every backup.
#
# Exit: 0 ok; 1 not root; 2 usage; 90 another backup holds the lock; 91 the repository
# password is missing or not root-only; 92 a database dump failed; 93 the object mirror
# failed; 94 restic failed (init/backup/forget/check); 95 the local snapshot is good but
# the off-host copy failed.

set -Eeuo pipefail

base=${PAGENTOS_BASE:-/opt/pagentos}
data=${PAGENTOS_DATA:-/mnt/pagentos-data}
backup_root=${PAGENTOS_BACKUP_ROOT:-/var/lib/pagentos-backup}
recovery_root=${PAGENTOS_RECOVERY_ROOT:-/opt/pagentos-recovery}
systemd_dir=${PAGENTOS_SYSTEMD_DIR:-/etc/systemd/system}
pg_container=${PAGENTOS_PG_CONTAINER:-pagentos-prod-postgres}
pg_user=${PAGENTOS_PG_USER:-pagentos}
minio_container=${PAGENTOS_MINIO_CONTAINER:-pagentos-prod-minio}
restic_bin=${PAGENTOS_RESTIC:-restic}
docker_bin=${PAGENTOS_DOCKER:-docker}
flock_bin=${PAGENTOS_FLOCK:-flock}
python_bin=${PAGENTOS_PYTHON:-python3}
helper=${PAGENTOS_BACKUP_HELPER:-$(dirname "${BASH_SOURCE[0]}")/backup_manifest.py}
host_name=${PAGENTOS_BACKUP_HOST:-pagentos-core}
offhost_env=${PAGENTOS_BACKUP_OFFHOST_ENV:-$base/backup-offhost.env}
check_subset=${PAGENTOS_BACKUP_CHECK_SUBSET:-100%}
export RESTIC_REPOSITORY=${RESTIC_REPOSITORY:-$backup_root/restic}
export RESTIC_PASSWORD_FILE=${RESTIC_PASSWORD_FILE:-$base/backup.password}
local_repository=$RESTIC_REPOSITORY

kind=scheduled
label=""
while [ $# -gt 0 ]; do
    case "$1" in
        --kind) kind=${2:-}; shift 2 || true;;
        --label) label=${2:-}; shift 2 || true;;
        *) echo "usage: backup-cloud-core.sh [--kind scheduled|pre-migration|manual] [--label TEXT]" >&2; exit 2;;
    esac
done
case "$kind" in scheduled|pre-migration|manual) ;; *) echo "unknown --kind '$kind'" >&2; exit 2;; esac
case "$label" in *[!A-Za-z0-9._-]*) echo "--label may hold only A-Z a-z 0-9 . _ -" >&2; exit 2;; esac

if [[ $EUID -ne 0 && "${PAGENTOS_ALLOW_NONROOT:-0}" != "1" ]]; then
    echo "run as root: the backup reads the env file, the credential root and every database" >&2
    exit 1
fi

say() { printf 'backup: %s\n' "$*"; }
fail() { local code=$1; shift; printf 'BACKUP FAILED (%s): %s\n' "$code" "$*" >&2; exit "$code"; }

# The password opens every secret in the repository; anything but root-only is refused.
if [ ! -s "$RESTIC_PASSWORD_FILE" ]; then
    fail 91 "the repository password $RESTIC_PASSWORD_FILE is missing (run install-backup.sh)"
fi
if [ "${PAGENTOS_ALLOW_NONROOT:-0}" != "1" ]; then
    posture=$(stat -c '%a:%U' "$RESTIC_PASSWORD_FILE")
    case "$posture" in 600:root|400:root) ;; *) fail 91 "$RESTIC_PASSWORD_FILE is $posture; it must be 600:root";; esac
fi

mkdir -p "$backup_root"
chmod 0700 "$backup_root"
exec 8>"$backup_root/.backup.lock"
if ! "$flock_bin" -n 8; then
    echo "another backup is running; this one was not started" >&2
    exit 90
fi

# One fixed staging path: restic stores absolute paths, so every snapshot has the same
# layout and a restore names it the same way (restore-cloud-core.sh).
staging="$backup_root/staging/snapshot"
rm -rf "$staging"
mkdir -p "$staging/postgres" "$staging/minio" "$staging/config/opt-pagentos" \
    "$staging/config/pagentos-data" "$staging/config/systemd"
chmod 0700 "$backup_root/staging" "$staging"
minio_tmp="/tmp/pagentos-backup-$$"
cleanup() {
    rm -rf "$staging"
    "$docker_bin" exec "$minio_container" rm -rf "$minio_tmp" >/dev/null 2>&1 || true
}
trap cleanup EXIT
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
started_s=$(date +%s)

# ---- postgres: roles, then every database, each fingerprinted from its own dump ----------
say "postgres: roles"
"$docker_bin" exec "$pg_container" pg_dumpall -U "$pg_user" --globals-only \
    > "$staging/postgres/globals.sql" || fail 92 "pg_dumpall --globals-only failed"
databases=$("$docker_bin" exec "$pg_container" psql -U "$pg_user" -d postgres -Atc \
    "select datname from pg_database where not datistemplate order by 1") \
    || fail 92 "cannot list the databases"
[ -n "$databases" ] || fail 92 "the server listed no databases"
for db in $databases; do
    case "$db" in *[!A-Za-z0-9_]*) fail 92 "refusing an unexpected database name '$db'";; esac
    "$docker_bin" exec "$pg_container" pg_dump -U "$pg_user" -Fc -d "$db" \
        > "$staging/postgres/$db.dump" || fail 92 "pg_dump $db failed"
    [ -s "$staging/postgres/$db.dump" ] || fail 92 "pg_dump $db produced nothing"
    "$docker_bin" exec -i "$pg_container" pg_restore --data-only -f - \
        < "$staging/postgres/$db.dump" | "$python_bin" "$helper" fingerprint \
        > "$staging/postgres/$db.fingerprint.json" || fail 92 "the dump of $db cannot be read back"
    say "postgres: $db $(stat -c %s "$staging/postgres/$db.dump") bytes"
done
alembic=$("$docker_bin" exec "$pg_container" psql -U "$pg_user" -d pagentos_prod -Atc \
    "select version_num from alembic_version" 2>/dev/null || true)

# ---- objects, through the MinIO API (a consistent object view, not live data files) -----
say "objects: mirroring every bucket"
# The credentials never leave the container: the alias is set there, in a throwaway config
# dir. The LISTING is parsed here, on the host. Until 2026-09-12 it was parsed in the
# container with awk - which that image does not have (sh, mc, tr, cut, mkdir, rm, ls, head
# and no more) - so the loop saw no buckets and every backup silently held no objects at all.
"$docker_bin" exec "$minio_container" sh -c '
    set -e
    mkdir -p "$1/mc" "$1/data"
    mc --config-dir "$1/mc" alias set bk http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
' sh "$minio_tmp" || fail 93 "the MinIO alias could not be set"
listing=$("$docker_bin" exec "$minio_container" \
    mc --config-dir "$minio_tmp/mc" ls --json bk) || fail 93 "the bucket listing failed"
buckets=$(printf '%s\n' "$listing" | "$python_bin" -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    row = json.loads(line)
    key = str(row.get("key", "")).rstrip("/")
    if row.get("status") == "success" and key:
        print(key)
') || fail 93 "the bucket listing could not be read"
[ -n "$buckets" ] || fail 93 "MinIO reported no bucket; refusing a backup that would hold no objects"
while IFS= read -r bucket; do
    [ -n "$bucket" ] || continue
    say "objects: $bucket"
    "$docker_bin" exec "$minio_container" sh -c '
        set -e
        mkdir -p "$2/$3"
        mc --config-dir "$1" mirror --quiet "bk/$3" "$2/$3" >/dev/null
    ' sh "$minio_tmp/mc" "$minio_tmp/data" "$bucket" || fail 93 "the object mirror failed for $bucket"
done <<BUCKETS
$buckets
BUCKETS
"$docker_bin" exec "$minio_container" rm -rf "$minio_tmp/mc" >/dev/null 2>&1 || true
"$docker_bin" cp "$minio_container:$minio_tmp/data/." "$staging/minio/" >/dev/null \
    || fail 93 "the mirrored objects could not be copied out"
"$docker_bin" exec "$minio_container" rm -rf "$minio_tmp" >/dev/null 2>&1 || true
# Said out loud, so "it held no objects" can never again be read as silence.
say "objects: $(find "$staging/minio" -type f | wc -l | tr -d ' ') file(s) from \
$(printf '%s\n' "$buckets" | wc -l | tr -d ' ') bucket(s)"

# ---- the host's own configuration and release/recovery metadata --------------------------
for name in .env RELEASE LAST_KNOWN_GOOD LAST_RECONCILE RECOVERY_BUNDLE_STALE; do
    [ -f "$base/$name" ] && cp -p "$base/$name" "$staging/config/opt-pagentos/"
done
for name in identity edge; do
    [ -d "$data/$name" ] && cp -a "$data/$name" "$staging/config/pagentos-data/"
done
[ -d "$recovery_root" ] && cp -a "$recovery_root" "$staging/config/opt-pagentos-recovery"
for unit in "$systemd_dir"/pagentos-*; do
    [ -f "$unit" ] && cp -p "$unit" "$staging/config/systemd/"
done

release=$(tr -d '[:space:]' 2>/dev/null < "$base/RELEASE" || true)
"$python_bin" "$helper" manifest "$staging" \
    "kind=$kind" "label=$label" "host=$host_name" "started_at=$started" \
    "release=${release:-unknown}" "alembic=${alembic:-unknown}" \
    >/dev/null || fail 94 "the manifest could not be written"

# ---- restic: the snapshot, retention, and a read-back of the data itself -----------------
if ! "$restic_bin" cat config >/dev/null 2>&1; then
    say "repository $RESTIC_REPOSITORY does not exist yet; initialising it"
    "$restic_bin" init >/dev/null || fail 94 "restic init failed"
fi
# A backup cut short (the release bounds its pre-migration backup with `timeout`) can leave a
# restic lock behind, and a lock left behind fails every later prune. Under the backup lock no
# other backup or restore of ours is running; plain `unlock` removes only stale locks.
"$restic_bin" unlock >/dev/null 2>&1 || true
tags=(--tag pagentos --tag "$kind")
[ -n "$label" ] && tags+=(--tag "$label")
summary=$("$restic_bin" backup --json --host "$host_name" "${tags[@]}" "$staging" | tail -1) \
    || fail 94 "restic backup failed"
snapshot=$(printf '%s' "$summary" | grep -oE '"snapshot_id":"[0-9a-f]+"' | cut -d'"' -f4 || true)
[ -n "$snapshot" ] || fail 94 "restic reported no snapshot id"
say "snapshot $snapshot ($kind)"

retention() {
    case "$kind" in
        scheduled) echo "--keep-daily ${PAGENTOS_BACKUP_KEEP_DAILY:-14} --keep-weekly ${PAGENTOS_BACKUP_KEEP_WEEKLY:-8} --keep-monthly ${PAGENTOS_BACKUP_KEEP_MONTHLY:-6}";;
        pre-migration) echo "--keep-last ${PAGENTOS_BACKUP_KEEP_PRE_MIGRATION:-10}";;
        manual) echo "--keep-last ${PAGENTOS_BACKUP_KEEP_MANUAL:-5}";;
    esac
}
# shellcheck disable=SC2046
"$restic_bin" forget --host "$host_name" --tag "$kind" $(retention) --prune >/dev/null \
    || fail 94 "restic forget/prune failed"
"$restic_bin" check --read-data-subset="$check_subset" >/dev/null \
    || fail 94 "restic check found the repository damaged"

# ---- off the host, when configured --------------------------------------------------------
offhost="not configured"
if [ -f "$offhost_env" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$offhost_env"
    set +a
    target=${PAGENTOS_BACKUP_OFFHOST_REPOSITORY:-}
    if [ -z "$target" ]; then
        offhost="configured without PAGENTOS_BACKUP_OFFHOST_REPOSITORY"
    else
        offhost="failed"
        if ! RESTIC_REPOSITORY="$target" "$restic_bin" cat config >/dev/null 2>&1; then
            RESTIC_REPOSITORY="$target" "$restic_bin" init --copy-chunker-params \
                --from-repo "$local_repository" --from-password-file "$RESTIC_PASSWORD_FILE" >/dev/null || true
        fi
        # shellcheck disable=SC2046
        if RESTIC_REPOSITORY="$target" "$restic_bin" copy --from-repo "$local_repository" \
                --from-password-file "$RESTIC_PASSWORD_FILE" "$snapshot" >/dev/null \
            && RESTIC_REPOSITORY="$target" "$restic_bin" forget --host "$host_name" --tag "$kind" \
                $(retention) --prune >/dev/null \
            && RESTIC_REPOSITORY="$target" "$restic_bin" check >/dev/null; then
            offhost="ok"
        fi
    fi
fi

finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)
seconds=$(( $(date +%s) - started_s ))
record="$backup_root/LAST_BACKUP.json"
printf '{"snapshot":"%s","kind":"%s","label":"%s","started_at":"%s","finished_at":"%s","seconds":%s,"release":"%s","alembic":"%s","offhost":"%s"}\n' \
    "$snapshot" "$kind" "$label" "$started" "$finished" "$seconds" "${release:-unknown}" "${alembic:-unknown}" "$offhost" \
    > "$record.next"
mv "$record.next" "$record"

if [ "$offhost" = "failed" ]; then
    echo "BACKUP PARTIAL: local snapshot $snapshot is good and checked; the off-host copy FAILED" >&2
    exit 95
fi
echo "BACKUP OK: snapshot $snapshot ($kind) in ${seconds}s; off-host: $offhost"
