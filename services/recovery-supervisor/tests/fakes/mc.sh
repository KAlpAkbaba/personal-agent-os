#!/bin/bash
# A static binary in the real image: it is not limited to the image's tool set.
[ -n "${FAKE_HOST_PATH:-}" ] && PATH="$FAKE_HOST_PATH"
# The MinIO client, as much of it as the backup and restore scripts use. Buckets are
# directories under FAKE_STATE/prod-objects (alias "bk", the production instance) or under
# the scratch instance's own root (alias "rs", set by the restore drill).
#
# It exists so the scripts' own in-container shell can run for real with only the tools that
# image has - the backup used to parse `mc ls` with awk, which that image does not have, and
# a fake that copied the objects itself could never have shown it.
set -e
cfg=""
args=()
flags=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --config-dir) cfg="$2"; shift 2;;
        --*) flags+=("$1"); shift;;
        *) args+=("$1"); shift;;
    esac
done
has() { case " ${flags[*]} " in *" $1 "*) return 0;; *) return 1;; esac; }

root_of() {
    case "$1" in
        bk) printf '%s' "$FAKE_STATE/prod-objects";;
        rs) printf '%s' "$FAKE_MC_SCRATCH";;
        *) printf '%s' "$FAKE_STATE/prod-objects";;
    esac
}

case "${args[0]}" in
    alias)
        # alias set <name> <url> <user> <pass>
        [ -n "$cfg" ] && mkdir -p "$cfg"
        [ "${args[2]}" = "rs" ] && mkdir -p "$FAKE_MC_SCRATCH"
        exit 0;;
    mb)
        target="${args[1]}"           # rs/<bucket>
        mkdir -p "$(root_of "${target%%/*}")/${target#*/}"
        exit 0;;
    ls)
        alias_name="${args[1]}"
        root="$(root_of "$alias_name")"
        for bucket in "$root"/*; do
            [ -d "$bucket" ] || continue
            name="$(basename "$bucket")"
            if has --json; then
                printf '{"status":"success","type":"folder","key":"%s/","size":0}\n' "$name"
            else
                printf '[2026-09-12 07:00:00 UTC]     0B %s/\n' "$name"
            fi
        done
        exit 0;;
    mirror)
        src="${args[1]}"; dst="${args[2]}"
        case "$src" in
            */*) srcdir="$(root_of "${src%%/*}")/${src#*/}";;
            *) srcdir="$src";;
        esac
        case "$dst" in
            bk/*|rs/*) dstdir="$(root_of "${dst%%/*}")/${dst#*/}";;
            *) dstdir="$dst";;
        esac
        mkdir -p "$dstdir"
        [ -d "$srcdir" ] || exit 0
        cp -r "$srcdir/." "$dstdir/"
        exit 0;;
esac
exit 0
