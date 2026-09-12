#!/usr/bin/env bash
S="$FAKE_STATE"
echo "docker $*" >> "$S/docker.log"
dbdir() { if [ "$1" = "pg" ]; then echo "$S/prod-db"; else echo "$S/db-$1"; fi; }
is_ref() { [[ "$1" =~ ^[A-Za-z][A-Za-z0-9_.-]+:/ ]]; }
case "$1" in
  exec)
    shift
    if [ "$1" = "-i" ]; then c="$2"; shift 2; else c="$1"; shift; fi
    case "$1" in
      pg_dumpall) echo "CREATE ROLE pagentos;"; exit "${FAKE_DUMPALL_EXIT:-0}";;
      pg_isready) exit 0;;
      psql)
        sql=""; prev=""
        for a in "$@"; do [ "$prev" = "-Atc" ] && sql="$a"; prev="$a"; done
        case "$sql" in
          *datname*) for f in "$(dbdir "$c")"/*.sql; do [ -e "$f" ] && basename "$f" .sql; done; exit 0;;
          *alembic_version*) echo "0040_fake"; exit 0;;
          "select 1") echo 1; exit 0;;
          "") cat > /dev/null; exit 0;;
        esac
        exit 0;;
      pg_dump)
        db=""; prev=""
        for a in "$@"; do [ "$prev" = "-d" ] && db="$a"; prev="$a"; done
        [ "${FAKE_PGDUMP_FAIL:-}" = "$db" ] && exit 1
        cat "$(dbdir "$c")/$db.sql" || exit 1
        exit 0;;
      pg_restore)
        case " $* " in *" --data-only "*) cat; exit 0;; esac
        db=""; prev=""
        for a in "$@"; do [ "$prev" = "-d" ] && db="$a"; prev="$a"; done
        mkdir -p "$(dbdir "$c")"
        if [ "${FAKE_RESTORE_DROP_ROW:-}" = "$db" ]; then
          awk 'drop==1 {drop=2; next} /^COPY / && drop==0 {drop=1} {print}' > "$(dbdir "$c")/$db.sql"
        else
          cat > "$(dbdir "$c")/$db.sql"
        fi
        exit 0;;
      createdb) db="${!#}"; mkdir -p "$(dbdir "$c")"; [ -f "$(dbdir "$c")/$db.sql" ] || : > "$(dbdir "$c")/$db.sql"; exit 0;;
      dropdb) db="${!#}"; rm -f "$(dbdir "$c")/$db.sql"; exit 0;;
      rm) shift; for p in "$@"; do case "$p" in -*) ;; *) rm -rf "$S/fs/$c$p";; esac; done; exit 0;;
      mc)
        # mc is on the container's PATH; run the fake one against the container's own tree.
        shift
        FAKE_MC_SCRATCH="$S/fs/$c/data" FAKE_CONTAINER="$c" "$FAKE_MC" "$@"
        exit $?;;
      sh)
        script="$3"; arg="$5"
        case "$script" in
          *"alias set rs"*)
            if [ -f "$S/mount-$c" ] && [ "$arg" = "/restore" ]; then src="$(cat "$S/mount-$c")"; else src="$S/fs/$c$arg"; fi
            out="$S/fs/$c/tmp/pagentos-restore-out"
            rm -rf "$out"; mkdir -p "$out"
            cp -r "$src/." "$out/"
            if [ "${FAKE_READBACK_DROP:-}" = "1" ]; then f=$(find "$out" -type f | head -1); [ -n "$f" ] && rm -f "$f"; fi
            if [ "$c" = "minio" ]; then rm -rf "$S/prod-objects"; mkdir -p "$S/prod-objects"; cp -r "$src/." "$S/prod-objects/"; fi
            exit 0;;
          *mc*)
            [ -n "${FAKE_MIRROR_EXIT:-}" ] && exit "$FAKE_MIRROR_EXIT"
            # The backup's in-container script runs FOR REAL, with only the tools that image
            # has (sh, mc, tr, cut, mkdir, rm, ls, head - no awk, no sed, no find). A fake
            # that mirrored the objects itself hid a backup that silently held none of them.
            shift 3  # drop: sh -c <script>; what is left is the inner $0 and its arguments
            mapped=()
            for a in "$@"; do case "$a" in /*) mapped+=("$S/fs/$c$a");; *) mapped+=("$a");; esac; done
            # A PATH entry must be POSIX-style: "C:/x" would split on the colon into two.
            image_path="$(cygpath -u "$FAKE_MINIO_PATH" 2>/dev/null || printf '%s' "$FAKE_MINIO_PATH")"
            host_path="$PATH"  # assignments in a command prefix apply left to right
            PATH="$image_path" FAKE_HOST_PATH="$host_path" FAKE_STATE="$S" \
              FAKE_MC_SCRATCH="$S/fs/$c/data" MINIO_ROOT_USER=fake MINIO_ROOT_PASSWORD=fake \
              sh -c "$script" "${mapped[@]}"
            exit $?;;
        esac
        exit 0;;
    esac
    exit 0;;
  cp)
    src="$2"; dst="$3"
    if is_ref "$src"; then c="${src%%:*}"; p="${src#*:}"; mkdir -p "$dst"; cp -r "$S/fs/$c${p%/.}/." "$dst/"; exit $?; fi
    if is_ref "$dst"; then c="${dst%%:*}"; p="${dst#*:}"; mkdir -p "$S/fs/$c$p"; cp -r "${src%/.}/." "$S/fs/$c$p/"; exit $?; fi
    exit 1;;
  inspect) echo "image-of-${!#}"; exit 0;;
  run)
    name=""; mount=""; prev=""
    for a in "$@"; do
      [ "$prev" = "--name" ] && name="$a"
      [ "$prev" = "-v" ] && mount="$a"
      prev="$a"
    done
    case "$name" in *-pg-*) mkdir -p "$S/db-$name"; : > "$S/db-$name/postgres.sql";; esac
    [ -n "$mount" ] && printf '%s' "${mount%:/restore:ro}" > "$S/mount-$name"
    echo "fake-container-id"; exit 0;;
  rm|stop|start) exit 0;;
esac
exit 0
