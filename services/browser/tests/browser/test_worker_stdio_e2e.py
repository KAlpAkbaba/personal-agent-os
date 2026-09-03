"""One full end-to-end scenario through the real worker subprocess and real stdio.

Every other browser-marked worker scenario (tests/browser/test_worker_e2e.py)
drives :class:`~browser_agent.worker.Worker` in-process for speed. This file
is the one place that actually spawns
``python -m browser_agent.worker`` as a real child process and talks to it
over real stdin/stdout pipes with the real newline-delimited JSON protocol
(contract §7) — proving the CLI entry point, the hello line, the exec/result
envelope, and clean shutdown all work end to end, not just the in-process
dispatch logic.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

_READLINE_TIMEOUT_S = 20.0


async def _read_json_line(stream: asyncio.StreamReader) -> dict:
    raw = await asyncio.wait_for(stream.readline(), timeout=_READLINE_TIMEOUT_S)
    if not raw:
        stderr_tail = ""
        raise AssertionError(f"worker stdout closed unexpectedly{stderr_tail}")
    return json.loads(raw.decode("utf-8"))


async def _send(proc: asyncio.subprocess.Process, obj: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
    await proc.stdin.drain()


async def test_worker_subprocess_full_session_lifecycle(site_url, tmp_path: Path) -> None:
    data_dir = tmp_path / "worker-stdio-data"
    data_dir.mkdir()
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "browser_agent.worker",
        "--data-dir",
        str(data_dir),
        "--channel",
        "chromium",
        "--headless",
        "--allow-private-destinations",
        "--idle-timeout-s",
        "600",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parents[2]),  # services/browser (package root)
    )
    assert proc.stdout is not None
    try:
        hello = await _read_json_line(proc.stdout)
        assert hello["type"] == "hello"
        assert hello["protocol_version"] == 1
        assert "browser.fetch_evidence" in hello["capabilities"]
        assert hello["browser"]["channel"] == "chromium"
        assert hello["browser"]["available"] is True

        await _send(
            proc,
            {
                "type": "exec",
                "request_id": "open1",
                "capability": "browser.session_open",
                "payload": {
                    "session_id": "sess1",
                    "profile": "isolated",
                    "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": False},
                },
                "timeout_ms": 30000,
            },
        )
        opened = await _read_json_line(proc.stdout)
        assert opened == {
            "type": "result",
            "request_id": "open1",
            "ok": True,
            "result": {
                "session_id": "sess1",
                "created": True,
                "channel": "chromium",
                "browser_version": opened["result"]["browser_version"],
                "idle_timeout_s": 600,
                "policy": {"allowed_risk_classes": ["NAVIGATE", "READ"], "visible": False},
            },
        }

        await _send(
            proc,
            {
                "type": "exec",
                "request_id": "nav1",
                "capability": "browser.navigate",
                "payload": {"session_id": "sess1", "url": f"{site_url}/article.html"},
                "timeout_ms": 15000,
            },
        )
        navigated = await _read_json_line(proc.stdout)
        assert navigated["ok"] is True
        assert navigated["result"]["page_kind"] == "ok"
        assert navigated["result"]["http_status"] == 200

        await _send(
            proc,
            {
                "type": "exec",
                "request_id": "ext1",
                "capability": "browser.extract",
                "payload": {"session_id": "sess1", "mode": "metadata"},
                "timeout_ms": 15000,
            },
        )
        extracted = await _read_json_line(proc.stdout)
        assert extracted["ok"] is True
        assert extracted["result"]["metadata"]["publisher"] == "Fixture Times"

        await _send(proc, {"type": "ping"})
        pong = await _read_json_line(proc.stdout)
        assert pong == {"type": "pong", "sessions": 1}

        await _send(
            proc,
            {
                "type": "exec",
                "request_id": "close1",
                "capability": "browser.session_close",
                "payload": {"session_id": "sess1"},
                "timeout_ms": 10000,
            },
        )
        closed = await _read_json_line(proc.stdout)
        assert closed["ok"] is True
        assert closed["result"]["closed"] is True

        await _send(proc, {"type": "shutdown"})
        exit_code = await asyncio.wait_for(proc.wait(), timeout=15)
        assert exit_code == 0
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
