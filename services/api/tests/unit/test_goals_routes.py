"""Unit tests: /v1/goals REST surface (overnight plan Phase 4) against
injected SQLite. Mirrors tests/unit/test_ledger_routes.py's client fixture
pattern: fully offline, real owner authentication (tests/identity_support).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Task
from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.goals.models import Goal, GoalTask
from app.goals.routes import GOALS_VERSION
from app.goals.routes import router as goals_router
from app.ledger.models import ActivityEventRow
from app.main import create_app
from tests.identity_support import authenticate, install_identity

ALL_TABLES = [
    Goal.__table__,
    GoalTask.__table__,
    Task.__table__,
    ActivityEventRow.__table__,
]


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
    # Replace create_app()'s real-database identity runtime BEFORE any request: an
    # unauthenticated call audits its refusal, and that audit must land in SQLite, not
    # dial the compose Postgres (tests/unit/conftest.py).
    install_identity(app, settings=settings)
    app.state.artifacts = artifacts_runtime
    # Not yet wired into app/main.py (integrator's job per task instructions);
    # included here at test time only, exactly like production wiring will.
    app.include_router(goals_router)
    return app, TestClient(app)


@pytest.fixture()
def client(app_and_client) -> TestClient:
    app, test_client = app_and_client
    authenticate(app, test_client, settings=Settings(_env_file=None))
    return test_client


def test_goals_endpoints_require_owner_session(app_and_client) -> None:
    _, test_client = app_and_client
    response = test_client.get("/v1/goals")
    assert response.status_code == 401


def test_get_goals_policy(client: TestClient) -> None:
    response = client.get("/v1/goals/policy")
    assert response.status_code == 200
    body = response.json()
    assert body["goals_version"] == GOALS_VERSION
    assert "active" in body["statuses"]
    assert "someday" in body["horizons"]
    assert "manual_evidence" in body["check_kinds"]


def _create(client: TestClient, **overrides) -> dict:
    payload = {"title": "Ev ağını yükselt", "intent": "Wi-Fi 6E ile değiştir."}
    payload.update(overrides)
    response = client.post("/v1/goals", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_and_get_goal(client: TestClient) -> None:
    created = _create(client)
    assert created["status"] == "draft"
    response = client.get(f"/v1/goals/{created['goal_id']}")
    assert response.status_code == 200
    assert response.json()["title"] == "Ev ağını yükselt"


def test_get_unknown_goal_is_404(client: TestClient) -> None:
    response = client.get("/v1/goals/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_create_goal_rejects_unknown_horizon(client: TestClient) -> None:
    response = client.post(
        "/v1/goals", json={"title": "x", "intent": "y", "horizon": "not_a_horizon"}
    )
    assert response.status_code == 422


def test_create_goal_rejects_unknown_check_kind(client: TestClient) -> None:
    response = client.post(
        "/v1/goals",
        json={
            "title": "x",
            "intent": "y",
            "success_criteria": [{"statement": "z", "check_kind": "nope"}],
        },
    )
    assert response.status_code == 422


def test_list_goals_filters_by_status(client: TestClient) -> None:
    a = _create(client, title="A")
    _create(client, title="B")
    client.post(f"/v1/goals/{a['goal_id']}/status", json={"status": "active"})

    active = client.get("/v1/goals", params={"status": "active"}).json()["goals"]
    assert [g["title"] for g in active] == ["A"]

    everything = client.get("/v1/goals").json()["goals"]
    assert len(everything) == 2


def test_update_status_happy_path(client: TestClient) -> None:
    goal = _create(client)
    response = client.post(f"/v1/goals/{goal['goal_id']}/status", json={"status": "active"})
    assert response.status_code == 200
    assert response.json()["status"] == "active"


def test_update_status_illegal_transition_is_409(client: TestClient) -> None:
    goal = _create(client)
    response = client.post(f"/v1/goals/{goal['goal_id']}/status", json={"status": "achieved"})
    assert response.status_code == 409


def test_update_status_unknown_status_is_422(client: TestClient) -> None:
    goal = _create(client)
    response = client.post(f"/v1/goals/{goal['goal_id']}/status", json={"status": "nope"})
    assert response.status_code == 422


def test_update_status_cannot_clear_the_owner_gate(client: TestClient) -> None:
    goal = _create(client, requires_owner_approval=True)
    client.post(f"/v1/goals/{goal['goal_id']}/status", json={"status": "active"})
    client.post(f"/v1/goals/{goal['goal_id']}/status", json={"status": "waiting_owner"})
    response = client.post(f"/v1/goals/{goal['goal_id']}/status", json={"status": "active"})
    assert response.status_code == 403


def test_approve_clears_the_owner_gate(client: TestClient) -> None:
    goal = _create(client, requires_owner_approval=True)
    client.post(f"/v1/goals/{goal['goal_id']}/status", json={"status": "active"})
    client.post(f"/v1/goals/{goal['goal_id']}/status", json={"status": "waiting_owner"})
    response = client.post(f"/v1/goals/{goal['goal_id']}/approve")
    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert response.json()["approved_at"] is not None


def test_evaluate_reports_unsatisfied_then_satisfied_after_evidence(
    client: TestClient, artifacts_runtime: ArtifactRuntime
) -> None:
    goal = _create(
        client,
        success_criteria=[{"statement": "kanıtlandı", "check_kind": "manual_evidence"}],
    )
    client.post(f"/v1/goals/{goal['goal_id']}/status", json={"status": "active"})

    first = client.post(f"/v1/goals/{goal['goal_id']}/evaluate")
    assert first.status_code == 200
    assert first.json()["all_satisfied"] is False
    assert first.json()["goal"]["status"] == "active"

    criterion_id = goal["success_criteria"][0]["id"]
    # No HTTP route records evidence in this phase; drive it through the
    # service layer the same way the Cognitive Core would, then re-evaluate
    # through the route to confirm the route reflects real evidence.
    import uuid as uuid_module

    from app.goals import service as goals_service

    with artifacts_runtime.session() as session:
        goals_service.record_evidence(
            session, uuid_module.UUID(goal["goal_id"]), criterion_id, {"kind": "test", "ref": "1"}
        )

    second = client.post(f"/v1/goals/{goal['goal_id']}/evaluate")
    assert second.json()["all_satisfied"] is True
    assert second.json()["goal"]["status"] == "achieved"


def test_add_dependency_refuses_cycle(client: TestClient) -> None:
    a = _create(client, title="A")
    b = _create(client, title="B")
    ok = client.post(
        f"/v1/goals/{a['goal_id']}/dependencies", json={"depends_on_goal_id": b["goal_id"]}
    )
    assert ok.status_code == 200
    cycle = client.post(
        f"/v1/goals/{b['goal_id']}/dependencies", json={"depends_on_goal_id": a["goal_id"]}
    )
    assert cycle.status_code == 409


def test_update_goal_patch(client: TestClient) -> None:
    goal = _create(client)
    response = client.patch(f"/v1/goals/{goal['goal_id']}", json={"priority": 5})
    assert response.status_code == 200
    assert response.json()["priority"] == 5
