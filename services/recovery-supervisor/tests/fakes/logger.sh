#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$FAKE_STATE/$(basename "$0").log"
if [ "$(basename "$0")" = "flock" ]; then exit "${FAKE_FLOCK_EXIT:-0}"; fi
if [ "$(basename "$0")" = "apt-get" ] && [ "$1" = "install" ] && [ -n "${FAKE_APT_PROVIDES:-}" ]; then
  cp "$FAKE_STATE/bin/restic" "$FAKE_APT_PROVIDES"; chmod +x "$FAKE_APT_PROVIDES"
fi
exit 0
