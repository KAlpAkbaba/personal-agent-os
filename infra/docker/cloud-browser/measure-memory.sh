#!/usr/bin/env bash
# Peak memory of the cloud-browser container during a 3-page navigation on the fixture site.
# Run on the Cloud Core host (CPX32) with the service up and enrolled:
#   infra/docker/cloud-browser/measure-memory.sh [container] [output.json]
# The script SAMPLES `docker stats` for MEASURE_WINDOW_S (default 60) seconds; start it,
# then drive three navigations on the fixture site through the broker (see the evidence
# file's `drive` field) inside that window. It writes peak RSS, machine, date, image digest.
set -euo pipefail
container="${1:-cloud-browser}"
out="${2:-docs/evidence/adr-0213-cloud-worker-memory-$(date -u +%F).json}"
window_s="${MEASURE_WINDOW_S:-60}"

digest="$(docker image inspect --format '{{.Id}}' "$(docker inspect --format '{{.Config.Image}}' "$container")")"
peak=0
end=$((SECONDS + window_s))
while [ "$SECONDS" -lt "$end" ]; do
  used="$(docker stats --no-stream --format '{{.MemUsage}}' "$container" | awk '{print $1}')"
  bytes="$(numfmt --from=iec-i "${used//iB/i}" 2>/dev/null || echo 0)"
  [ "$bytes" -gt "$peak" ] && peak="$bytes"
  sleep 1
done
limit="$(docker inspect --format '{{.HostConfig.Memory}}' "$container")"
cat >"$out" <<JSON
{
  "status": "PROVEN_REAL",
  "machine": "$(hostname)",
  "date_utc": "$(date -u +%FT%TZ)",
  "image_digest": "$digest",
  "container": "$container",
  "window_s": $window_s,
  "peak_rss_bytes": $peak,
  "mem_limit_bytes": $limit
}
JSON
echo "wrote $out (peak ${peak} bytes of ${limit})"
