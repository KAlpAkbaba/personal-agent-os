"""Unit tests for the browser_agent.worker stdio protocol layer.

No browser: these drive :class:`~browser_agent.worker.Worker` in-process
(``_handle_line``/``_handle_exec``/``_execute`` called directly, never a real
subprocess or a real Playwright session) and exercise only capabilities that
need no session (``browser.worker_status``, ``ping``, ``shutdown``) plus the
session-required error path up to (never past) "unknown session" — no
``session_open`` is ever called here, so no browser binary is needed. The
protocol-layer guards (bad line, cancel, size cap, forbidden-key scan) are
exactly what this suite targets; end-to-end real-browser scenarios live under
``tests/browser`` (``-m browser``).
"""

from __future__ import annotations

import asyncio
import json

from browser_agent import policy
from browser_agent.detect import BrowserInfo
from browser_agent.worker import (
    MAX_RESULT_BYTES,
    PROTOCOL_VERSION,
    WORKER_VERSION,
    Worker,
    build_arg_parser,
    cap_result_size,
    redact_forbidden_keys,
)


def _make_worker(tmp_path, monkeypatch, **argv_overrides) -> Worker:
    argv = ["--data-dir", str(tmp_path / "data"), "--channel", "chrome"]
    for key, value in argv_overrides.items():
        argv.extend([f"--{key.replace('_', '-')}", str(value)])
    args = build_arg_parser().parse_args(argv)
    worker = Worker(args)

    async def fake_detect(_channel):
        return BrowserInfo(
            channel="chrome", available=True, version="999.0.0.0", executable_path="/fake/chrome"
        )

    monkeypatch.setattr("browser_agent.worker.detect_browser", fake_detect)
    return worker


def _capture_writes(monkeypatch) -> list[dict]:
    written: list[dict] = []
    monkeypatch.setattr("browser_agent.worker._write_line", written.append)
    return written


class TestHello:
    async def test_hello_shape(self, tmp_path, monkeypatch) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)
        await worker._print_hello()
        assert len(written) == 1
        hello = written[0]
        assert hello["type"] == "hello"
        assert hello["worker_version"] == WORKER_VERSION
        assert hello["protocol_version"] == PROTOCOL_VERSION == 1
        assert hello["capabilities"] == list(policy.CAPABILITIES)
        assert hello["browser"] == {"channel": "chrome", "available": True, "version": "999.0.0.0"}


class TestExecResultRoundTrip:
    async def test_worker_status_succeeds_with_no_sessions(self, tmp_path, monkeypatch) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        await worker._print_hello()
        written = _capture_writes(monkeypatch)
        line = json.dumps(
            {
                "type": "exec",
                "request_id": "r1",
                "capability": "browser.worker_status",
                "payload": {},
                "timeout_ms": 5000,
            }
        )
        await worker._handle_line(line)
        await _drain(worker)
        assert len(written) == 1
        result = written[0]
        assert result == {
            "type": "result",
            "request_id": "r1",
            "ok": True,
            "result": {
                "worker_version": WORKER_VERSION,
                "browser": {
                    "channel": "chrome",
                    "available": True,
                    "version": "999.0.0.0",
                    "alive": True,
                },
                "sessions": [],
                "uptime_s": result["result"]["uptime_s"],  # monotonic, just assert shape below
            },
        }
        assert isinstance(result["result"]["uptime_s"], float | int)

    async def test_ping_replies_pong_with_session_count(self, tmp_path, monkeypatch) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)
        await worker._handle_line(json.dumps({"type": "ping"}))
        assert written == [{"type": "pong", "sessions": 0}]

    async def test_shutdown_sets_flag(self, tmp_path, monkeypatch) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        assert worker._shutting_down is False
        await worker._handle_line(json.dumps({"type": "shutdown"}))
        assert worker._shutting_down is True


class TestValidationErrors:
    async def test_unknown_capability_is_capability_missing(self, tmp_path, monkeypatch) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)
        await worker._handle_line(
            json.dumps(
                {
                    "type": "exec",
                    "request_id": "r2",
                    "capability": "browser.teleport",
                    "payload": {},
                }
            )
        )
        await _drain(worker)
        assert written[0]["ok"] is False
        assert written[0]["error"]["class"] == "capability_missing"

    async def test_malformed_capability_name_is_validation_error(
        self, tmp_path, monkeypatch
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)
        await worker._handle_line(
            json.dumps(
                {
                    "type": "exec",
                    "request_id": "r3",
                    "capability": "Browser.Navigate!",
                    "payload": {},
                }
            )
        )
        await _drain(worker)
        assert written[0]["error"]["class"] == "validation_error"

    async def test_unknown_session_id_is_validation_error(self, tmp_path, monkeypatch) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)
        await worker._handle_line(
            json.dumps(
                {
                    "type": "exec",
                    "request_id": "r4",
                    "capability": "browser.navigate",
                    "payload": {"session_id": "does-not-exist", "url": "https://example.com"},
                }
            )
        )
        await _drain(worker)
        assert written[0]["ok"] is False
        assert written[0]["error"]["class"] == "validation_error"
        assert written[0]["error"]["message"].startswith("unknown session")

    async def test_missing_session_id_is_validation_error(self, tmp_path, monkeypatch) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)
        await worker._handle_line(
            json.dumps(
                {
                    "type": "exec",
                    "request_id": "r5",
                    "capability": "browser.navigate",
                    "payload": {},
                }
            )
        )
        await _drain(worker)
        assert written[0]["error"]["class"] == "validation_error"

    async def test_exec_missing_request_id_is_dropped_not_crashed(
        self, tmp_path, monkeypatch
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)
        await worker._handle_line(
            json.dumps({"type": "exec", "capability": "browser.worker_status"})
        )
        assert written == []  # can't reply without a request_id; must not crash


