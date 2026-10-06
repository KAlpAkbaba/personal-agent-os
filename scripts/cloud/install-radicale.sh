#!/usr/bin/env bash
# Prepare the Cloud Core host for the owner's calendar server, Radicale (radicale-stack-ops;
# ADR in team/plans/radicale-stack-ops-adr.md until the lead numbers it).
#
# Usage (root, on the host, once - and again after the password changes):
#   install-radicale.sh
#
# Before it: PAGENTOS_CALDAV_PASSWORD is in /opt/pagentos/.env, put there with
# scripts/cloud/set-cloud-secret.ps1 (72 bytes at most: bcrypt reads no further).
#
# What it does:
#   /mnt/pagentos-data/radicale             the calendar's data folder: uid 10002, 0700
#   /mnt/pagentos-data/radicale-auth        root, 0700
#   /mnt/pagentos-data/radicale-auth/users  `owner:<bcrypt>`: uid 10002, 0600 (the
#                                           container reads it through a read-only bind)
# The bcrypt line is made by the Radicale image's own python, in a container with no network,
# the password handed over on STDIN - never an argument or an environment variable, which
# `ps` and `docker inspect` would show. The image is built from infra/docker/radicale when the
# host does not have it yet. The password is never printed, and never written anywhere else.
#
# Exit: 0 ok; 1 not root; 2 PAGENTOS_CALDAV_PASSWORD is not in the env file (put it there with
# set-cloud-secret.ps1); 70 the password is longer than 72 bytes (bcrypt would cut it; choose a
# shorter one); 71 the Radicale image could not be built; 72 the bcrypt line could not be
# made; 73 the folders or the users file could not be written.

set -Eeuo pipefail

base=${PAGENTOS_BASE:-/opt/pagentos}
data=${PAGENTOS_DATA:-/mnt/pagentos-data}
env_file=${PAGENTOS_ENV_FILE:-$base/.env}
docker_bin=${PAGENTOS_DOCKER:-docker}
chown_bin=${PAGENTOS_CHOWN:-chown}
chmod_bin=${PAGENTOS_CHMOD:-chmod}
image=${PAGENTOS_RADICALE_IMAGE:-pagentos/radicale:local}
context=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/infra/docker/radicale
# The Dockerfile's RADICALE_UID: the user Radicale runs as (test_radicale_stack.py).
radicale_uid=10002

if [[ $EUID -ne 0 && "${PAGENTOS_ALLOW_NONROOT:-0}" != "1" ]]; then
    echo "run as root: the users file is root-prepared and owned by the container's uid" >&2
    exit 1
fi

say() { printf 'install-radicale: %s\n' "$*"; }

# Read the one line, never source the file: sourcing would run whatever else it holds.
password=""
if [ -f "$env_file" ]; then
    password=$(sed -n 's/^PAGENTOS_CALDAV_PASSWORD=//p' "$env_file" | tail -n 1)
    password=${password%$'\r'}
    case "$password" in \"*\") password=${password:1:-1};; \'*\') password=${password:1:-1};; esac
fi
if [ -z "$password" ]; then
    echo "PAGENTOS_CALDAV_PASSWORD is not in $env_file; put it there with scripts/cloud/set-cloud-secret.ps1 and run this again" >&2
    exit 2
fi
length=$(printf '%s' "$password" | wc -c | tr -d ' ')
if [ "$length" -gt 72 ]; then
    echo "PAGENTOS_CALDAV_PASSWORD is $length bytes; bcrypt reads only 72 - set a shorter one with set-cloud-secret.ps1" >&2
    exit 70
fi

if ! mkdir -p "$data/radicale" "$data/radicale-auth" \
    || ! "$chown_bin" "$radicale_uid:$radicale_uid" "$data/radicale" \
    || ! "$chmod_bin" 0700 "$data/radicale" "$data/radicale-auth"; then
    echo "the calendar folders under $data could not be prepared" >&2
    exit 73
fi

if ! "$docker_bin" image inspect "$image" >/dev/null 2>&1; then
    say "building $image from $context"
    "$docker_bin" build -t "$image" "$context" >/dev/null || { echo "the Radicale image could not be built" >&2; exit 71; }
fi

digest=$(printf '%s' "$password" | "$docker_bin" run --rm -i --network none --entrypoint python "$image" -c '
import sys, bcrypt
sys.stdout.write(bcrypt.hashpw(sys.stdin.buffer.read(), bcrypt.gensalt(12)).decode() + "\n")
') || digest=""
password=""
case "$digest" in
    '$2'*) ;;
    *) echo "the bcrypt line could not be made (the image's python answered nothing usable)" >&2; exit 72;;
esac

users="$data/radicale-auth/users"
umask 077
if ! printf 'owner:%s\n' "$digest" > "$users.next" \
    || ! "$chown_bin" "$radicale_uid:$radicale_uid" "$users.next" \
    || ! "$chmod_bin" 0600 "$users.next" \
    || ! mv -f "$users.next" "$users"; then
    rm -f "$users.next"
    echo "the users file $users could not be written" >&2
    exit 73
fi
say "users file written: $users (owner, bcrypt, uid $radicale_uid, 0600)"
say "data folder ready: $data/radicale (uid $radicale_uid, 0700)"
