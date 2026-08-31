"""Trace ID middleware + structured logging unit tests."""

import json

import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.logging import (
    add_correlation_ids,
    get_logger,
    task_id_var,
    trace_id_var,
)
from app.middleware import TRACE_HEADER, TraceIdMiddleware, sanitize_trace_id


def build_test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)

    @app.get("/echo-trace")
    def echo_trace() -> dict[str, str | None]:
        return {"trace_id_in_context": trace_id_var.get()}

    return app


def test_incoming_trace_id_is_honored_and_echoed() -> None:
    client = TestClient(build_test_app())
    response = client.get("/echo-trace", headers={TRACE_HEADER: "trace-abc-123"})
    assert response.status_code == 200
    assert response.headers[TRACE_HEADER] == "trace-abc-123"
    assert response.json()["trace_id_in_context"] == "trace-abc-123"


def test_trace_id_is_generated_when_absent() -> None:
    client = TestClient(build_test_app())
    response = client.get("/echo-trace")
    generated = response.headers[TRACE_HEADER]
    assert len(generated) == 32  # uuid4().hex
    assert response.json()["trace_id_in_context"] == generated


def test_oversized_trace_id_is_truncated_to_column_limit() -> None:
    client = TestClient(build_test_app())
    response = client.get("/echo-trace", headers={TRACE_HEADER: "x" * 500})
    assert response.headers[TRACE_HEADER] == "x" * 128


def test_hostile_trace_id_characters_are_stripped() -> None:
    assert sanitize_trace_id("abc<script>'; DROP--") == "abcscriptDROP--"
    assert sanitize_trace_id("   ") is None
    assert sanitize_trace_id(None) is None
    client = TestClient(build_test_app())
    response = client.get("/echo-trace", headers={TRACE_HEADER: "!!!@@@###"})
    # nothing usable remains -> a fresh uuid is generated instead
    assert len(response.headers[TRACE_HEADER]) == 32


def test_two_requests_get_distinct_trace_ids() -> None:
    client = TestClient(build_test_app())
    first = client.get("/echo-trace").headers[TRACE_HEADER]
    second = client.get("/echo-trace").headers[TRACE_HEADER]
    assert first != second


def test_log_lines_carry_trace_and_task_ids(capsys) -> None:
    structlog.configure(
        processors=[add_correlation_ids, structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    trace_token = trace_id_var.set("trace-log-1")
    task_token = task_id_var.set("task-log-9")
    try:
        get_logger("test").info("something_happened", detail=42)
    finally:
        trace_id_var.reset(trace_token)
        task_id_var.reset(task_token)
    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["trace_id"] == "trace-log-1"
    assert payload["task_id"] == "task-log-9"
    assert payload["event"] == "something_happened"
    assert payload["detail"] == 42


def test_task_id_defaults_to_none_in_logs(capsys) -> None:
    structlog.configure(
        processors=[add_correlation_ids, structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    get_logger("test").info("no_context")
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["task_id"] is None
    assert payload["trace_id"] is None
