"""M24 Capability Genesis: the failure matrix (M24_CAPABILITY_GENESIS_SPEC.md
§7): the app down -> ``failed``/``dependency_unavailable``; an off-schema
response -> ``postcondition_failed``; a mutating operation on an unauthorized
asset -> ``awaiting_approval``, and a cancel leaves no registration; approval
by the model's own argument (never the router's recorded turn) is refused; a
genesis run cannot trigger another; the ≤10 min / rate bounds.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from app.actions.confirmation_gate import CONFIRM_SOURCE_VOICE, Confirmation
from app.evolution.errors import EvolutionError
from app.genesis.models import GenesisRun
from app.genesis.service import MAX_RUNS_PER_HOUR_PER_INTERFACE
from tests.fixtures.genesis import counterbox_app
from tests.unit.genesis_stack import authorized, make_stack


def test_app_down_fails_with_dependency_unavailable(tmp_path):
    stack = make_stack(tmp_path)
    # A URL nothing listens on: loopback, an arbitrary high port.
    result = stack.service.request(
        interface_name="ghostbox",
        interface_url="http://127.0.0.1:1/spec",
        operation_id="read",
    )
    assert result["state"] == "failed"
    assert result["error_class"] == "dependency_unavailable"
    assert stack.registry.resolve("ghostbox.read") is None


# --------------------------------------------------------- off-schema fixture


class _BrokenHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        server: _BrokenServer = self.server  # type: ignore[assignment]
        if self.path == "/spec":
            self._send_json(
                {
                    "name": "brokenbox",
                    "base_url": server.base_url,
                    "operations": [
                        {
                            "id": "read",
                            "method": "GET",
                            "path": "/thing",
                            "input_schema": None,
                            "output_schema": {
                                "fields": {"value": "integer"},
                                "required": ["value"],
                            },
                            "side_effect": "read",
                            "idempotent": True,
                        }
                    ],
                    "evidence": {"read_back": "read"},
                }
            )
            return
        if self.path == "/thing":
            # Off-schema: missing the declared required field "value".
            self._send_json({"wrong_field": 1})
            return
        self._send_json({"error": "not_found"}, status=404)


class _BrokenServer(HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str = "127.0.0.1") -> None:
        super().__init__((host, 0), _BrokenHandler)
        self.base_url = f"http://{host}:{self.server_port}"

    @property
    def spec_url(self) -> str:
        return f"{self.base_url}/spec"


@contextmanager
def _serve_broken() -> Iterator[_BrokenServer]:
    server = _BrokenServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_off_schema_response_fails_with_postcondition_failed(tmp_path):
    stack = make_stack(tmp_path)
    with _serve_broken() as server:
        result = stack.service.request(
            interface_name="brokenbox", interface_url=server.spec_url, operation_id="read"
        )
        assert result["state"] == "failed"
        assert result["error_class"] == "postcondition_failed"
        assert stack.registry.resolve("brokenbox.read") is None


# ----------------------------------------------------- awaiting_approval / cancel


def test_unauthorized_mutation_parks_awaiting_approval_and_cancel_leaves_no_registration(
    tmp_path,
):
    stack = make_stack(tmp_path)  # nothing authorized anywhere
    with counterbox_app.serve() as server:
        result = stack.service.request(
            interface_name="counterbox",
            interface_url=server.spec_url,
            operation_id="increment",
            arguments={"by": 1},
            session_id="sess-1",
            turn=3,
        )
        assert result["state"] == "awaiting_approval"
        assert result["approval_required"] is True
        assert result["authority_class"] is None or result["side_effect_class"] == "mutate_external"
        assert stack.registry.resolve("counterbox.increment") is None

        cancelled = stack.service.cancel(uuid.UUID(result["id"]))
        assert cancelled["state"] == "cancelled"
        assert stack.registry.resolve("counterbox.increment") is None

        # A second request for the SAME capability starts a NEW run (the
        # first is terminal) rather than resuming the cancelled one.
        again = stack.service.request(
            interface_name="counterbox",
            interface_url=server.spec_url,
            operation_id="increment",
            arguments={"by": 1},
        )
        assert again["id"] != result["id"]


def test_approval_refused_when_not_bound_to_the_recorded_turn(tmp_path):
    """capability.approve honours ONLY a Confirmation the router recorded for
    THIS session + a turn AFTER the park — never the model's own claim."""
    stack = make_stack(tmp_path)
    with counterbox_app.serve() as server:
        result = stack.service.request(
            interface_name="counterbox",
            interface_url=server.spec_url,
            operation_id="reset",
            session_id="sess-1",
            turn=5,
        )
        assert result["state"] == "awaiting_approval"
        run_id = uuid.UUID(result["id"])

        # (a) A voice confirmation from a DIFFERENT session is refused.
        with pytest.raises(EvolutionError):
            stack.service.approve(
                run_id,
                Confirmation(
                    source=CONFIRM_SOURCE_VOICE,
                    session_id="different-session",
                    turn=6,
                    owner_intent_ok=True,
                ),
            )
        assert stack.registry.resolve("counterbox.reset") is None

        # (b) The SAME session, but the router never resolved this turn to
        # the approval intent (a model tool-call with no owner word behind
        # it) — owner_intent_ok=False must refuse.
        with pytest.raises(EvolutionError):
            stack.service.approve(
                run_id,
                Confirmation(
                    source=CONFIRM_SOURCE_VOICE, session_id="sess-1", turn=6, owner_intent_ok=False
                ),
            )
        assert stack.registry.resolve("counterbox.reset") is None

        # (c) A turn at or before the park itself is refused (must be
        # STRICTLY after).
        with pytest.raises(EvolutionError):
            stack.service.approve(
                run_id,
                Confirmation(
                    source=CONFIRM_SOURCE_VOICE, session_id="sess-1", turn=5, owner_intent_ok=True
                ),
            )
        assert stack.registry.resolve("counterbox.reset") is None

        # (d) The genuine confirmation — same session, a LATER turn, the
        # router's own intent flag true — succeeds.
        approved = stack.service.approve(
            run_id,
            Confirmation(
                source=CONFIRM_SOURCE_VOICE, session_id="sess-1", turn=6, owner_intent_ok=True
            ),
        )
        assert approved["state"] == "verified"
        assert stack.registry.resolve("counterbox.reset") is not None


