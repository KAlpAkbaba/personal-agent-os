#!/usr/bin/env bash
# Which search engine answers the cloud worker from THIS host's address (ADR-0248).
# Run by the lead on the Cloud Core host, read-only, after the release:
#   infra/docker/cloud-browser/measure-search-engines.sh [--engines bing brave ...] [--json]
# It runs browser_agent.cloud.engine_probe in a THROWAWAY container (`docker run --rm`) from
# the image the production worker already uses, under the limits of compose.fragment.yml,
# with a tmpfs for its data and no volume, broker URL or enrollment token of the production
# worker. At most 12 searches, one attempt each, nothing solved or bypassed.
# It never execs into, stops, restarts, builds or recreates any container, runs no
# `docker compose` command and writes nothing on the host: the table goes to standard
# output; the lead saves it on his own PC as docs/evidence/cloud-search-engines-<date>.md.
set -euo pipefail

IMAGE="pagentos/cloud-browser:local"
# The CPX32 also runs the api: the probe's 2 GiB cap plus headroom must be free first.
MIN_AVAILABLE_KIB=$((2560 * 1024)) # 2.5 GiB
MEMINFO="${PAGENTOS_PROBE_MEMINFO:-/proc/meminfo}"

available_kib="$(awk '/^MemAvailable:/ {print $2}' "$MEMINFO" 2>/dev/null || true)"
if [ -z "$available_kib" ] || [ "$available_kib" -lt "$MIN_AVAILABLE_KIB" ]; then
  echo "refused: available memory ${available_kib:-unknown} KiB is below ${MIN_AVAILABLE_KIB} KiB (2.5 GiB)" >&2
  exit 4
fi

# The values of compose.fragment.yml (a unit test holds them equal).
LIMITS=(
  --memory 2g
  --memory-swap 2g
  --shm-size 2gb
  --pids-limit 512
  --cpus 2
  --init
  --cap-drop ALL
  --security-opt no-new-privileges:true
  --tmpfs /tmp:rw,nosuid,nodev,size=512m,mode=1777
)

if ! digest="$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null)"; then
  echo "image $IMAGE is not on this host; this script does not build it" >&2
  exit 3
fi

if ! docker run --rm "${LIMITS[@]}" --entrypoint python "$IMAGE" -c \
  "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('browser_agent.cloud.engine_probe') else 3)" \
  >/dev/null 2>&1; then
  echo "image $IMAGE does not carry browser_agent.cloud.engine_probe (rebuilt only by ADR-0248 release-order step 2)"
  exit 3
fi

docker run --rm "${LIMITS[@]}" \
  -e "PAGENTOS_PROBE_IMAGE_DIGEST=$digest" \
  --entrypoint python "$IMAGE" -m browser_agent.cloud.engine_probe "$@"
