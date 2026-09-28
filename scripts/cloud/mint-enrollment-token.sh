#!/usr/bin/env bash
# Personal Agent OS - mint ONE single-use device enrolment token on the Cloud Core host
# (ADR-0203: a second device is enrolled with its own key, never with a copy of the first).
#
# Why this runs HERE and not on the device being enrolled: POST /v1/devices/enrollment-tokens
# is loopback-only (app/broker/routes.py, _require_loopback). A caller on the tailnet is
# refused with 403 - the guard cannot tell a second owner machine from a stranger that
# reached the tailnet. scripts/complete-device-enrollment.ps1 mints its token against the
# broker the installed agent dials, which worked on the first machine only because that
# broker was still the loopback one; from a second machine it stops at the 403. The trust
# that mints a token is therefore root on the host (Tailscale SSH), the same trust that
# bootstraps and rotates the owner credential.
#
#   ssh -t root@pagentos-core "bash /opt/pagentos/app/scripts/cloud/mint-enrollment-token.sh"
#
# What it does, inside the serving colour's container:
#   owner credential (hidden prompt) -> a short-lived labelled session -> one enrolment
#   token -> the session is revoked again.
# What it prints: the token and its expiry, nothing else. The token is single-use and
# short-lived (broker_enrollment_token_ttl_s, 900 s by default); the credential is never
# printed, never written to disk, and never an argument or an environment variable of any
# process - it crosses into the container on stdin.
#
# Exits: 0 minted; 64 no credential given; 65 no api container is running; 66 the owner
# credential was refused; 67 the token route refused or did not answer.
#
# Env overrides for tests: PAGENTOS_EDGE_DIR (default /mnt/pagentos-data/edge),
# PAGENTOS_API_PORT (default 8001), PAGENTOS_API_CONTAINER (skip the colour lookup).
set -eu -o pipefail

edge_dir=${PAGENTOS_EDGE_DIR:-/mnt/pagentos-data/edge}
port=${PAGENTOS_API_PORT:-8001}
label=${1:-second-device-enrolment}

running() {
    # No pipe into a truncating reader (pipefail; see release-cloud-core-bluegreen.sh).
    local listed
    listed="$(docker ps --format '{{.Names}}' 2>/dev/null || true)"
    grep -qx "$1" <<<"$listed"
}

container=${PAGENTOS_API_CONTAINER:-}
if [ -z "$container" ]; then
    colour=""
    if [ -f "$edge_dir/active.txt" ]; then
        colour="$(tr -d '[:space:]' < "$edge_dir/active.txt" | tr '[:upper:]' '[:lower:]')"
    fi
    # The active colour first; the single-container topology only when no colour serves.
    for candidate in ${colour:+"pagentos-prod-api-$colour"} pagentos-prod-api; do
        if running "$candidate"; then container="$candidate"; break; fi
    done
fi
if [ -z "$container" ]; then
    echo "no api container is running (looked for the active colour and pagentos-prod-api)" >&2
    exit 65
fi

if [ -t 0 ]; then
    printf 'Owner credential (input hidden): ' >&2
    IFS= read -rs credential
    printf '\n' >&2
else
    IFS= read -r credential || true
fi
if [ -z "${credential:-}" ]; then
    echo "no credential given; nothing was minted" >&2
    exit 64
fi

# The whole conversation is one python process inside the container, so the session token
# exists only in that process. Status lines go to stderr; stdout carries the token alone.
code='
import json, sys, urllib.request, urllib.error

port, label = sys.argv[1], sys.argv[2]
base = "http://127.0.0.1:%s" % port
credential = sys.stdin.readline().rstrip("\r\n")


def post(path, body, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(base + path, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode() or "{}")


try:
    session = post(
        "/v1/identity/sessions",
        {"owner_credential": credential, "client_kind": "cli", "label": label, "ttl_s": 300},
    )
except urllib.error.HTTPError as exc:
    sys.stderr.write("the owner credential was refused (HTTP %d)\n" % exc.code)
    sys.exit(66)
except Exception as exc:
    sys.stderr.write("the api did not answer on %s: %s\n" % (base, type(exc).__name__))
    sys.exit(67)
finally:
    credential = None

status = 0
try:
    minted = post("/v1/devices/enrollment-tokens", None, session["token"])
    sys.stdout.write("token      %s\nexpires_at %s\n" % (minted["token"], minted["expires_at"]))
except urllib.error.HTTPError as exc:
    sys.stderr.write("the enrolment-token route refused (HTTP %d)\n" % exc.code)
    status = 67
except Exception as exc:
    sys.stderr.write("the enrolment-token route did not answer: %s\n" % type(exc).__name__)
    status = 67
finally:
    # The session was for this one call. A revoke that fails is said, not hidden: the
    # session still ends by its own ttl (300 s).
    try:
        post("/v1/identity/sessions/%s/revoke" % session["session_id"], None, session["token"])
        sys.stderr.write("session %s revoked\n" % session["session_id"])
    except Exception:
        sys.stderr.write("session %s NOT revoked; it expires within 300 s\n" % session["session_id"])
sys.exit(status)
'

echo "minting in $container (label: $label)" >&2
rc=0
printf '%s\n' "$credential" | docker exec -i "$container" python3 -c "$code" "$port" "$label" || rc=$?
credential=""
if [ "$rc" -ne 0 ]; then
    echo "nothing was minted (exit $rc)" >&2
    exit "$rc"
fi
echo "single-use; enrol the device with it before it expires. It is not stored anywhere." >&2
