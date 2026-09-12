"""Unit tests: app.worldmodel (overnight plan Phase 5).

Covers: the four truth-kind separation (a fact known only from source is
never reported as runtime truth), graceful degradation when a source is
unavailable, and the owner-gated read-only REST surface.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import (
    TASK_STATUS_PRESENTING,
    TASK_STATUS_READY,
    TASK_STATUS_RUNNING,
    Task,
)
from app.artifacts.runtime import ArtifactRuntime
from app.broker.models import Device
from app.config import Settings
from app.goals.models import Goal, GoalTask
from app.identity.models import OwnerSession
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.selfhealing.models import Incident, Release
from app.worldmodel.routes import router as worldmodel_router
from app.worldmodel.state import Fact, TruthKind, assemble_snapshot
from tests.identity_support import authenticate, install_identity

ALL_TABLES = [
    Task.__table__,
    Goal.__table__,
    GoalTask.__table__,
    ActivityEventRow.__table__,
    Device.__table__,
    OwnerSession.__table__,
    Release.__table__,
    Incident.__table__,
]

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ALL_TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


# ------------------------------------------------------------------- state.py


def test_snapshot_never_raises_on_a_bare_empty_database(session) -> None:
    snapshot = assemble_snapshot(session, now=NOW)
    assert snapshot.facts
    assert snapshot.generated_at == NOW


def test_owner_never_enrolled_is_evidence_truth_and_uncertain(session) -> None:
    snapshot = assemble_snapshot(session, now=NOW)
    owner_fact = next(f for f in snapshot.facts if f.key == "owner.enrolled")
    assert owner_fact.truth_kind == TruthKind.EVIDENCE
    assert owner_fact.value is False
    assert any(u.subject == "owner.enrolled" for u in snapshot.uncertainties)


def test_owner_enrolled_is_evidence_truth_with_a_ref(session) -> None:
    session.add(
        OwnerSession(
            token_hash="x" * 64,
            client_kind="cli",
            expires_at=NOW,
        )
    )
    session.commit()
    snapshot = assemble_snapshot(session, now=NOW)
    owner_fact = next(f for f in snapshot.facts if f.key == "owner.enrolled")
    assert owner_fact.truth_kind == TruthKind.EVIDENCE
    assert owner_fact.value is True
    assert owner_fact.evidence_refs


def test_device_enrolled_count_is_evidence_never_promoted_to_runtime_without_a_probe(
    session,
) -> None:
    session.add(
        Device(name="laptop", platform="windows", public_key_spki_b64="abc", capabilities_json=[])
    )
    session.commit()
    snapshot = assemble_snapshot(session, now=NOW, broker_runtime=None)
    devices_fact = next(f for f in snapshot.facts if f.key == "devices.enrolled_count")
    assert devices_fact.truth_kind == TruthKind.EVIDENCE
    assert devices_fact.value == 1
    # no live broker runtime was supplied -> presence must NOT appear as a fact at all
    assert not [f for f in snapshot.facts if f.key.startswith("devices.presence")]
    assert any(u.subject == "devices.presence" for u in snapshot.uncertainties)


def test_device_presence_is_runtime_truth_when_a_live_broker_is_supplied(session) -> None:
    device = Device(
        name="laptop", platform="windows", public_key_spki_b64="abc", capabilities_json=[]
    )
    session.add(device)
    session.commit()

    class FakeBrokerRuntime:
        def is_online(self, device_id) -> bool:
            return True

    snapshot = assemble_snapshot(session, now=NOW, broker_runtime=FakeBrokerRuntime())
    presence_fact = next(f for f in snapshot.facts if f.key.startswith("devices.presence."))
    assert presence_fact.truth_kind == TruthKind.RUNTIME
    assert presence_fact.value == "online"


def test_capabilities_are_source_truth(session) -> None:
    snapshot = assemble_snapshot(session, now=NOW)
    cap_fact = next(f for f in snapshot.facts if f.key == "capabilities.voice_tools")
    assert cap_fact.truth_kind == TruthKind.SOURCE
    assert isinstance(cap_fact.value, list)


def _fact(snapshot, key: str):
    return next(f for f in snapshot.facts if f.key == key)


def test_a_running_task_is_counted_as_running_and_ages_like_an_observation(session) -> None:
    session.add(Task(intent="araştır", status=TASK_STATUS_RUNNING))
    session.commit()
    snapshot = assemble_snapshot(session, now=NOW)
    fact = _fact(snapshot, "tasks.running_count")
    # RUNTIME, not EVIDENCE: "how many are running" is an observation of this moment. As
    # EVIDENCE it never went stale, so an hour-old snapshot read as current fact (B06 req 67).
    assert fact.truth_kind == TruthKind.RUNTIME
    assert fact.value == 1


def test_finished_work_waiting_for_the_owner_is_not_reported_as_running(session) -> None:
    """B06 req 68. Production said `tasks.running = 10` while nothing was running: the ten
    were finished research sitting in READY. "Not terminal" is not "the system is working on
    it", and the owner asking "ne yapıyorsun?" is asking the second question."""
    session.add(Task(intent="araştır", status=TASK_STATUS_READY))
    session.add(Task(intent="araştır", status=TASK_STATUS_PRESENTING))
    session.add(Task(intent="araştır", status=TASK_STATUS_RUNNING))
    session.commit()

    snapshot = assemble_snapshot(session, now=NOW)

    assert _fact(snapshot, "tasks.running_count").value == 1
    assert _fact(snapshot, "tasks.awaiting_owner_count").value == 2


