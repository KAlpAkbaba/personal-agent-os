#!/usr/bin/env bash
# Prove that a Cloud Core backup restores - or restore one over production (ADR-0122).
#
#   restore-cloud-core.sh --drill [--snapshot ID]
#       Restore the snapshot (default: the newest) into SCRATCH containers beside
#       production - no network, throwaway credentials, the same images production runs -
#       and verify all of it: every file against the snapshot's own manifest, every
#       database re-dumped and fingerprinted against the fingerprint taken from the backup's
#       dump, every object loaded into a scratch MinIO and read back out through its API.
#       Measures each step and writes a report. Changes nothing in production.
#
#   restore-cloud-core.sh --apply --snapshot ID --confirm "RESTORE ID OVER PRODUCTION"
#       Replace production's databases and objects with the snapshot's. Refuses without the
#       exact confirmation naming the snapshot. First takes a `manual` backup labelled
#       pre-restore (so the restore is itself undoable), then holds the backup lock AND the
#       blue/green operation lock (no release, rollback or reconcile can interleave), stops
#       both api colours and Temporal, restores every database and bucket, verifies the
#       databases against the backup's fingerprints, starts Temporal, and hands the colours
#       back to the reconcile. The env file and credential root are NOT replaced (they are in
#       the snapshot under config/ for a rebuilt host; overwriting live secrets is a decision,
#       not a side effect).
#
# Exit: 0 ok; 1 not root; 2 usage/refused; 90 a backup or blue/green operation holds a lock;
# 96 the snapshot could not be restored; 97 files differ from the manifest; 98 a database
# did not restore to the same rows; 99 the objects did not load back; 100 --apply: the
# pre-restore backup failed (nothing was touched); 101 --apply: services did not come back.

set -Eeuo pipefail

base=${PAGENTOS_BASE:-/opt/pagentos}
backup_root=${PAGENTOS_BACKUP_ROOT:-/var/lib/pagentos-backup}
pg_container=${PAGENTOS_PG_CONTAINER:-pagentos-prod-postgres}
pg_user=${PAGENTOS_PG_USER:-pagentos}
minio_container=${PAGENTOS_MINIO_CONTAINER:-pagentos-prod-minio}
temporal_container=${PAGENTOS_TEMPORAL_CONTAINER:-pagentos-prod-temporal}
colour_containers=${PAGENTOS_COLOUR_CONTAINERS:-pagentos-prod-api-blue pagentos-prod-api-green}
restic_bin=${PAGENTOS_RESTIC:-restic}
docker_bin=${PAGENTOS_DOCKER:-docker}
flock_bin=${PAGENTOS_FLOCK:-flock}
python_bin=${PAGENTOS_PYTHON:-python3}
here=$(dirname "${BASH_SOURCE[0]}")
helper=${PAGENTOS_BACKUP_HELPER:-$here/backup_manifest.py}
backup_script=${PAGENTOS_BACKUP_SCRIPT:-$here/backup-cloud-core.sh}
# The reconcile that hands the colours back after --apply: the recovery timer's pinned copy
# when it is installed (ADR-0121), otherwise the deployed tree's.
reconcile_script=${PAGENTOS_RECONCILE_SCRIPT:-}
if [ -z "$reconcile_script" ]; then
    if [ -x /opt/pagentos-recovery/reconcile.sh ]; then
        reconcile_script=/opt/pagentos-recovery/reconcile.sh
    else
        reconcile_script=$base/app/scripts/cloud/release-cloud-core-bluegreen.sh
    fi
fi
host_name=${PAGENTOS_BACKUP_HOST:-pagentos-core}
ready_tries=${PAGENTOS_DRILL_READY_TRIES:-60}
ready_step=${PAGENTOS_DRILL_READY_STEP_S:-1}
export RESTIC_REPOSITORY=${RESTIC_REPOSITORY:-$backup_root/restic}
export RESTIC_PASSWORD_FILE=${RESTIC_PASSWORD_FILE:-$base/backup.password}
staging_path="$backup_root/staging/snapshot"

