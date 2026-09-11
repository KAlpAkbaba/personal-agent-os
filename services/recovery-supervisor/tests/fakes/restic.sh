#!/usr/bin/env bash
S="$FAKE_STATE"
R="$RESTIC_REPOSITORY"
echo "restic [$R] $*" >> "$S/restic.log"
case "$1" in
  version) echo "restic 0.16.4 (fake)"; exit 0;;
  cat) [ -f "$R/config" ]; exit $?;;
  init) mkdir -p "$R/snapshots"; : > "$R/config"; exit 0;;
  backup)
    [ -n "${FAKE_RESTIC_BACKUP_EXIT:-}" ] && exit "$FAKE_RESTIC_BACKUP_EXIT"
    path="${!#}"
    n=$(ls "$R/snapshots" 2>/dev/null | wc -l)
    id=$(printf '%064x' $((n + 1)))
    mkdir -p "$R/snapshots/$id/tree"
    cp -r "$path/." "$R/snapshots/$id/tree/"
    echo '{"message_type":"status","percent_done":1}'
    echo "{\"message_type\":\"summary\",\"snapshot_id\":\"$id\"}"
    exit 0;;
  forget) exit 0;;
  check) exit "${FAKE_RESTIC_CHECK_EXIT:-0}";;
  copy) exit "${FAKE_RESTIC_COPY_EXIT:-0}";;
  snapshots)
    id=$(ls "$R/snapshots" 2>/dev/null | sort | tail -1)
    [ -n "$id" ] && echo "[{\"id\":\"$id\",\"short_id\":\"${id:0:8}\"}]" || echo "[]"
    exit 0;;
  restore)
    ref="$2"; id="${ref%%:*}"; target=""; prev=""
    for a in "$@"; do [ "$prev" = "--target" ] && target="$a"; prev="$a"; done
    [ -d "$R/snapshots/$id/tree" ] || exit 1
    mkdir -p "$target"; cp -r "$R/snapshots/$id/tree/." "$target/"
    [ "${FAKE_TAMPER:-}" = "1" ] && printf 'x' >> "$target/config/opt-pagentos/RELEASE"
    exit 0;;
esac
exit 0