class TestBadLines:
    async def test_invalid_json_is_ignored_without_crashing(self, tmp_path, monkeypatch) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)
        await worker._handle_line("{not json at all")
        assert written == []
        # the worker must still be usable afterward
        await worker._handle_line(json.dumps({"type": "ping"}))
        assert written == [{"type": "pong", "sessions": 0}]

    async def test_json_array_instead_of_object_is_ignored(self, tmp_path, monkeypatch) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)
        await worker._handle_line(json.dumps([1, 2, 3]))
        assert written == []

    async def test_blank_line_is_ignored(self, tmp_path, monkeypatch) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)
        await worker._handle_line("   \n")
        assert written == []

    async def test_unknown_type_without_request_id_is_dropped(self, tmp_path, monkeypatch) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)
        await worker._handle_line(json.dumps({"type": "mystery"}))
        assert written == []

    async def test_unknown_type_with_request_id_is_validation_error(
        self, tmp_path, monkeypatch
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)
        await worker._handle_line(json.dumps({"type": "mystery", "request_id": "r6"}))
        assert written[0]["ok"] is False
        assert written[0]["error"]["class"] == "validation_error"


class TestCancel:
    async def test_cancel_aborts_in_flight_op_with_cancelled_class(
        self, tmp_path, monkeypatch
    ) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)

        started = asyncio.Event()

        async def slow_execute(capability: str, payload: dict) -> dict:
            started.set()
            await asyncio.sleep(30)
            return {"should": "never get here"}

        worker._execute = slow_execute  # instance-level override; no real capability involved

        await worker._handle_line(
            json.dumps({"type": "exec", "request_id": "slow1", "capability": "x.y", "payload": {}})
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        await worker._handle_line(json.dumps({"type": "cancel", "request_id": "slow1"}))
        await _drain(worker)

        assert len(written) == 1
        assert written[0]["request_id"] == "slow1"
        assert written[0]["ok"] is False
        assert written[0]["error"]["class"] == "cancelled"
        assert written[0]["error"]["retryable"] is False

    async def test_cancel_of_unknown_request_id_is_a_noop(self, tmp_path, monkeypatch) -> None:
        worker = _make_worker(tmp_path, monkeypatch)
        written = _capture_writes(monkeypatch)
        await worker._handle_line(json.dumps({"type": "cancel", "request_id": "never-existed"}))
        assert written == []


class TestResultGuards:
    def test_cap_result_size_truncates_oversized_string_field(self) -> None:
        oversized = {"text": "x" * (MAX_RESULT_BYTES * 2), "url": "https://example.com"}
        capped = cap_result_size(oversized)
        assert capped["truncated"] is True
        assert len(json.dumps(capped).encode("utf-8")) <= MAX_RESULT_BYTES

    def test_cap_result_size_leaves_small_result_untouched(self) -> None:
        small = {"ok": True, "url": "https://example.com"}
        assert cap_result_size(small) == small

    def test_redact_forbidden_keys_catches_normalized_variants(self) -> None:
        # Regression coverage for the documented realtime-session bug: a raw
        # substring check against "api_key" misses "apiKey"/"api-key"/"APIKEY".
        payload = {
            "apiKey": "leak-1",
            "api-key": "leak-2",
            "APIKEY": "leak-3",
            "cookie": "leak-4",
            "Set-Cookie": "leak-5",
            "authorization_header": "leak-6",
            "localStorage": "leak-7",
            "user_password": "leak-8",
            "access_token": "leak-9",
            "safe_field": "kept",
        }
        redacted, found = redact_forbidden_keys(payload)
        assert set(found) == set(payload) - {"safe_field"}
        for key in found:
            assert redacted[key] == "<redacted:forbidden-key>"
        assert redacted["safe_field"] == "kept"

    def test_redact_forbidden_keys_recurses_into_nested_structures(self) -> None:
        payload = {"outer": {"inner_list": [{"token": "leak"}, {"ok": "fine"}]}}
        redacted, found = redact_forbidden_keys(payload)
        assert found == ["token"]
        assert redacted["outer"]["inner_list"][0]["token"] == "<redacted:forbidden-key>"
        assert redacted["outer"]["inner_list"][1]["ok"] == "fine"

    def test_redact_forbidden_keys_no_false_positive_on_unrelated_keys(self) -> None:
        payload = {"url": "https://example.com", "title": "hello", "published_at": "2026-01-01"}
        redacted, found = redact_forbidden_keys(payload)
        assert found == []
        assert redacted == payload


async def _drain(worker: Worker) -> None:
    """Let the worker's spawned exec task(s) finish and their done-callbacks fire."""
    pending = [t for t in worker._inflight.values()]
    if pending:
        await asyncio.wait(pending)
    await asyncio.sleep(0)