mode=""
snapshot=""
confirm=""
while [ $# -gt 0 ]; do
    case "$1" in
        --drill) mode=drill; shift;;
        --apply) mode=apply; shift;;
        --snapshot) snapshot=${2:-}; shift 2 || true;;
        --confirm) confirm=${2:-}; shift 2 || true;;
        *) echo "usage: restore-cloud-core.sh --drill [--snapshot ID] | --apply --snapshot ID --confirm \"RESTORE ID OVER PRODUCTION\"" >&2; exit 2;;
    esac
done
[ -n "$mode" ] || { echo "say --drill or --apply" >&2; exit 2; }
case "$snapshot" in *[!0-9a-f]*) echo "a snapshot id is hexadecimal" >&2; exit 2;; esac

if [[ $EUID -ne 0 && "${PAGENTOS_ALLOW_NONROOT:-0}" != "1" ]]; then
    echo "run as root" >&2
    exit 1
fi

if [ "$mode" = "apply" ]; then
    if [ -z "$snapshot" ] || [ "$confirm" != "RESTORE $snapshot OVER PRODUCTION" ]; then
        echo "refusing: --apply replaces production's data; pass --snapshot ID and --confirm \"RESTORE ID OVER PRODUCTION\" with that same id" >&2
        exit 2
    fi
fi

say() { printf 'restore: %s\n' "$*"; }
pre_restore=""
fail() {
    local code=$1; shift
    printf 'RESTORE FAILED (%s): %s\n' "$code" "$*" >&2
    if [ -n "$pre_restore" ]; then
        printf 'production was mid-restore; its state from just before is snapshot %s:\n' "$pre_restore" >&2
        printf '  restore-cloud-core.sh --apply --snapshot %s --confirm "RESTORE %s OVER PRODUCTION"\n' \
            "$pre_restore" "$pre_restore" >&2
    fi
    exit "$code"
}
now_s() { date +%s; }
random_secret() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# --apply: the safety point comes first, under the backup's own lock, before anything else.
if [ "$mode" = "apply" ]; then
    say "taking a pre-restore backup of the current production state"
    "$backup_script" --kind manual --label pre-restore || fail 100 "the pre-restore backup failed; nothing was touched"
    pre_restore=$(grep -oE '"snapshot":"[0-9a-f]+"' "$backup_root/LAST_BACKUP.json" | cut -d'"' -f4 || true)
fi

mkdir -p "$backup_root"
exec 8>"$backup_root/.backup.lock"
"$flock_bin" -w "${PAGENTOS_RESTORE_LOCK_WAIT_S:-600}" 8 || { echo "a backup still holds the lock" >&2; exit 90; }
if [ "$mode" = "apply" ]; then
    exec 9>"$base/.bluegreen-operation.lock"
    "$flock_bin" -w "${PAGENTOS_RESTORE_LOCK_WAIT_S:-600}" 9 || { echo "a release, rollback or reconcile still holds the lock" >&2; exit 90; }
fi

if [ -z "$snapshot" ]; then
    snapshot=$("$restic_bin" snapshots --json --latest 1 --host "$host_name" --tag pagentos \
        | grep -oE '"id":"[0-9a-f]{64}"' | tail -1 | cut -d'"' -f4 || true)
    [ -n "$snapshot" ] || fail 96 "the repository holds no snapshot to restore"
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
work="$backup_root/drill/$stamp"
tree="$work/tree"
pg_scratch="pagentos-drill-pg-$$"
minio_scratch="pagentos-drill-minio-$$"
cleanup() {
    "$docker_bin" rm -f "$pg_scratch" "$minio_scratch" >/dev/null 2>&1 || true
    rm -rf "$work"
}
trap cleanup EXIT
mkdir -p "$work"
chmod 0700 "$backup_root" "$work"
started_s=$(now_s)

# ---- 1. the snapshot, and every file in it against its own manifest ----------------------
say "restoring snapshot ${snapshot:0:12}"
"$restic_bin" restore "$snapshot:$staging_path" --target "$tree" >/dev/null \
    || fail 96 "restic could not restore $snapshot"
