"""Unit tests: /v1/routines REST surface (M18) against injected SQLite.

Mirrors tests/unit/test_goals_routes.py's client fixture pattern: fully offline, real
owner authentication (tests/identity_support). Unlike app.goals.routes at the time it was
written, app.routines.routes is already wired into app.main.create_app, so these tests do
not need to include_router it themselves.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.routines.models import Routine, RoutineFiring
from app.routines.routes import ROUTINES_VERSION
from app.uistate.publisher import UiStatePublisher, set_publisher
from tests.identity_support import authenticate

ALL_TABLES = [Routine.__table__, RoutineFiring.__table__, ActivityEventRow.__table__]


@pytest.fixture(autouse=True)
def _fresh_uistate_publisher():
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


def test_routines_endpoints_require_owner_session(app_and_client) -> None:
    _, test_client = app_and_client
    response = test_client.get("/v1/routines")
    assert response.status_code == 401


def test_get_routines_policy(client: TestClient) -> None:
    response = client.get("/v1/routines/policy")
    assert response.status_code == 200
    body = response.json()
    assert body["routines_version"] == ROUTINES_VERSION
    assert "at" in body["trigger_kinds"]
    assert "schedule" in body["trigger_kinds"]
    assert "presence" in body["trigger_kinds"]
    assert "owner_present" in body["condition_kinds"]
    assert "alarm" in body["action_kinds"]
    assert "owner.returned" in body["presence_trigger_events"]


def _create(client: TestClient, **overrides) -> dict:
    payload: dict = {
        "name": "Sabah rutini",
        "trigger_kind": "at",
        "trigger": {"at": "2026-09-05T07:30:00Z"},
    }
    payload.update(overrides)
    response = client.post("/v1/routines", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_and_get_routine(client: TestClient) -> None:
    created = _create(client)
    assert created["status"] == "armed"
    response = client.get(f"/v1/routines/{created['routine_id']}")
    assert response.status_code == 200
    assert response.json()["name"] == "Sabah rutini"


def test_get_unknown_routine_is_404(client: TestClient) -> None:
    response = client.get("/v1/routines/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_create_routine_rejects_unknown_trigger_kind(client: TestClient) -> None:
    response = client.post(
        "/v1/routines", json={"name": "x", "trigger_kind": "not_a_kind", "trigger": {}}
    )
    assert response.status_code == 422


def test_create_routine_rejects_unknown_condition_kind(client: TestClient) -> None:
    response = client.post(
        "/v1/routines",
        json={
            "name": "x",
            "trigger_kind": "at",
            "trigger": {"at": "2026-09-05T07:30:00Z"},
            "conditions": [{"kind": "owner_mood", "detail": {}}],
        },
    )
    assert response.status_code == 422


def test_create_routine_rejects_malformed_trigger_detail(client: TestClient) -> None:
    """kind is valid but the shape the trigger module requires is missing — validated in
    the service layer (app.routines.triggers), not by pydantic's field_validator."""
    response = client.post(
        "/v1/routines",
        json={"name": "x", "trigger_kind": "at", "trigger": {"at": "not-a-datetime"}},
    )
    assert response.status_code == 422


def test_create_routine_rejects_media_action_without_url(client: TestClient) -> None:
    response = client.post(
        "/v1/routines",
        json={
            "name": "x",
            "trigger_kind": "at",
            "trigger": {"at": "2026-09-05T07:30:00Z"},
            "actions": [{"kind": "media_playback", "detail": {"title": "no url"}}],
        },
    )
    assert response.status_code == 422


def test_list_routines_filters_by_status(client: TestClient) -> None:
    a = _create(client, name="A")
    _create(client, name="B")
    client.post(f"/v1/routines/{a['routine_id']}/cancel")

    cancelled = client.get("/v1/routines", params={"status": "cancelled"}).json()["routines"]
    assert [r["name"] for r in cancelled] == ["A"]

    everything = client.get("/v1/routines").json()["routines"]
    assert len(everything) == 2


def test_cancel_routine(client: TestClient) -> None:
    routine = _create(client)
    response = client.post(f"/v1/routines/{routine['routine_id']}/cancel", json={"reason": "iptal"})
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancel_reason"] == "iptal"


def test_cancel_unknown_routine_is_404(client: TestClient) -> None:
    response = client.post("/v1/routines/00000000-0000-0000-0000-000000000000/cancel")
    assert response.status_code == 404


def test_evaluate_due_fires_and_firings_are_readable(client: TestClient) -> None:
    # The route always evaluates against the real clock — use a trigger far enough in the
    # future that it is unconditionally NOT due, and one far enough in the past that it is
    # unconditionally due, regardless of when this test happens to run.
    not_yet = _create(
        client,
        name="not yet",
        trigger={"at": "2099-01-01T00:00:00Z"},
        actions=[{"kind": "voice_briefing", "detail": {"text": "Günaydın"}}],
    )
    past = _create(
        client, name="already due", trigger={"at": "2020-01-01T00:00:00Z"}
    )
    response = client.post("/v1/routines/evaluate")
    assert response.status_code == 200
    body = response.json()
    fired_ids = {o["routine_id"] for o in body["outcomes"] if o["status"] == "triggered"}
    assert past["routine_id"] in fired_ids
    assert not_yet["routine_id"] not in fired_ids

    firings = client.get(f"/v1/routines/{past['routine_id']}/firings").json()["firings"]
    assert len(firings) == 1
    assert firings[0]["status"] == "triggered"


def test_evaluate_due_with_context_skips_and_records_reason(client: TestClient) -> None:
    due_now = _create(
        client,
        name="conditional",
        trigger={"at": "2020-01-01T00:00:00Z"},
        conditions=[{"kind": "owner_present", "detail": {}}],
    )
    response = client.post(
        "/v1/routines/evaluate", json={"context": {"owner_present": False}}
    )
    assert response.status_code == 200
    outcome = next(
        o for o in response.json()["outcomes"] if o["routine_id"] == due_now["routine_id"]
    )
    assert outcome["status"] == "skipped"
    assert outcome["reason"]
