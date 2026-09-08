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
from types import SimpleNamespace
from typing import Any

import pytest

from app.actions.confirmation_gate import CONFIRM_SOURCE_VOICE, Confirmation
from app.evolution.authorization import StaticAuthorizationProvider
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.task_resumption import DispatchResult
from app.genesis.models import GenesisRun
from app.genesis.service import MAX_RUNS_PER_HOUR_PER_INTERFACE
from tests.fixtures.genesis import counterbox_app
from tests.unit.genesis_stack import authorized, make_stack


def test_app_down_fails_with_dependency_unavailable(tmp_path):
    stack = make_stack(tmp_path)
    # A URL nothing listens on: loopback, an arbitrary high port. High, not port 1 —
    # the reserved/privileged-port rule from the security review would refuse that
    # before the fetch, and this case is about the fetch finding nothing there.
    result = stack.service.request(
        interface_name="ghostbox",
        interface_url="http://127.0.0.1:64998/spec",
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


def test_ten_minute_bound_is_enforced(tmp_path):
    """spec §5: "≤ 10 minutes end to end" — a run started long enough ago is
    refused at the next stage boundary, never allowed to keep building."""
    stack = make_stack(tmp_path)
    ten_minutes_ago = datetime.now(UTC) - timedelta(minutes=11)
    with pytest.raises(EvolutionError) as excinfo:
        stack.service._enforce_time_bound(ten_minutes_ago)  # noqa: SLF001 - the bound itself
    assert str(excinfo.value.error_class) == "rate_limited"
    # A run well inside the bound is unaffected.
    stack.service._enforce_time_bound(datetime.now(UTC))  # noqa: SLF001 - no raise


# ---------------------------------------------------- the second layer of the same gate


def test_verify_refuses_an_empty_comparison_even_if_a_description_slipped_through(tmp_path):
    """The parse-time bound (``app.genesis.interface``) refuses a read-back that shares
    no field with a mutation, so this shape cannot reach the service through the front
    door. The check itself refuses it anyway: "verified" may never mean "nothing
    disagreed" over nothing - the vacuous gate this project refuses everywhere else.
    Reached by calling the check directly with two disjoint outputs, the only way left."""
    stack = make_stack(tmp_path)
    service = stack.service
    run = SimpleNamespace(state="used", evidence_json={})
    service._transition = lambda *a, **k: None  # type: ignore[method-assign]
    service._patch_evidence = lambda *a, **k: None  # type: ignore[method-assign]
    # The read-back is a DIFFERENT operation, so the service really dispatches it; the
    # stub answers with a payload sharing no field with the mutation's own output.
    service.dispatcher = SimpleNamespace(  # type: ignore[assignment]
        dispatch=lambda capability_id, arguments: DispatchResult(
            capability_id=capability_id,
            version="0.1.0",
            skill_version_id=str(uuid.uuid4()),
            output={"z": 100},
        )
    )
    spec = SimpleNamespace(operation=SimpleNamespace(id="bump"))
    interface = SimpleNamespace(name="disjointbox", evidence={"read_back": "peek"})
    with pytest.raises(EvolutionError) as excinfo:
        service._verify(
            run,  # type: ignore[arg-type]
            spec,  # type: ignore[arg-type]
            interface,  # type: ignore[arg-type]
            DispatchResult(
                capability_id="disjointbox.bump",
                version="0.1.0",
                skill_version_id=str(uuid.uuid4()),
                output={"w": 7},
            ),
        )
    assert excinfo.value.error_class == EvolutionErrorClass.POSTCONDITION_FAILED
    assert "verified nothing" in str(excinfo.value)


# ------------------------------------------- the authorization gate (security review)


def test_an_asset_enrolled_with_no_grants_authorizes_no_mutation(tmp_path):
    """The M24 security review's HIGH, in the reviewer's own shape: an asset the owner
    enrolled with ZERO permissions used to authorise a brand-new external mutation,
    because the service asked only whether a row existed and never whether it COVERED
    the grant the adapter declares. It must park at awaiting_approval instead."""
    stack = make_stack(
        tmp_path, mutation_authorization=StaticAuthorizationProvider({"counterbox": {}})
    )
    with counterbox_app.serve() as server:
        result = stack.service.request(
            interface_name="counterbox",
            interface_url=server.spec_url,
            operation_id="increment",
            arguments={"by": 5},
            session_id="s-1",
        )
    assert result["state"] == "awaiting_approval"
    assert result["authority_class"] == "mutating_unauthorized"


def test_a_grant_that_does_not_cover_the_network_permission_authorizes_nothing(tmp_path):
    """A grant for some OTHER host is still not a grant for this one."""
    stack = make_stack(
        tmp_path,
        mutation_authorization=StaticAuthorizationProvider(
            {"counterbox": {"network_permissions": ["10.0.0.7"]}}
        ),
    )
    with counterbox_app.serve() as server:
        result = stack.service.request(
            interface_name="counterbox",
            interface_url=server.spec_url,
            operation_id="increment",
            arguments={"by": 5},
            session_id="s-1",
        )
    assert result["state"] == "awaiting_approval"
    assert result["authority_class"] == "mutating_unauthorized"


def test_a_description_may_not_rename_itself_into_another_assets_authority(tmp_path):
    """The reviewer's PoC in its purest form: the fetched document's own ``name`` is not
    the asset reference. A description that calls itself something other than the
    interface the owner registered is refused outright, and nothing registers."""
    stack = make_stack(tmp_path, mutation_authorization=authorized("mailserver"))
    with counterbox_app.serve() as server:
        result = stack.service.request(
            interface_name="mailserver",  # what the caller and the catalogue say
            interface_url=server.spec_url,  # a document that calls itself "counterbox"
            operation_id="increment",
            arguments={"by": 5},
            session_id="s-1",
        )
    assert result["state"] == "failed"
    assert result["error_class"] == "validation_error"
    assert stack.registry.resolve("mailserver.increment") is None


def test_two_racing_approvals_claim_the_run_once(tmp_path):
    """The M21 lesson restated for M24 (security review, MEDIUM): reading the state,
    checking it in Python and writing it back later let two approvals both pass and both
    dispatch. The conditional UPDATE means exactly one claims the run."""
    stack = make_stack(tmp_path)
    with counterbox_app.serve() as server:
        parked = stack.service.request(
            interface_name="counterbox",
            interface_url=server.spec_url,
            operation_id="increment",
            arguments={"by": 1},
            session_id="s-1",
            turn=1,
        )
        assert parked["state"] == "awaiting_approval"
        run_id = uuid.UUID(parked["id"])
        assert stack.service._claim_approval(run_id, "s-1") is True
        assert stack.service._claim_approval(run_id, "s-2") is False