def test_genesis_run_never_calls_request_recursively():
    """spec §9: "a genesis run may not trigger another". Structural, not just
    behavioural: no internal method calls ``self.request(``."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "app" / "genesis" / "service.py").read_text(
        encoding="utf-8"
    )
    # Every call site of `request(` must be the public entrypoint's own
    # definition or an external caller — never `self.request(` from inside
    # another method.
    assert "self.request(" not in source


# --------------------------------------------------------------------- bounds


def test_rate_bound_refuses_a_fourth_run_within_the_hour(tmp_path):
    stack = make_stack(tmp_path, mutation_authorization=authorized("counterbox"))
    now = datetime.now(UTC)
    with stack.service._session_factory() as session:  # noqa: SLF001 - test seeding
        for i in range(MAX_RUNS_PER_HOUR_PER_INTERFACE):
            session.add(
                GenesisRun(
                    capability_id=f"counterbox.op{i}",
                    operation_id=f"op{i}",
                    state="failed",
                    interface_json={"name": "counterbox"},
                    evidence_json={},
                    created_at=now - timedelta(minutes=5),
                    updated_at=now,
                )
            )
        session.commit()

    with counterbox_app.serve() as server:
        # Refused BEFORE any row/research/build exists — a raised typed
        # error (rate_limited), the same discipline every other genesis
        # choke-point validation follows (app.genesis.interface.parse,
        # AdapterSpec.__post_init__); the voice/REST layer maps it to a
        # refusal receipt exactly like it does for those.
        with pytest.raises(EvolutionError) as excinfo:
            stack.service.request(
                interface_name="counterbox",
                interface_url=server.spec_url,
                operation_id="reset",
            )
        assert str(excinfo.value.error_class) == "rate_limited"
        assert stack.registry.resolve("counterbox.reset") is None


def test_second_identical_request_while_active_returns_same_run_no_second_run(tmp_path):
    """spec §7: "a second identical request while a run is active -> the same
    run's status, no second run"."""
    stack = make_stack(tmp_path, mutation_authorization=authorized("counterbox"))
    now = datetime.now(UTC)
    with stack.service._session_factory() as session:  # noqa: SLF001 - test seeding
        row = GenesisRun(
            capability_id="counterbox.increment",
            operation_id="increment",
            state="testing",
            interface_json={"name": "counterbox"},
            evidence_json={},
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        active_id = row.id

    result = stack.service.request(
        interface_name="counterbox",
        interface_url="http://127.0.0.1:1/spec",
        operation_id="increment",
    )
    assert result["id"] == str(active_id)
    assert result["state"] == "testing"