def test_a_task_nobody_has_moved_for_a_day_is_counted_as_stuck(session) -> None:
    """B06 req 69. The 2026-09-09 research sat in `discovering` for three days and the world
    model reported it as work in progress, indefinitely."""
    from datetime import timedelta

    session.add(
        Task(intent="araştır", status=TASK_STATUS_RUNNING, created_at=NOW - timedelta(days=3))
    )
    session.add(Task(intent="araştır", status=TASK_STATUS_RUNNING, created_at=NOW))
    session.commit()

    snapshot = assemble_snapshot(session, now=NOW)

    stuck = _fact(snapshot, "tasks.stuck_count")
    assert stuck.value == 1
    assert len(stuck.evidence_refs) == 1
    assert _fact(snapshot, "tasks.running_count").value == 2


def test_dependencies_are_source_truth_without_a_health_probe(session) -> None:
    snapshot = assemble_snapshot(session, now=NOW)
    db_fact = next(f for f in snapshot.facts if f.key == "dependencies.database_url")
    assert db_fact.truth_kind == TruthKind.SOURCE
    assert any(u.subject == "dependencies.runtime" for u in snapshot.uncertainties)
    assert not [f for f in snapshot.facts if f.key.endswith(".runtime_status")]


def test_dependencies_become_runtime_truth_when_a_health_probe_is_supplied(session) -> None:
    snapshot = assemble_snapshot(
        session, now=NOW, health_results={"redis": {"status": "ok", "latency_ms": 1.2}}
    )
    runtime_fact = next(f for f in snapshot.facts if f.key == "dependencies.redis.runtime_status")
    assert runtime_fact.truth_kind == TruthKind.RUNTIME
    assert runtime_fact.value == "ok"


def test_environment_name_is_source_python_runtime_is_runtime_installed_is_installed(
    session,
) -> None:
    snapshot = assemble_snapshot(session, now=NOW)
    by_key = {f.key: f for f in snapshot.facts}
    assert by_key["environment.name"].truth_kind == TruthKind.SOURCE
    assert by_key["environment.python_runtime"].truth_kind == TruthKind.RUNTIME
    fastapi_fact = by_key.get("environment.installed.fastapi")
    assert fastapi_fact is not None
    assert fastapi_fact.truth_kind == TruthKind.INSTALLED


def test_a_failing_section_degrades_to_an_uncertainty_not_an_exception(
    session, monkeypatch
) -> None:
    import app.worldmodel.state as state_module

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(state_module, "_collect_owner", _boom)
    snapshot = assemble_snapshot(session, now=NOW)
    # every OTHER section still produced facts
    assert any(f.key == "tasks.running_count" for f in snapshot.facts)
    assert any(
        u.category == "owner" and u.reason == "section_unavailable" for u in snapshot.uncertainties
    )


def test_open_incidents_are_evidence(session) -> None:
    session.add(Release(component="cloud_core", version="1.0.0", manifest_digest="deadbeef"))
    session.commit()
    session.add(Incident(component="cloud_core", fingerprint="abc", status="open"))
    session.commit()
    snapshot = assemble_snapshot(session, now=NOW)
    incidents_fact = next(f for f in snapshot.facts if f.key == "incidents.open_count")
    assert incidents_fact.truth_kind == TruthKind.EVIDENCE
    assert incidents_fact.value == 1


