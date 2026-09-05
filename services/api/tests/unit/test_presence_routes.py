"""Unit tests: /v1/presence REST surface, against injected SQLite.

Mirrors ``tests/unit/test_goals_routes.py``'s client fixture pattern: fully
offline, real owner authentication (``tests/identity_support``), a fresh
in-memory engine per test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import EVENT_TYPE_EYE_DISABLED, EVENT_TYPE_PRESENCE_STATE_CHANGED
from app.main import create_app
from app.presence.engine import PresenceFusionEngine, get_engine, set_engine
from app.uistate.publisher import UiStatePublisher, get_publisher, set_publisher
from tests.identity_support import authenticate

ALL_TABLES = [ActivityEventRow.__table__]

# The HTTP route never accepts a caller-supplied clock (perception must not
# be able to dictate the server's notion of "now" - a deliberate security
# choice, app.presence.service.ingest_observation's docstring), so
# observation timestamps here are anchored to REAL wall-clock time at the
# moment each one is built, not a fixed historical constant - otherwise the
# fusion engine's own TTL/staleness rule (app.presence.engine.FusionPolicy
# .ttl_s) would filter every observation out as stale the moment real time
# drifts away from a hardcoded date.


@pytest.fixture(autouse=True)
def _reset_presence_engine():
    """The fusion engine is a process-wide singleton
    (``app.presence.engine.get_engine``/``set_engine`` - same shape as
    ``app.uistate.publisher``'s bus) - each test must start from a clean
    buffer or an earlier test's observations would leak in."""
    set_engine(PresenceFusionEngine())
    yield
    set_engine(PresenceFusionEngine())


@pytest.fixture(autouse=True)
def _reset_uistate_bus():
    set_publisher(UiStatePublisher())
    yield
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
def artifacts_runtime(engine) -> ArtifactRuntime:
    settings = Settings(_env_file=None)
    runtime = ArtifactRuntime(settings)
    runtime._engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return runtime


@pytest.fixture()
def app_and_client(artifacts_runtime):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.state.artifacts = artifacts_runtime
    return app, TestClient(app)


@pytest.fixture()
def client(app_and_client) -> TestClient:
    app, test_client = app_and_client
    authenticate(app, test_client, settings=Settings(_env_file=None))
    return test_client


def _iso_at(offset_s: float = 0.0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_s)).isoformat().replace("+00:00", "Z")


def _observation(**overrides) -> dict:
    payload = {
        "person_present": True,
        "presence_confidence": 0.9,
        "activity_level": "medium",
        "posture": "upright",
        "awake_state": "awake",
        "observed_at": _iso_at(0.0),
        "source": "camera",
    }
    payload.update(overrides)
    return payload


def _rows(engine) -> list[ActivityEventRow]:
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        return list(session.execute(select(ActivityEventRow)).scalars().all())


# ---------------------------------------------------- auth is never bypassed


def test_presence_endpoints_require_owner_session(app_and_client) -> None:
    _, test_client = app_and_client
    assert test_client.get("/v1/presence/state").status_code == 401
    assert test_client.post("/v1/presence/observations", json=_observation()).status_code == 401
    assert test_client.post("/v1/presence/eye/disable").status_code == 401


def test_presence_never_grants_or_influences_authority(client: TestClient) -> None:
    """M18 spec §2 / §6: perception is never authentication. Build up a
    high-confidence, established presence state through the authenticated
    client, then prove a SEPARATE, unauthenticated client is still refused
    everywhere - the presence engine is process-global, so if presence could
    grant authority this second client would benefit from the first
    client's observations."""
    for offset in (0, 25, 50):
        response = client.post(
            "/v1/presence/observations", json=_observation(observed_at=_iso_at(offset))
        )
        assert response.status_code == 201, response.text

    assertion = get_engine().current()
    assert assertion is not None
    assert assertion.confidence > 0.0  # real, established presence evidence

    # A second client against the SAME app, but WITHOUT a token.
    bare_client = TestClient(client.app)
    response = bare_client.get("/v1/presence/state")
    assert response.status_code == 401


# --------------------------------------------------------- observation intake