restored_s=$(now_s)
[ -f "$tree/MANIFEST.json" ] || fail 97 "the snapshot carries no MANIFEST.json"
"$python_bin" "$helper" verify "$tree" || fail 97 "files differ from the snapshot's manifest"
for required in config/opt-pagentos/.env config/opt-pagentos/RELEASE postgres/globals.sql; do
    [ -f "$tree/$required" ] || fail 97 "the snapshot cannot rebuild a host: $required is missing"
done
verified_s=$(now_s)

pg_image=$("$docker_bin" inspect -f '{{.Config.Image}}' "$pg_container")
minio_image=$("$docker_bin" inspect -f '{{.Config.Image}}' "$minio_container")

wait_for() {
    # wait_for WHAT COMMAND...: until COMMAND succeeds, bounded.
    local what=$1; shift
    local tries=0
    until "$@" >/dev/null 2>&1; do
        tries=$((tries + 1))
        if [ "$tries" -ge "$ready_tries" ]; then return 1; fi
        sleep "$ready_step"
    done
}

# ---- 2. the databases ---------------------------------------------------------------------
if [ "$mode" = "drill" ]; then
    pg_target=$pg_scratch
    "$docker_bin" run -d --name "$pg_scratch" --network none \
        -e POSTGRES_USER="$pg_user" -e POSTGRES_PASSWORD="$(random_secret)" -e POSTGRES_DB=postgres \
        "$pg_image" >/dev/null || fail 98 "the scratch postgres did not start"
    wait_for postgres "$docker_bin" exec "$pg_scratch" pg_isready -U "$pg_user" -d postgres \
        || fail 98 "the scratch postgres never became ready"
    # pg_isready answers during the image's init restart; a real query is the readiness.
    wait_for postgres "$docker_bin" exec "$pg_scratch" psql -U "$pg_user" -d postgres -Atc "select 1" \
        || fail 98 "the scratch postgres never answered a query"
else
    pg_target=$pg_container
    say "stopping both api colours and Temporal (the reconcile brings them back)"
    for c in $colour_containers; do "$docker_bin" stop "$c" >/dev/null 2>&1 || true; done
    "$docker_bin" stop "$temporal_container" >/dev/null 2>&1 || true
fi

# Roles first. On the target the POSTGRES_USER role already exists, so its CREATE fails and
# is the one expected error; every other statement must succeed.
"$docker_bin" exec -i "$pg_target" psql -U "$pg_user" -d postgres -q -v ON_ERROR_STOP=0 \
    < "$tree/postgres/globals.sql" >/dev/null 2>&1 || true

