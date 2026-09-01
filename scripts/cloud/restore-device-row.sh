#!/usr/bin/env bash
# Personal Agent OS — register the ALREADY-ENROLLED Windows device in the cloud database.
#
# The Windows device keeps its identity (device id + P-256 keypair) across the move from
# the loopback broker to the cloud one; only the broker's DATABASE ROW is missing on the
# new host. This recreates it from the device's own identity document — the same
# restore_device_registration.py flow that already ran once on the local machine — so
# there is NO re-enrollment and NO new key.
#
# Input: the JSON document printed by the Windows identity helper
# (`PagentOS.DeviceService identity` — non-secret by construction: device id, name,
# broker url, enrolled_at, PUBLIC key). Get it on the Windows machine with:
#
#   "C:\Program Files\PagentOS\agent\service\PagentOS.DeviceService.exe" identity > identity.json
#
# and copy it here over the tailnet.
#
# Usage (as the admin user, repo root on the VPS): sudo ./scripts/cloud/restore-device-row.sh identity.json

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="/opt/pagentos/.env"
COMPOSE_FILE="$REPO_ROOT/infra/docker/docker-compose.prod.yml"

if [[ $# -ne 1 || ! -f "$1" ]]; then
    echo "usage: $0 <identity.json from the Windows identity helper>" >&2
    exit 2
fi

device_id="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['device_id'])" "$1")"
name="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['name'])" "$1")"
spki="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['public_key_spki_b64'])" "$1")"

echo "restoring device row: device_id=$device_id name=$name (public key only; nothing secret in transit)"

docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" run --rm --no-deps \
    --entrypoint "uv" api run python scripts/restore_device_registration.py \
    --device-id "$device_id" --name "$name" --public-key-spki-b64 "$spki"

echo "done. Point the Windows agent at this broker with scripts\\switch-agent-broker.ps1 (elevated, on the Windows machine)."
