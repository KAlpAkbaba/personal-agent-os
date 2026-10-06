#!/bin/sh
# Start Radicale only with a users file (radicale-stack-ops).
#
# Production: /etc/radicale/users is a read-only bind of the file install-radicale.sh wrote;
# it is used as it is. Dev: /etc/radicale is a tmpfs and PAGENTOS_RADICALE_DEV_PASSWORD (a
# fixed dev value, not a secret) is hashed into a fresh line at every start.
#
# Docker creates a DIRECTORY where a bind source is missing. Then, and whenever there is no
# file and no dev password, this stops: Radicale never starts with an empty user list.
set -eu

users=/etc/radicale/users

if [ -f "$users" ]; then
    :
elif [ ! -e "$users" ] && [ -n "${PAGENTOS_RADICALE_DEV_PASSWORD:-}" ]; then
    # The password goes to bcrypt on stdin, never as an argument.
    umask 077
    printf '%s' "$PAGENTOS_RADICALE_DEV_PASSWORD" | python -c '
import sys, bcrypt
digest = bcrypt.hashpw(sys.stdin.buffer.read(), bcrypt.gensalt(12)).decode()
sys.stdout.write("owner:" + digest + "\n")
' > "$users"
else
    echo "radicale: no users file at $users - run scripts/cloud/install-radicale.sh on the host" >&2
    exit 1
fi

exec radicale --config /usr/local/etc/radicale/config "$@"