def test_deployment_status_reflects_the_latest_release_as_evidence(session) -> None:
    session.add(
        Release(
            component="cloud_core",
            version="1.0.0",
            manifest_digest="deadbeef",
            status="active",
            promoted_at=NOW,
        )
    )
    session.commit()
    snapshot = assemble_snapshot(session, now=NOW)
    deploy_fact = next(f for f in snapshot.facts if f.key == "deployment.cloud_core.status")
    assert deploy_fact.truth_kind == TruthKind.EVIDENCE
    assert deploy_fact.value == "active"


# ------------------------------------------------------------------- routes.py


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
def app_and_client(engine):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    # Replace create_app()'s real-database identity runtime BEFORE any request: an
    # unauthenticated call audits its refusal, and that audit must land in SQLite, not
    # dial the compose Postgres (tests/unit/conftest.py).
    install_identity(app, settings=settings)

    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    # Not yet wired into app/main.py (integrator's job per task instructions).
    app.include_router(worldmodel_router)

    return app, TestClient(app)


@pytest.fixture()
def client(app_and_client) -> TestClient:
    app, test_client = app_and_client
    authenticate(app, test_client, settings=Settings(_env_file=None))
    return test_client


def test_world_endpoints_require_owner_session(app_and_client) -> None:
    _, test_client = app_and_client
    response = test_client.get("/v1/world")
    assert response.status_code == 401


def test_get_world_policy(client: TestClient) -> None:
    response = client.get("/v1/world/policy")
    assert response.status_code == 200
    body = response.json()
    assert body["world_version"] == 1
    assert set(body["truth_kinds"]) == {
        "source_truth",
        "installed_truth",
        "runtime_truth",
        "evidence_truth",
    }


def test_get_world_returns_facts_and_uncertainties(client: TestClient) -> None:
    response = client.get("/v1/world")
    assert response.status_code == 200
    body = response.json()
    assert body["facts"]
    assert isinstance(body["uncertainties"], list)


def test_get_world_facts_filters_by_kind(client: TestClient) -> None:
    response = client.get("/v1/world/facts", params={"kind": "source_truth"})
    assert response.status_code == 200
    facts = response.json()["facts"]
    assert facts
    assert all(f["truth_kind"] == "source_truth" for f in facts)


def test_get_world_facts_rejects_unknown_kind(client: TestClient) -> None:
    response = client.get("/v1/world/facts", params={"kind": "not_a_kind"})
    assert response.status_code == 422


def test_get_world_uncertainties(client: TestClient) -> None:
    response = client.get("/v1/world/uncertainties")
    assert response.status_code == 200
    assert any(u["subject"] == "owner.enrolled" for u in response.json()["uncertainties"])


# ------------------------------------------------------------------- staleness


def test_a_runtime_fact_goes_stale_and_says_so() -> None:
    """"It was running fifteen minutes ago" is not "it is running".

    ``stale`` used to be written as False everywhere and computed nowhere, so every
    answer claimed a freshness it had never checked (security review, 2026-09-05).
    """
    seen = datetime(2026, 9, 5, 6, 0, tzinfo=UTC)
    fact = Fact(
        key="cloud_core.running",
        category="runtime",
        value="0.4.0",
        truth_kind=TruthKind.RUNTIME,
        observed_at=seen,
    )
    assert not fact.is_stale(now=seen + timedelta(minutes=5))
    assert fact.is_stale(now=seen + timedelta(hours=2))
    assert fact.as_dict(now=seen + timedelta(hours=2))["stale"] is True
    assert fact.as_dict(now=seen + timedelta(minutes=1))["stale"] is False


def test_evidence_truth_never_goes_stale() -> None:
    """What happened stays happened; a historical record is not a perishable claim."""
    seen = datetime(2026, 1, 1, tzinfo=UTC)
    fact = Fact(
        key="research.qualified",
        category="evidence",
        value=True,
        truth_kind=TruthKind.EVIDENCE,
        observed_at=seen,
    )
    assert not fact.is_stale(now=seen + timedelta(days=900))


def test_an_explicitly_superseded_fact_stays_stale_however_fresh() -> None:
    seen = datetime(2026, 9, 5, 6, 0, tzinfo=UTC)
    fact = Fact(
        key="agent.installed",
        category="installed",
        value="0.3.9",
        truth_kind=TruthKind.INSTALLED,
        observed_at=seen,
        stale=True,
    )
    assert fact.is_stale(now=seen)
