"""Unit tests: /v1/experience REST surface, against injected SQLite.

This router is NOT registered in app.main (the integrator wires it in with
the experience_lessons migration — task brief), so this file builds a
standalone FastAPI app around app.experience.routes.router directly, the way
tests/unit/test_identity_enforcement.py builds bespoke FastAPI apps rather
than always going through app.main.create_app. Owner authentication is real
(tests.identity_support.authenticate) — no dependency override, matching
every other route test in this repo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.experience import routes as experience_routes
from app.experience.models import STATUS_CANDIDATE, ExperienceLessonRow
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.memory.embedding import DeterministicEmbedder
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.memory.types import MemoryClass, WriteStage
from app.selfhealing.models import Incident, Release
from tests.identity_support import authenticate

ALL_TABLES = [
    ActivityEventRow.__table__,
    Incident.__table__,
    Release.__table__,
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
    ExperienceLessonRow.__table__,
]

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

REJECTED_BY_REASON = {
    "off_topic": 9,
    "interstitial": 11,
    "date_uncertain": 3,
    "duplicate_event": 1,
    "outside_recency_window": 4,
}


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
    app = FastAPI()
    app.include_router(experience_routes.router)

    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts

    # routes only ever read `.embedder` off app.state.memory (see
    # app.experience.routes._embedder) — a real MemoryRuntime is not needed.
    app.state.memory = SimpleNamespace(embedder=DeterministicEmbedder())

    return app, TestClient(app)


@pytest.fixture()
def client(app_and_client) -> TestClient:
    app, test_client = app_and_client
    authenticate(app, test_client, settings=Settings(_env_file=None))
    return test_client


def _seed_research_completed(engine, *, rejected_by_reason=None) -> None:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        task_id = uuid.uuid4()
        stats = {"discovered": 240, "fetched": 33, "evidence": 5, "rejected": 28}
        if rejected_by_reason is not None:
            stats["rejected_by_reason"] = rejected_by_reason
        ledger_service.record(
            session,
            ledger_service.build_research_completed_event(
                task_id=task_id,
                occurred_at=NOW,
                report_json={
                    "stats": stats,
                    "findings": [{"id": f"f{i}"} for i in range(5)],
                    "sources": [{"id": f"s{i}"} for i in range(5)],
                },
                source_ref=f"research_runs:{task_id}:ready",
            ),
        )


def _seed_incident(engine, *, occurrence_count: int = 1) -> None:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(
            Incident(
                component="research",
                severity="warning",
                fingerprint=f"research:{uuid.uuid4()}",
                evidence_json={
                    "dominant_rejection_reason": "interstitial",
                    "rejected_by_reason": dict(REJECTED_BY_REASON),
                },
                status="fixed",
                occurrence_count=occurrence_count,
                first_seen_at=NOW,
                last_seen_at=NOW,
            )
        )
        session.commit()


# ---------------------------------------------------------------- owner gate


def test_experience_endpoints_require_owner_session(app_and_client) -> None:
    _, test_client = app_and_client
    response = test_client.get("/v1/experience/lessons")
    assert response.status_code == 401


def test_experience_ingest_requires_owner_session(app_and_client) -> None:
    _, test_client = app_and_client
    response = test_client.post("/v1/experience/ingest", json={})
    assert response.status_code == 401


# -------------------------------------------------------------------- policy


def test_get_policy_shape(client: TestClient) -> None:
    response = client.get("/v1/experience/policy")
    assert response.status_code == 200
    body = response.json()
    assert body["experience_version"] == 1
    assert body["thresholds"]["auto_promote_score"] == pytest.approx(0.75)
    assert set(body["weights"]) == {
        "generalizability",
        "confidence",
        "recurrence",
        "owner_relevance",
        "risk_overgeneralization_penalty",
    }
    assert set(body["lesson_statuses"]) == {"candidate", "promoted", "rejected", "superseded"}


# -------------------------------------------------------------------- ingest


def test_ingest_route_records_episodic_memory(engine, client: TestClient) -> None:
    _seed_research_completed(engine)

    response = client.post("/v1/experience/ingest", json={})

    assert response.status_code == 200
    report = response.json()["report"]
    assert report["episodic_created"] == 1
    assert report["episodic_refused_secret"] == 0

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        memories = (
            session.execute(select(Memory).where(Memory.memory_class == MemoryClass.EPISODIC.value))
            .scalars()
            .all()
        )
        assert len(memories) == 1


def test_ingest_route_is_idempotent(engine, client: TestClient) -> None:
    _seed_research_completed(engine)
    client.post("/v1/experience/ingest", json={})

    response = client.post("/v1/experience/ingest", json={})

    report = response.json()["report"]
    assert report["episodic_created"] == 0
    assert report["episodic_skipped_existing"] == 1


# ------------------------------------------------------------------ compile


def test_compile_route_lists_candidate_lesson(engine, client: TestClient) -> None:
    _seed_incident(engine, occurrence_count=1)

    response = client.post("/v1/experience/compile")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    lesson = body["lessons"][0]
    assert lesson["status"] == STATUS_CANDIDATE
    assert "must not become research evidence" in lesson["statement"]

    listing = client.get("/v1/experience/lessons", params={"status": "candidate"})
    assert listing.status_code == 200
    assert listing.json()["count"] == 1


def test_list_lessons_rejects_unknown_status(engine, client: TestClient) -> None:
    response = client.get("/v1/experience/lessons", params={"status": "not-a-status"})
    assert response.status_code == 422


# ------------------------------------------------------------ promote/reject


def test_promote_lesson_creates_durable_owner_memory(engine, client: TestClient) -> None:
    _seed_incident(engine, occurrence_count=1)
    client.post("/v1/experience/compile")
    lesson_id = client.get("/v1/experience/lessons").json()["lessons"][0]["lesson_id"]

    response = client.post(f"/v1/experience/lessons/{lesson_id}/promote")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "promoted"
    memory_id = body["promoted_memory_id"]
    assert memory_id is not None

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        memory = session.get(Memory, uuid.UUID(memory_id))
        assert memory is not None
        assert memory.memory_class == MemoryClass.PROCEDURAL.value
        assert memory.explicit is True
        assert memory.confidence == 1.0
        assert memory.stage == WriteStage.DURABLE.value

    # promoting again is refused: the candidate is gone.
    again = client.post(f"/v1/experience/lessons/{lesson_id}/promote")
    assert again.status_code == 409


def test_reject_lesson_then_cannot_be_promoted(engine, client: TestClient) -> None:
    _seed_incident(engine, occurrence_count=1)
    client.post("/v1/experience/compile")
    lesson_id = client.get("/v1/experience/lessons").json()["lessons"][0]["lesson_id"]

    response = client.post(
        f"/v1/experience/lessons/{lesson_id}/reject", json={"reason": "not useful"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    promote = client.post(f"/v1/experience/lessons/{lesson_id}/promote")
    assert promote.status_code == 409


def test_promote_unknown_lesson_is_404(client: TestClient) -> None:
    response = client.post(f"/v1/experience/lessons/{uuid.uuid4()}/promote")
    assert response.status_code == 404