database_rows=""
for dump in "$tree"/postgres/*.dump; do
    db=$(basename "$dump" .dump)
    case "$db" in *[!A-Za-z0-9_]*) fail 98 "refusing an unexpected database name '$db'";; esac
    if [ "$db" = "postgres" ]; then
        # The maintenance database exists on every server; it is restored into, cleanly.
        flags=(--clean --if-exists -d postgres)
    else
        if [ "$mode" = "apply" ]; then
            "$docker_bin" exec "$pg_target" dropdb -U "$pg_user" --if-exists --force "$db" \
                || fail 98 "could not drop $db before restoring it"
        fi
        "$docker_bin" exec "$pg_target" createdb -U "$pg_user" "$db" || fail 98 "could not create $db"
        flags=(-d "$db")
    fi
    "$docker_bin" exec -i "$pg_target" pg_restore -U "$pg_user" --exit-on-error "${flags[@]}" \
        < "$dump" || fail 98 "pg_restore of $db failed"
    "$docker_bin" exec "$pg_target" pg_dump -U "$pg_user" -Fc -d "$db" \
        | "$docker_bin" exec -i "$pg_target" pg_restore --data-only -f - \
        | "$python_bin" "$helper" fingerprint > "$work/$db.restored.json" \
        || fail 98 "the restored $db could not be dumped again"
    "$python_bin" "$helper" compare "$tree/postgres/$db.fingerprint.json" "$work/$db.restored.json" \
        || fail 98 "$db did not restore to the same rows"
    rows=$("$python_bin" -c 'import json,sys; print(sum(t["rows"] for t in json.load(open(sys.argv[1]))["tables"].values()))' "$work/$db.restored.json")
    database_rows="$database_rows\"$db\":$rows,"
    say "database $db: $rows rows, identical to the backup"
done
postgres_s=$(now_s)

# ---- 3. the objects -----------------------------------------------------------------------
object_files=$(find "$tree/minio" -type f | wc -l | tr -d ' ')
if [ "$mode" = "drill" ]; then
    "$docker_bin" run -d --name "$minio_scratch" --network none \
        -e MINIO_ROOT_USER=drill -e MINIO_ROOT_PASSWORD="$(random_secret)" \
        -v "$tree/minio:/restore:ro" "$minio_image" server /data >/dev/null \
        || fail 99 "the scratch MinIO did not start"
    minio_target=$minio_scratch
else
    minio_target=$minio_container
    "$docker_bin" cp "$tree/minio/." "$minio_container:/tmp/pagentos-restore-$$" >/dev/null \
        || fail 99 "the objects could not be copied into the MinIO container"
fi
restore_src=/restore
[ "$mode" = "apply" ] && restore_src="/tmp/pagentos-restore-$$"
load_objects() {
    "$docker_bin" exec "$minio_target" sh -c '
        set -e
        src="$1"; cfg=/tmp/pagentos-restore-mc; out=/tmp/pagentos-restore-out
        rm -rf "$cfg" "$out"; mkdir -p "$cfg" "$out"
        mc --config-dir "$cfg" alias set rs http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
        for bucket in $(ls "$src"); do
            mc --config-dir "$cfg" mb --ignore-existing "rs/$bucket" >/dev/null
            mc --config-dir "$cfg" mirror --quiet --overwrite --remove "$src/$bucket" "rs/$bucket" >/dev/null
            mc --config-dir "$cfg" mirror --quiet "rs/$bucket" "$out/$bucket" >/dev/null
            mkdir -p "$out/$bucket"
        done
        rm -rf "$cfg"
    ' sh "$restore_src"
}
wait_for minio load_objects || fail 99 "the objects could not be loaded into MinIO"
readback="$work/objects-read-back"
mkdir -p "$readback"
"$docker_bin" cp "$minio_target:/tmp/pagentos-restore-out/." "$readback/" >/dev/null \
    || fail 99 "the loaded objects could not be read back out"
"$docker_bin" exec "$minio_target" rm -rf /tmp/pagentos-restore-out "/tmp/pagentos-restore-$$" >/dev/null 2>&1 || true
# Every object that went in came back out of the API byte for byte.
if ! diff -r "$tree/minio" "$readback" >/dev/null; then
    fail 99 "objects read back through MinIO differ from the snapshot's"
fi
objects_s=$(now_s)

# ---- 4. --apply: hand the services back ---------------------------------------------------
if [ "$mode" = "apply" ]; then
    "$docker_bin" start "$temporal_container" >/dev/null || fail 101 "Temporal did not start again"
    exec 9>&-
    "$reconcile_script" --reconcile || fail 101 "the reconcile did not bring a colour back; run it by hand"
fi

finished_s=$(now_s)
report_dir="$backup_root/drills"
mkdir -p "$report_dir"
report="$report_dir/$stamp-$mode.json"
printf '{"mode":"%s","snapshot":"%s","finished_at":"%s","seconds":{"restore":%s,"verify_files":%s,"databases":%s,"objects":%s,"total":%s},"databases":{%s},"objects":%s,"verdict":"passed"}\n' \
    "$mode" "$snapshot" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    $((restored_s - started_s)) $((verified_s - restored_s)) $((postgres_s - verified_s)) \
    $((objects_s - postgres_s)) $((finished_s - started_s)) "${database_rows%,}" "$object_files" \
    > "$report"
if [ "$mode" = "drill" ]; then
    echo "DRILL OK: snapshot ${snapshot:0:12} restored and verified in $((finished_s - started_s))s (report $report)"
else
    echo "RESTORE OK: production now holds snapshot ${snapshot:0:12} (report $report)"
fi
