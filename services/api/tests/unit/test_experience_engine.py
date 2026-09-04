"""Unit tests: app.experience.engine (Phase 2, Experience Engine).

Fixtures use the REAL shapes this repo's own history/tests carry (see
tests/unit/test_ledger_service.py and docs/DECISIONS.md #22): a
research.completed event with the 2026-09-04 owner run's stats (240
discovered, 33 fetched, 5 evidence, 28 rejected, rejected_by_reason incl.
interstitial=11) and a research.failed event with error_class
'insufficient_valid_findings'. SQLite only, tables created from metadata —
mirrors tests/unit/test_memory_service.py + test_ledger_service.py's fixture
pattern (this module touches both the ledger and the memory schema).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.experience import engine as experience_engine
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_RESEARCH_COMPLETED,
    EVENT_TYPE_RESEARCH_FAILED,
    STATUS_FAILED,
    SUBSYSTEM_RESEARCH,
)
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
from app.memory.types import MemoryClass, MemoryStatus

ALL_TABLES = [
    ActivityEventRow.__table__,
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
]

EMBEDDER = DeterministicEmbedder()
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

#: An AWS-access-key-shaped fixture, assembled so no tracked file carries the shape.
_SECRET_SHAPED = "AKIA" + "ABCDEFGHIJKLMNOP"

#: the real 2026-09-04 owner run's rejection breakdown (task instructions /
#: docs/DECISIONS.md #22 / tests/unit/test_ledger_service.py).
REJECTED_BY_REASON = {
    "off_topic": 9,
    "interstitial": 11,
    "date_uncertain": 3,
    "duplicate_event": 1,
    "outside_recency_window": 4,
}


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


def _record_research_completed(
    session, *, occurred_at: datetime, rejected_by_reason: dict | None = None, source_ref=None
) -> ActivityEventRow:
    stats = {
        "queries": 3,
        "discovered": 240,
        "fetched": 33,
        "fetch_failed": 2,
        "deduplicated": 0,
        "evidence": 5,
        "rejected": 28,
    }
    if rejected_by_reason is not None:
        stats["rejected_by_reason"] = rejected_by_reason
    task_id = uuid.uuid4()
    return ledger_service.record(
        session,
        ledger_service.build_research_completed_event(
            task_id=task_id,
            occurred_at=occurred_at,
            report_json={
                "stats": stats,
                "findings": [{"id": f"f{i}"} for i in range(5)],
                "sources": [{"id": f"s{i}"} for i in range(5)],
            },
            source_ref=source_ref or f"research_runs:{task_id}:ready",
        ),
    )


def _record_research_failed(
    session, *, occurred_at: datetime, error_class: str
) -> ActivityEventRow:
    task_id = uuid.uuid4()
    return ledger_service.record(
        session,
        ledger_service.build_research_failed_event(
            task_id=task_id,
            occurred_at=occurred_at,
            error_class=error_class,
            error="insufficient_valid_findings: fewer than 3 attributable findings",
            source_ref=f"research_runs:{task_id}:failed",
        ),
    )


def _episodic_memories(session):
    return list(
        session.execute(select(Memory).where(Memory.memory_class == MemoryClass.EPISODIC.value))
        .scalars()
        .all()
    )


def _semantic_memories(session):
    return list(
        session.execute(select(Memory).where(Memory.memory_class == MemoryClass.SEMANTIC.value))
        .scalars()
        .all()
    )


# ------------------------------------------------------------------ episodic


def test_ingest_creates_episodic_memory_for_research_completed(session):
    event = _record_research_completed(
        session, occurred_at=NOW, rejected_by_reason=dict(REJECTED_BY_REASON)
    )

    report = experience_engine.ingest(session, embedder=EMBEDDER)

    assert report.episodic_created == 1
    memories = _episodic_memories(session)
    assert len(memories) == 1
    memory = memories[0]
    assert memory.text == event.factual_summary
    assert memory.status == MemoryStatus.ACTIVE.value
    assert memory.explicit is False
    # never 1.0 for an inference (task brief).
    assert 0.0 < memory.confidence < 1.0
    assert memory.value_json["ledger_event_id"] == str(event.event_id)
    assert memory.value_json["tags"]["subsystem"] == SUBSYSTEM_RESEARCH
    assert memory.value_json["tags"]["event_type"] == EVENT_TYPE_RESEARCH_COMPLETED


def test_ingest_creates_episodic_memory_for_research_failed(session):
    event = _record_research_failed(
        session, occurred_at=NOW, error_class="insufficient_valid_findings"
    )

    report = experience_engine.ingest(session, embedder=EMBEDDER)

    assert report.episodic_created == 1
    memory = _episodic_memories(session)[0]
    assert memory.text == event.factual_summary
    assert memory.value_json["tags"]["event_type"] == EVENT_TYPE_RESEARCH_FAILED
    assert memory.value_json["tags"]["status"] == STATUS_FAILED
    # outcome prefers the event's own `result` (here: the error_class) over
    # the bare status, matching app.experience.engine._episodic_observation.
    assert memory.value_json["tags"]["outcome"] == "insufficient_valid_findings"
    assert memory.value_json["detail"]["error_class"] == "insufficient_valid_findings"


def test_ingest_is_idempotent_on_second_run(session):
    _record_research_completed(session, occurred_at=NOW)
    _record_research_failed(session, occurred_at=NOW, error_class="insufficient_valid_findings")

    first = experience_engine.ingest(session, embedder=EMBEDDER)
    assert first.episodic_created == 2

    second = experience_engine.ingest(session, embedder=EMBEDDER)
    assert second.episodic_created == 0
    assert second.episodic_skipped_existing == 2

    # no duplicate rows, no new audit noise from a second pass.
    assert len(_episodic_memories(session)) == 2


def test_evidence_refs_resolvable_to_a_real_ledger_event(session):
    event = _record_research_completed(session, occurred_at=NOW)
    experience_engine.ingest(session, embedder=EMBEDDER)

    memory = _episodic_memories(session)[0]
    event_id = memory.provenance_json["source"]["event_id"]
    resolved = session.get(ActivityEventRow, uuid.UUID(event_id))

    assert resolved is not None
    assert resolved.event_id == event.event_id
    assert memory.occurred_at is not None


def test_secret_like_content_is_refused_by_existing_policy(session):
    task_id = uuid.uuid4()
    event = ledger_service.record(
        session,
        ledger_service.ActivityEvent(
            event_type=EVENT_TYPE_RESEARCH_FAILED,
            subsystem=SUBSYSTEM_RESEARCH,
            action="research_failed",
            status=STATUS_FAILED,
            # A secret-shaped string leaking into a factual summary - exactly the class of
            # content app.memory.policy.find_secret refuses. Composed at runtime on purpose:
            # the repository's own secret-hygiene gate refuses secret-SHAPED literals in
            # tracked files, and a fixture is not worth an exception to that rule.
            factual_summary=f"Araştırma başarısız oldu: {_SECRET_SHAPED} sızdı.",
            source="live",
            source_ref=f"research_runs:{task_id}:failed",
            occurred_at=NOW,
            research_job_id=task_id,
            evidence_refs=[{"kind": "research_run", "ref": str(task_id)}],
            detail_json={"error_class": "insufficient_valid_findings", "error": "x"},
        ),
    )

    report = experience_engine.ingest(session, embedder=EMBEDDER)

    assert report.episodic_refused_secret == 1
    assert report.episodic_created == 0
    assert _episodic_memories(session) == []
    # the refusal itself is audited (memory subsystem invariant).
    audits = session.execute(select(MemoryAuditEvent)).scalars().all()
    assert any(a.action == "refused_secret" for a in audits)
    assert event.event_id is not None  # the ledger event itself is untouched


# ------------------------------------------------------------------ semantic


def test_single_event_never_becomes_a_semantic_fact(session):
    """One observation is a fact about that run, never about the pipeline —
    'no inference stored as fact' until corroboration crosses the named
    threshold (SEMANTIC_MIN_CORROBORATION)."""
    _record_research_completed(
        session, occurred_at=NOW, rejected_by_reason=dict(REJECTED_BY_REASON)
    )

    report = experience_engine.ingest(session, embedder=EMBEDDER)

    assert report.semantic_created == 0
    assert _semantic_memories(session) == []


def test_corroborated_semantic_fact_is_marked_as_inference_with_confidence_below_one(session):
    _record_research_completed(
        session,
        occurred_at=NOW - timedelta(days=1),
        rejected_by_reason=dict(REJECTED_BY_REASON),
        source_ref="research_runs:run-a:ready",
    )
    _record_research_completed(
        session,
        occurred_at=NOW,
        rejected_by_reason=dict(REJECTED_BY_REASON),
        source_ref="research_runs:run-b:ready",
    )

    report = experience_engine.ingest(session, embedder=EMBEDDER)

    assert report.semantic_created + report.semantic_corroborated >= 2
    semantic = _semantic_memories(session)
    assert len(semantic) == 1
    memory = semantic[0]
    assert memory.value_json["kind"] == "inference"
    assert memory.value_json["reason"] == "interstitial"
    assert 0.0 < memory.confidence < 1.0
    assert memory.evidence_count == 2


def test_semantic_derivation_is_idempotent_across_ingest_runs(session):
    _record_research_completed(
        session,
        occurred_at=NOW - timedelta(days=1),
        rejected_by_reason=dict(REJECTED_BY_REASON),
        source_ref="research_runs:run-a:ready",
    )
    _record_research_completed(
        session,
        occurred_at=NOW,
        rejected_by_reason=dict(REJECTED_BY_REASON),
        source_ref="research_runs:run-b:ready",
    )

    experience_engine.ingest(session, embedder=EMBEDDER)
    before = _semantic_memories(session)[0]
    before_evidence_count = before.evidence_count

    second = experience_engine.ingest(session, embedder=EMBEDDER)

    assert second.semantic_created == 0
    assert second.semantic_corroborated == 0
    after = _semantic_memories(session)[0]
    assert after.evidence_count == before_evidence_count
