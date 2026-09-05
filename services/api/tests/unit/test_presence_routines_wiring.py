"""Presence and routines, joined up, through the real HTTP surfaces.

Each package has its own unit tests, and both pass in isolation — which is exactly the
situation where a seam breaks without either side noticing. This module drives the whole
path the way the owner's own machine will:

    real observations  ->  a real presence transition  ->  a published owner.* event
                       ->  a presence-triggered routine becomes due
                       ->  conditions evaluated against the REAL presence state
                       ->  a firing recorded, with the reason it fired

The two properties it exists to protect are the ones neither package can assert alone: that
the routine's presence condition is answered by the fusion engine rather than by the caller,
and that an unknown or stale presence stops a routine rather than firing it on a guess.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.presence.engine import PresenceFusionEngine, get_engine, set_engine
from app.routines.models import Routine, RoutineFiring
from app.uistate.publisher import UiStatePublisher, get_publisher, set_publisher
from tests.identity_support import authenticate

ALL_TABLES = [Routine.__table__, RoutineFiring.__table__, ActivityEventRow.__table__]


@pytest.fixture(autouse=True)
def _fresh_singletons():
    """The fusion engine and the UI-state bus are both process-wide."""
    set_engine(PresenceFusionEngine())
    set_publisher(UiStatePublisher())
    yield
    set_engine(PresenceFusionEngine())
    set_publisher(UiStatePublisher())


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ALL_TABLES:
        table.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def client(engine) -> TestClient:
    settings = Settings(_env_file=None)
    runtime = ArtifactRuntime(settings)
    runtime._engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings)
    app.state.artifacts = runtime
    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    return test_client


def _iso(offset_s: float = 0.0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_s)).isoformat().replace("+00:00", "Z")


def _observe(client: TestClient, *, present: bool, awake: str, offset_s: float, source="camera"):
    """One structured observation - the only shape that may cross the boundary."""
    response = client.post(
        "/v1/presence/observations",
        json={
            "person_present": present,
            "presence_confidence": 0.92 if present else 0.88,
            "activity_level": "medium" if present else "none",
            "posture": "upright" if present else "unknown",
            "awake_state": awake,
            "observed_at": _iso(offset_s),
            "source": source,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _establish_presence(client: TestClient, *, present: bool = True) -> None:
    """Enough sustained evidence for the fusion engine to actually assert a state.

    Deliberately several observations over a span: the engine refuses to classify from a
    single one, which is the rule this helper must respect rather than route around.
    """
    for i in range(6):
        _observe(
            client, present=present, awake="awake" if present else "uncertain", offset_s=-30 + i * 5
        )


def _create_presence_routine(client: TestClient, event: str, conditions: list[dict]) -> dict:
    response = client.post(
        "/v1/routines",
        json={
            "name": "Sabah brifingi",
            "trigger_kind": "presence",
            "trigger": {"event": event},
            "conditions": conditions,
            "actions": [{"kind": "voice_briefing", "detail": {"text": "Günaydın."}}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _evaluate(client: TestClient) -> dict:
    response = client.post("/v1/routines/evaluate", json={})
    assert response.status_code == 200, response.text
    return response.json()


def _outcome_for(body: dict, routine_id: str) -> dict | None:
    for outcome in body["outcomes"]:
        if outcome["routine_id"] == routine_id:
            return outcome
    return None


def test_a_real_presence_transition_reaches_a_routine(client) -> None:
    """Observations in one end, a firing out the other, through the real HTTP surfaces."""
    routine = _create_presence_routine(client, "owner.present", conditions=[])

    _establish_presence(client, present=True)

    published = [
        e.state.value for e in get_publisher().tail() if e.state.value.startswith("owner.")
    ]
    assert published, "sustained observations must produce a published owner.* state"

    body = _evaluate(client)
    assert body["checked"] >= 1
    outcome = _outcome_for(body, routine["routine_id"])
    assert outcome is not None, f"the routine was never evaluated: {body}"
    assert outcome["status"] == "triggered", outcome


def test_the_presence_condition_is_answered_by_the_engine_not_the_caller(client) -> None:
    """The caller says nothing about presence; the condition must still be decided."""
    routine = _create_presence_routine(
        client,
        "owner.present",
        conditions=[{"kind": "owner_present", "detail": {"required": True}}],
    )
    _establish_presence(client, present=True)
    assert get_engine().current() is not None

    outcome = _outcome_for(_evaluate(client), routine["routine_id"])
    assert outcome is not None
    assert outcome["status"] == "triggered", outcome
    # And the record says the fact came from perception, not from the request body.
    firings = client.get(f"/v1/routines/{routine['routine_id']}/firings").json()
    reasons = str(firings)
    assert "presence_engine" in reasons, reasons
    assert "caller" not in reasons


def test_an_unknown_presence_skips_rather_than_fires(client) -> None:
    """Nothing observed. A routine needing the owner present must skip - and say why."""
    routine = _create_presence_routine(
        client,
        "owner.present",
        conditions=[{"kind": "owner_present", "detail": {"required": True}}],
    )
    # The trigger still has to see its event, so publish presence and then let the
    # assertion be absent: the engine is reset per test, so `current()` is None here.
    from app.uistate.contract import UiState
    from app.uistate.publisher import publish

    publish(UiState.OWNER_PRESENT, subsystem="presence", status="present")

    outcome = _outcome_for(_evaluate(client), routine["routine_id"])
    assert outcome is not None, "the trigger must fire so the CONDITION is what stops it"
    assert outcome["status"] == "skipped"
    # Skipped because nothing was known - not because something said "away".
    assert "owner_presence_unknown" in (outcome["reason"] or "")


def test_the_eye_disable_path_stops_camera_observations_immediately(client) -> None:
    """Disabling the eye is durable and immediate, not a flag that goes stale."""
    _establish_presence(client, present=True)

    disabled = client.post("/v1/presence/eye/disable", json={"reason": "owner_command"})
    assert disabled.status_code == 200, disabled.text

    refused = client.post(
        "/v1/presence/observations",
        json={
            "person_present": True,
            "presence_confidence": 0.9,
            "activity_level": "medium",
            "posture": "upright",
            "awake_state": "awake",
            "observed_at": _iso(),
            "source": "camera",
        },
    )
    assert refused.status_code >= 400, "a camera observation must be refused while the eye is off"

    states = [e.state.value for e in get_publisher().tail()]
    assert "eye.disabled" in states


def test_a_frame_shaped_payload_never_reaches_the_routine_path(client) -> None:
    """The privacy boundary refuses; it does not redact and then carry on."""
    response = client.post(
        "/v1/presence/observations",
        json={
            "person_present": True,
            "presence_confidence": 0.9,
            "activity_level": "medium",
            "posture": "upright",
            "awake_state": "awake",
            "observed_at": _iso(),
            "source": "camera",
            "frame_base64": "AAAA",
        },
    )
    assert response.status_code >= 400
    assert get_engine().current() is None, "a refused observation must not reach the engine"
