#!/usr/bin/env bash
# Personal Agent OS — deploy the Cloud Core on the Hetzner host (release action, RQ-2).
#
# Runs ON the VPS (over Tailscale SSH), from a checkout of this repository. First run
# bootstraps /opt/pagentos/.env with GENERATED secrets (root-owned, 0600, never printed,
# never in git); later runs reuse it untouched. Per OPERATIONS.md the initial release
# path builds the image on the host from the checked-out source; CI→GHCR images replace
# the build step later without changing anything else here (ADR-0032).
#
# Deliberately NOT done here: identity bootstrap (one owner action, once, via the
# loopback guard) and device-row restoration (scripts/cloud/restore-device-row.sh, which
# needs the device's identity document from the Windows machine).
#
# Usage (as the admin user, from the repo root on the VPS):
#   sudo ./scripts/cloud/deploy-cloud-core.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="/opt/pagentos/.env"
COMPOSE_FILE="$REPO_ROOT/infra/docker/docker-compose.prod.yml"

if [[ $EUID -ne 0 ]]; then
    echo "run with sudo: the env file and data directories are root-owned" >&2
    exit 1
fi

# ------------------------------------------------------------------ tailnet address
# The API binds the Tailscale IP and nothing else. No tailnet, no deployment.
TAILNET_IP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
if [[ -z "$TAILNET_IP" ]]; then
    echo "this host is not on the tailnet (tailscale ip -4 returned nothing); join it first" >&2
    exit 1
fi
echo "tailnet address: $TAILNET_IP"

# ------------------------------------------------------------------ persistent data
for dir in postgres minio identity; do
    mkdir -p "/mnt/pagentos-data/$dir"
done
# The identity dir is mounted into the container where uid 10001 runs the app.
chown 10001:10001 /mnt/pagentos-data/identity
chmod 700 /mnt/pagentos-data/identity

# ------------------------------------------------------------------ secrets (once)
if [[ ! -f "$ENV_FILE" ]]; then
    echo "first deployment: generating $ENV_FILE (secrets are generated, not printed)"
    mkdir -p /opt/pagentos
    umask 077
    cat > "$ENV_FILE" <<EOF
PAGENTOS_BIND_IP=$TAILNET_IP
PAGENTOS_DB_PASSWORD=$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-40)
PAGENTOS_S3_SECRET=$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-40)
PAGENTOS_VOICE_PROFILE_SECRET=$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-40)
PAGENTOS_WEB_ORIGINS=
EOF
    chmod 600 "$ENV_FILE"
else
    echo "reusing existing $ENV_FILE"
    # The tailnet IP can change if the node was re-added; keep the binding honest.
    current_ip="$(grep '^PAGENTOS_BIND_IP=' "$ENV_FILE" | cut -d= -f2)"
    if [[ "$current_ip" != "$TAILNET_IP" ]]; then
        echo "tailnet IP changed ($current_ip -> $TAILNET_IP); updating the binding"
        sed -i "s/^PAGENTOS_BIND_IP=.*/PAGENTOS_BIND_IP=$TAILNET_IP/" "$ENV_FILE"
    fi
fi

# ------------------------------------------------------------------ build + migrate + up
cd "$REPO_ROOT/infra/docker"

echo "building the Cloud Core image..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build api

echo "starting infrastructure..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --wait postgres redis minio temporal

echo "applying migrations..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" run --rm --no-deps \
    --entrypoint "uv" api run alembic upgrade head

echo "starting the API..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --wait api

# ------------------------------------------------------------------ health, on both faces
for target in "127.0.0.1" "$TAILNET_IP"; do
    status="$(curl -fsS "http://$target:8001/v1/system/health" | head -c 200 || true)"
    if [[ "$status" != *'"status"'* ]]; then
        echo "health check FAILED on $target:8001" >&2
        exit 1
    fi
    echo "health ok on $target:8001"
done

echo ""
echo "Cloud Core is up, tailnet-only ($TAILNET_IP:8001; loopback for host-side recovery)."
echo "Remaining one-time steps, in order:"
echo "  1. owner identity bootstrap (ONE owner action, on this host, loopback guard):"
echo "     curl -X POST http://127.0.0.1:8001/v1/identity/bootstrap   # credential shown ONCE"
echo "  2. restore the Windows device row (no re-enrollment):"
echo "     ./scripts/cloud/restore-device-row.sh <identity.json from the Windows helper>"
