"""Owner-machine qualification runner for the M13 browser-research pipeline.

Mints nothing itself — the owner session token is read from STDIN (never an
argument, never printed, never logged), exactly like every other qualification
script in this repo. Starts ``POST /v1/research`` for a topic (default the
first owner use case), optionally polls ``GET /v1/research/{task_id}`` until
the run reaches a terminal stage, and prints progress lines followed by the
final report JSON on success.

Usage (from services/api):

    echo <owner-session-token> | uv run python scripts/research_smoke.py \\
        --api-base http://127.0.0.1:8001 [--topic "..."] [--device ev] \\
        [--synthesis auto|deterministic] [--wait] [--timeout 600]

Exit codes: 0 ready; 2 failed; 3 no_capable_device; 4 timeout (``--wait`` only
— without it the script returns as soon as the run is accepted, exit 0).
stdout carries progress lines (stderr-safe to redirect) and, at the end, the
report JSON as the LAST line, so ``... | tail -1 | jq`` extracts it cleanly.
UTF-8 throughout (Turkish characters print correctly on Windows PowerShell
when its console codepage is UTF-8; this script always encodes stdout as
UTF-8 regardless of the console).
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from typing import Any

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", newline="")

EXIT_READY = 0
EXIT_FAILED = 2
EXIT_NO_DEVICE = 3
EXIT_TIMEOUT = 4

DEFAULT_TOPIC = "Son üç gündeki yapay zekâ ajanlarıyla ilgili önemli gelişmeleri araştır."
TERMINAL_STAGES = ("ready", "failed", "cancelled")


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _read_token() -> str:
    token = sys.stdin.readline().strip()
    if not token:
        _log("research_smoke: no owner session token on stdin")
        raise SystemExit(EXIT_FAILED)
    return token


def _client(api_base: str, token: str):
    import httpx

    return httpx.Client(
        base_url=api_base.rstrip("/"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30.0,
    )


def start_research(
    client: Any, *, topic: str, device: str | None, synthesis: str
) -> dict[str, Any]:
    import httpx

    body: dict[str, Any] = {"input": topic, "synthesis": synthesis}
    if device:
        body["target_device"] = device
    resp = client.post("/v1/research", json=body)
    if resp.status_code == 409:
        detail = resp.json().get("detail", {})
        _log(f"research_smoke: no_capable_device: {detail.get('detail')}")
        raise SystemExit(EXIT_NO_DEVICE)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _log(f"research_smoke: start failed: {exc}")
        raise SystemExit(EXIT_FAILED) from exc
    return resp.json()


def poll_research(client: Any, task_id: str, *, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_line = ""
    while time.monotonic() < deadline:
        resp = client.get(f"/v1/research/{task_id}")
        resp.raise_for_status()
        record = resp.json()
        stage = record.get("stage")
        progress = record.get("progress") or {}
        line = (
            f"stage={stage} discovered={progress.get('discovered')} "
            f"fetch_done={progress.get('fetch_done')} evidence={progress.get('evidence')}"
        )
        if line != last_line:
            _log(line)
            last_line = line
        if stage in TERMINAL_STAGES:
            return record
        time.sleep(3)
    _log(f"research_smoke: not terminal within {timeout_s:.0f}s")
    raise SystemExit(EXIT_TIMEOUT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--device", default=None, help="device id, exact name, or Turkish alias")
    parser.add_argument("--synthesis", default="auto", choices=("auto", "deterministic"))
    parser.add_argument("--wait", action="store_true", help="poll until the run is terminal")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)

    token = _read_token()
    client = _client(args.api_base, token)
    try:
        started = start_research(
            client, topic=args.topic, device=args.device, synthesis=args.synthesis
        )
        task_id = started["task_id"]
        _log(f"research_smoke: started task_id={task_id} device={started.get('device')}")

        if not args.wait:
            print(json.dumps(started, ensure_ascii=False))
            return EXIT_READY

        record = poll_research(client, task_id, timeout_s=args.timeout)
        stage = record.get("stage")
        if stage == "ready":
            print(json.dumps(record.get("report"), ensure_ascii=False))
            return EXIT_READY
        if stage == "failed":
            error = record.get("error") or {}
            _log(f"research_smoke: failed: {error}")
            if error.get("error_class") == "no_capable_device":
                return EXIT_NO_DEVICE
            return EXIT_FAILED
        _log(f"research_smoke: ended in unexpected stage {stage!r}")
        return EXIT_FAILED
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