def test_ingesting_two_observations_over_enough_span_yields_present(client: TestClient) -> None:
    r1 = client.post("/v1/presence/observations", json=_observation())
    assert r1.status_code == 201
    assert r1.json()["assertion"]["state"] == "unknown"  # rule 1: not yet enough evidence

    r2 = client.post(
        "/v1/presence/observations",
        json=_observation(observed_at=_iso_at(25)),
    )
    body = r2.json()
    assert body["assertion"]["state"] == "present"
    assert body["changed"] is True


def test_a_fake_base64_image_field_is_refused_with_422_and_never_reaches_the_ledger(
    client: TestClient, engine
) -> None:
    payload = _observation()
    payload["frame_base64"] = "irrelevant-should-never-be-read"
    response = client.post("/v1/presence/observations", json=payload)
    assert response.status_code == 422
    assert _rows(engine) == []  # nothing was written - refused at the boundary


def test_only_a_real_transition_writes_a_ledger_row_not_every_observation(
    client: TestClient, engine
) -> None:
    client.post("/v1/presence/observations", json=_observation())
    client.post(
        "/v1/presence/observations",
        json=_observation(observed_at=_iso_at(25)),
    )
    # A third observation confirming the SAME state must not add a second row.
    client.post(
        "/v1/presence/observations",
        json=_observation(observed_at=_iso_at(40)),
    )
    transitions = [
        r for r in _rows(engine) if r.event_type == EVENT_TYPE_PRESENCE_STATE_CHANGED
    ]
    assert len(transitions) == 1
    assert transitions[0].detail_json["to_state"] == "present"


def test_a_meaningful_transition_publishes_the_matching_uistate_event(client: TestClient) -> None:
    client.post("/v1/presence/observations", json=_observation())
    client.post(
        "/v1/presence/observations",
        json=_observation(observed_at=_iso_at(25)),
    )
    current = get_publisher().current()
    assert current is not None
    assert current.state.value == "owner.present"
    assert current.subsystem == "presence"


# --------------------------------------------------------------- the Active Eye


def test_disabling_the_eye_refuses_camera_observations_immediately(client: TestClient) -> None:
    disable = client.post("/v1/presence/eye/disable", json={"reason": "owner asked"})
    assert disable.status_code == 200
    assert disable.json()["eye_enabled"] is False

    refused = client.post("/v1/presence/observations", json=_observation(source="camera"))
    assert refused.status_code == 409

    # Non-camera sources are unaffected - the invariant is about the camera
    # perception pipeline specifically, not presence intake as a whole.
    accepted = client.post(
        "/v1/presence/observations",
        json=_observation(source="input", person_present=True),
    )
    assert accepted.status_code == 201


def test_the_eye_disable_is_durable_and_observable(client: TestClient, engine) -> None:
    client.post("/v1/presence/eye/disable", json={"reason": "test"})

    state = client.get("/v1/presence/state").json()
    assert state["eye_enabled"] is False

    eye_rows = [r for r in _rows(engine) if r.event_type == EVENT_TYPE_EYE_DISABLED]
    assert len(eye_rows) == 1

    current = get_publisher().current()
    assert current is not None
    assert current.state.value == "eye.disabled"


def test_re_enabling_the_eye_allows_camera_observations_again(client: TestClient) -> None:
    client.post("/v1/presence/eye/disable")
    assert client.post(
        "/v1/presence/observations", json=_observation(source="camera")
    ).status_code == 409

    client.post("/v1/presence/eye/enable")
    assert client.post(
        "/v1/presence/observations", json=_observation(source="camera")
    ).status_code == 201


def test_eye_is_enabled_by_default_with_no_prior_action(client: TestClient) -> None:
    state = client.get("/v1/presence/state").json()
    assert state["eye_enabled"] is True


# ------------------------------------------------------------------- policy


def test_presence_policy_reports_the_closed_vocabulary_and_thresholds(client: TestClient) -> None:
    response = client.get("/v1/presence/policy")
    assert response.status_code == 200
    body = response.json()
    assert set(body["sources"]) == {"camera", "input", "voice", "task"}
    assert body["fusion_policy"]["min_observations"] >= 2
    assert "min_rest_duration_s" in body["greeting_policy"]


def test_greeting_evaluate_endpoint_returns_a_decision_shape(client: TestClient) -> None:
    response = client.post("/v1/presence/greeting/evaluate")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["should_greet"], bool)
    assert isinstance(body["reason"], str)
