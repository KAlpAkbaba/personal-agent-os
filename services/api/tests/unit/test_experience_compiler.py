"""Unit tests: app.experience.compiler (Phase 3, Experience Compiler).

Fixtures are grounded in this repo's own real incident history:
- docs/DECISIONS.md #22 (2026-09-04): a Cloudflare interstitial page was
  ranked as research evidence; the fix was a page-validity gate before
  ranking. Modeled here as a "browser interstitial incident".
- docs/DECISIONS.md #16 (2026-09-04): "INSTALL VERIFIED" while the live
  worker still ran the OLD build — repo/staged tree current, running process
  stale. Modeled here as a deployment.agent.installed incident with
  evidence_json {"repo_state": "current", "runtime_state": "stale"}.
- app.research.contracts' real error_class "insufficient_valid_findings"
  for a lone, non-recurring research.failed ledger event.

SQLite only, tables created from metadata (ledger + selfhealing + memory +
experience schemas together, since the compiler reads all four).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.experience import compiler as experience_compiler
from app.experience.compiler import (
    AUTO_PROMOTE_SCORE_THRESHOLD,
    PATTERN_ACCEPTANCE_WORDING,
    PATTERN_DEPLOYMENT_PROVENANCE,
    PATTERN_GENERIC,
    PATTERN_RESEARCH_EVIDENCE_LEAK,
    RECURRENCE_SATURATION,
    score_lesson,
)
from app.experience.models import (
    STATUS_CANDIDATE,
    STATUS_PROMOTED,
    STATUS_REJECTED,
    ExperienceLessonRow,
)
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.service import ActivityEvent
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
from app.selfhealing.models import Incident, Release

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

EMBEDDER = DeterministicEmbedder()
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

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


def _release(session, *, component="agent", version="0.2.0") -> Release:
    release = Release(
        component=component,
        version=version,
        manifest_digest="deadbeef" * 4,
        status="active",
        promoted_at=NOW,
    )
    session.add(release)
    session.commit()
    return release


def _incident(
    session,
    *,
    component: str,
    status: str = "open",
    occurrence_count: int = 1,
    evidence_json: dict | None = None,
    fixed_release_id=None,
    first_seen_at: datetime = NOW,
) -> Incident:
    incident = Incident(
        component=component,
        severity="warning",
        fingerprint=f"{component}:{uuid.uuid4()}",
        evidence_json=evidence_json or {},
        status=status,
        occurrence_count=occurrence_count,
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at,
        fixed_release_id=fixed_release_id,
    )
    session.add(incident)
    session.commit()
    return incident


def _research_failed(session, *, occurred_at, error_class="insufficient_valid_findings"):
    task_id = uuid.uuid4()
    return ledger_service.record(
        session,
        ledger_service.build_research_failed_event(
            task_id=task_id,
            occurred_at=occurred_at,
            error_class=error_class,
            error="fewer than 3 attributable findings",
            source_ref=f"research_runs:{task_id}:failed",
        ),
    )


def _research_completed(session, *, occurred_at, source_ref):
    task_id = uuid.uuid4()
    return ledger_service.record(
        session,
        ledger_service.build_research_completed_event(
            task_id=task_id,
            occurred_at=occurred_at,
            report_json={
                "stats": {"discovered": 10, "fetched": 5, "evidence": 5, "rejected": 0},
                "findings": [{"id": "f0"}],
                "sources": [{"id": "s0"}],
            },
            source_ref=source_ref,
        ),
    )


def _lessons(session) -> list[ExperienceLessonRow]:
    return list(session.execute(select(ExperienceLessonRow)).scalars().all())


# ---------------------------------------------------------------- score_lesson


def test_score_lesson_is_monotonic_in_generalizability_confidence_recurrence_owner_relevance():
    base = dict(
        generalizability=0.3,
        confidence=0.3,
        recurrence=1,
        owner_relevance=0.3,
        risk_overgeneralization=0.3,
    )
    baseline = score_lesson(**base)

    higher_g = score_lesson(**{**base, "generalizability": 0.9})
    higher_c = score_lesson(**{**base, "confidence": 0.9})
    higher_r = score_lesson(**{**base, "recurrence": RECURRENCE_SATURATION})
    higher_o = score_lesson(**{**base, "owner_relevance": 0.9})

    assert higher_g > baseline
    assert higher_c > baseline
    assert higher_r > baseline
    assert higher_o > baseline


def test_score_lesson_decreases_with_overgeneralization_risk():
    base = dict(
        generalizability=0.8,
        confidence=0.8,
        recurrence=3,
        owner_relevance=0.8,
        risk_overgeneralization=0.1,
    )
    low_risk = score_lesson(**base)
    high_risk = score_lesson(**{**base, "risk_overgeneralization": 0.9})

    assert high_risk < low_risk


def test_score_lesson_recurrence_saturates():
    base = dict(
        generalizability=0.5, confidence=0.5, owner_relevance=0.5, risk_overgeneralization=0.2
    )
    at_saturation = score_lesson(recurrence=RECURRENCE_SATURATION, **base)
    past_saturation = score_lesson(recurrence=RECURRENCE_SATURATION * 3, **base)

    assert at_saturation == past_saturation


def test_score_lesson_bounded_zero_to_one():
    assert (
        0.0
        <= score_lesson(
            generalizability=0.0,
            confidence=0.0,
            recurrence=0,
            owner_relevance=0.0,
            risk_overgeneralization=1.0,
        )
        <= 1.0
    )
    assert (
        0.0
        <= score_lesson(
            generalizability=1.0,
            confidence=1.0,
            recurrence=99,
            owner_relevance=1.0,
            risk_overgeneralization=0.0,
        )
        <= 1.0
    )


# -------------------------------------------------------------- compile_lessons


def test_unresolved_incident_produces_no_lesson(session):
    _incident(session, component="research", status="open", first_seen_at=NOW)

    candidates = experience_compiler.compile_lessons(session, embedder=EMBEDDER, now=NOW)

    assert candidates == []
    assert _lessons(session) == []


def test_browser_interstitial_incident_yields_evidence_leak_lesson(session):
    """docs/DECISIONS.md #22: an interstitial page nearly became research
    evidence; lesson: interstitial pages must not become research evidence."""
    _incident(
        session,
        component="research",
        status="fixed",
        occurrence_count=1,
        evidence_json={
            "dominant_rejection_reason": "interstitial",
            "rejected_by_reason": dict(REJECTED_BY_REASON),
        },
        first_seen_at=NOW,
    )

    candidates = experience_compiler.compile_lessons(session, embedder=EMBEDDER, now=NOW)

    assert len(candidates) == 1
    lesson = candidates[0]
    assert lesson.pattern == PATTERN_RESEARCH_EVIDENCE_LEAK
    assert "araştırma kanıtı olamaz" in lesson.statement
    assert lesson.scope == "research"


def test_deployment_provenance_incident_yields_provenance_lesson(session):
    """docs/DECISIONS.md #16: repo/staged tree current, running process stale
    ('INSTALL VERIFIED' but the live worker was the old build); lesson:
    runtime provenance must be independently verified."""
    release = _release(session, component="agent", version="0.3.0")
    _incident(
        session,
        component="agent",
        status="fixed",
        occurrence_count=1,
        evidence_json={"repo_state": "current", "runtime_state": "stale"},
        fixed_release_id=release.id,
        first_seen_at=NOW,
    )

    candidates = experience_compiler.compile_lessons(session, embedder=EMBEDDER, now=NOW)

    assert len(candidates) == 1
    lesson = candidates[0]
    assert lesson.pattern == PATTERN_DEPLOYMENT_PROVENANCE
    assert "ayrıca doğrulanmalı" in lesson.statement.lower()
    assert lesson.pattern == PATTERN_DEPLOYMENT_PROVENANCE
    assert lesson.scope == "deployment"


def test_single_non_recurring_weak_lesson_stays_candidate(session):
    _research_failed(session, occurred_at=NOW - timedelta(hours=2))
    # the resolution: a later, unrelated research run completes fine.
    _research_completed(session, occurred_at=NOW, source_ref="research_runs:next:ready")

    candidates = experience_compiler.compile_lessons(session, embedder=EMBEDDER, now=NOW)

    assert len(candidates) == 1
    lesson = candidates[0]
    assert lesson.pattern == PATTERN_GENERIC
    assert lesson.recurrence == 1
    assert lesson.score < AUTO_PROMOTE_SCORE_THRESHOLD

    rows = _lessons(session)
    assert len(rows) == 1
    assert rows[0].status == STATUS_CANDIDATE


def test_recurring_well_evidenced_lesson_is_promoted_to_procedural_memory(session):
    release = _release(session, component="research", version="policy-4")
    _incident(
        session,
        component="research",
        status="fixed",
        occurrence_count=RECURRENCE_SATURATION,
        evidence_json={
            "dominant_rejection_reason": "interstitial",
            "rejected_by_reason": dict(REJECTED_BY_REASON),
        },
        fixed_release_id=release.id,
        first_seen_at=NOW,
    )

    candidates = experience_compiler.compile_lessons(session, embedder=EMBEDDER, now=NOW)
    assert len(candidates) == 1
    assert candidates[0].score >= AUTO_PROMOTE_SCORE_THRESHOLD

    rows = _lessons(session)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == STATUS_PROMOTED
    assert row.promoted_memory_id is not None
    assert row.detail_json["promotion"]["kind"] == "auto"
    assert row.detail_json["promotion"]["actor"] == "policy"

    memory = session.get(Memory, row.promoted_memory_id)
    assert memory is not None
    assert memory.memory_class == MemoryClass.PROCEDURAL.value
    assert memory.status == MemoryStatus.ACTIVE.value
    assert memory.explicit is False  # a system inference, never owner authority
    assert memory.confidence < 1.0
    assert memory.value_json["kind"] == "inference"


def test_recompile_does_not_overwrite_an_owner_rejected_lesson(session):
    _incident(
        session,
        component="research",
        status="fixed",
        occurrence_count=1,
        evidence_json={
            "dominant_rejection_reason": "interstitial",
            "rejected_by_reason": dict(REJECTED_BY_REASON),
        },
        first_seen_at=NOW,
    )
    experience_compiler.compile_lessons(session, embedder=EMBEDDER, now=NOW)
    row = _lessons(session)[0]
    row.status = STATUS_REJECTED
    session.commit()

    experience_compiler.compile_lessons(session, embedder=EMBEDDER, now=NOW)

    rows = _lessons(session)
    assert len(rows) == 1
    assert rows[0].status == STATUS_REJECTED


def test_recompile_is_stable_and_does_not_duplicate_rows(session):
    _incident(
        session,
        component="research",
        status="fixed",
        occurrence_count=1,
        evidence_json={
            "dominant_rejection_reason": "interstitial",
            "rejected_by_reason": dict(REJECTED_BY_REASON),
        },
        first_seen_at=NOW,
    )

    experience_compiler.compile_lessons(session, embedder=EMBEDDER, now=NOW)
    experience_compiler.compile_lessons(session, embedder=EMBEDDER, now=NOW)

    assert len(_lessons(session)) == 1


# ------------------------------------- the defect class that cost two owner runs


def _wording_failure(session, *, occurred_at, source_ref, detail=None):
    """The shape the two REAL incidents had in the Phase 8 lifecycle run."""
    return ledger_service.record(
        session,
        ActivityEvent(
            event_type="incident.opened",
            subsystem="voice",
            action="qualification.wording_gate_failed",
            factual_summary=(
                "Calisan bir sistem, brifing daha kisa ifade edildigi icin cumle "
                "onekini esleyen kabul kontrolunde basarisiz oldu."
            ),
            status="failed",
            severity="warning",
            production_state="n/a",
            module="scripts/voice/owner-explain.ps1",
            occurred_at=occurred_at,
            source="backfill",
            source_ref=source_ref,
            detail_json=(
                {
                    "adr": "ADR-0051 addendum 4",
                    "defect_class": "acceptance_depends_on_generated_wording",
                    "failed_check": "speech_head -like <turkish sentence prefix>",
                    "cost": "one owner qualification run",
                }
                if detail is None
                else detail
            ),
        ),
    )


def _voice_recovered(session, *, occurred_at, source_ref):
    return ledger_service.record(
        session,
        ActivityEvent(
            event_type="voice.explained",
            subsystem="voice",
            action="qualification.verify_only_passed",
            factual_summary="Oturum yapisal provenance uzerinden yeniden dogrulandi.",
            status="completed",
            severity="info",
            production_state="n/a",
            occurred_at=occurred_at,
            source="backfill",
            source_ref=source_ref,
        ),
    )


def test_the_acceptance_wording_defect_compiles_as_its_own_lesson(session):
    """Before this pattern existed, both real incidents compiled as the GENERIC
    "Recurring voice failure (incident.opened)" lesson - so the system could not
    answer "ne ogrendin?" about the defect class that cost the owner two
    qualification runs. The lesson lived only in docs/DECISIONS.md."""
    _wording_failure(session, occurred_at=NOW - timedelta(days=1), source_ref="adr2")
    _wording_failure(session, occurred_at=NOW - timedelta(hours=4), source_ref="adr4")
    _voice_recovered(session, occurred_at=NOW - timedelta(hours=1), source_ref="adr4:verify")
    session.commit()

    candidates = experience_compiler.compile_lessons(session, embedder=EMBEDDER, now=NOW)

    named = [c for c in candidates if c.pattern == PATTERN_ACCEPTANCE_WORDING]
    assert named, f"expected the named pattern, got {[c.pattern for c in candidates]}"
    lesson = named[0]
    assert lesson.title == "Kabul kanıtı yapısal olmalı, ifade değil"
    assert lesson.scope == "qualification"
    assert "YAPIYA" in lesson.statement
    # it names the check that failed, never the generated sentence it matched
    assert "speech_head" in lesson.root_cause
    assert "Efendim" not in lesson.root_cause
    assert not any(c.pattern == PATTERN_GENERIC for c in candidates), (
        "the specific pattern must win over the generic fallback"
    )


def test_an_undeclared_voice_failure_is_not_swept_into_the_pattern(session):
    """It is recognised from a DECLARED class, so an ordinary voice failure that
    happens to look similar must still compile as generic."""
    _wording_failure(
        session,
        occurred_at=NOW - timedelta(days=1),
        source_ref="plain",
        detail={"note": "unrelated"},
    )
    _voice_recovered(session, occurred_at=NOW - timedelta(hours=1), source_ref="plain:ok")
    session.commit()

    candidates = experience_compiler.compile_lessons(session, embedder=EMBEDDER, now=NOW)

    assert candidates and all(c.pattern == PATTERN_GENERIC for c in candidates)


def test_a_generic_lesson_never_reads_an_evidence_blob_aloud(session):
    """The generic branch embedded repr(detail_json) in the root cause, and that text
    is spoken verbatim. Only the SHAPE survives now (security review, 2026-09-05)."""
    literal = "AKIA" + "ABCDEFGHIJKLMNOP"
    _wording_failure(
        session,
        occurred_at=NOW - timedelta(days=1),
        source_ref="blob",
        detail={"note": f"token {literal} leaked here", "other": 1},
    )
    _voice_recovered(session, occurred_at=NOW - timedelta(hours=1), source_ref="blob:ok")
    session.commit()

    candidates = experience_compiler.compile_lessons(session, embedder=EMBEDDER, now=NOW)

    root_causes = " ".join(c.root_cause for c in candidates)
    assert literal not in root_causes
    assert "note" in root_causes and "other" in root_causes  # keys survive, values do not
