"""Unit tests: memory service behavioral matrix against SQLite.

Each test name maps to an M5 acceptance behavior (see docstrings). Embeddings
use the frozen DeterministicEmbedder; semantic retrieval runs the in-memory
cosine fallback on SQLite (the pgvector path is covered by integration tests).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.memory import lifecycle, service
from app.memory.embedding import DeterministicEmbedder
from app.memory.errors import MemoryErrorClass, MemorySubsystemError
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.memory.policy import Observation
from app.memory.retrieval import RetrievalFilters, hybrid_search
from app.memory.service import MemoryLinks
from app.memory.types import (
    PROMOTE_MIN_CONFIDENCE,
    PROMOTE_MIN_EVIDENCE,
    SINGLE_OBSERVATION_MAX_CONFIDENCE,
    Actor,
    MemoryClass,
    MemoryStatus,
    RetentionClass,
    WriteStage,
)

MEMORY_TABLES = [
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
]

EMBEDDER = DeterministicEmbedder()


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in MEMORY_TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


def _observe(db: Session, text: str, **kwargs):
    links = kwargs.pop("links", None)
    return service.record_observation(
        db, EMBEDDER, Observation(text=text, **kwargs), links
    )


def _audit_actions(db: Session, memory_id=None) -> list[str]:
    stmt = select(MemoryAuditEvent).order_by(MemoryAuditEvent.id)
    if memory_id is not None:
        stmt = stmt.where(MemoryAuditEvent.memory_id == memory_id)
    return [e.action for e in db.execute(stmt).scalars().all()]


# ------------------------------------------------------------ explicit teach


def test_owner_explicit_teach_creates_durable_memory(db: Session) -> None:
    """Behavior: owner explicitly teaches a preference -> durable immediately."""
    result = service.remember_explicit(
        db,
        EMBEDDER,
        text="Bundan sonra özetleri Türkçe ver.",
        memory_class=MemoryClass.PREFERENCE,
        key="summary.language",
        value={"value": "tr-TR"},
    )
    assert result.action == "created"
    memory = db.get(Memory, result.memory_id)
    assert memory.stage == WriteStage.DURABLE
    assert memory.explicit is True
    assert memory.confidence == 1.0
    assert memory.provenance_json["origin"] == "owner_statement"
    # Embedding row exists for the deterministic model.
    embeddings = db.execute(select(MemoryEmbedding)).scalars().all()
    assert len(embeddings) == 1
    assert embeddings[0].model_id == EMBEDDER.model_id


def test_preference_readable_from_new_session_same_store(db: Session) -> None:
    """Behavior: a stored preference is not conversation-bound — a second
    session over the same store retrieves it (cross-session continuity; the
    cross-device/new-client variant runs in integration)."""
    result = service.remember_explicit(
        db,
        EMBEDDER,
        text="Owner prefers executive summary first in reports.",
        memory_class=MemoryClass.PREFERENCE,
        key="response.detail",
        value={"value": "executive_first"},
    )
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    fresh = factory()
    try:
        hits = hybrid_search(
            fresh,
            EMBEDDER,
            "executive summary preference",
            RetrievalFilters(memory_class=MemoryClass.PREFERENCE.value),
        )
        assert hits and hits[0].memory.id == result.memory_id
    finally:
        fresh.close()


# ------------------------------------------------------- inference + evidence


def test_weak_inferred_preference_stays_low_confidence(db: Session) -> None:
    """Behavior: single observation never yields a high-confidence durable row."""
    result = _observe(
        db,
        "Owner prefers meetings late in the afternoon.",
        memory_class=MemoryClass.PREFERENCE,
        key="calendar.meeting_time",
        value={"value": "late_afternoon"},
        confidence_hint=0.95,
    )
    memory = db.get(Memory, result.memory_id)
    assert memory.stage == WriteStage.CANDIDATE
    assert memory.explicit is False
    assert memory.confidence <= SINGLE_OBSERVATION_MAX_CONFIDENCE
    assert memory.evidence_count == 1


def test_repeated_evidence_increases_confidence_and_promotes(db: Session) -> None:
    """Behavior: corroborating evidence raises confidence; promotion happens
    exactly when evidence_count >= 3 AND confidence >= 0.7."""
    first = _observe(
        db,
        "Owner prefers meetings late in the afternoon.",
        memory_class=MemoryClass.PREFERENCE,
        key="calendar.meeting_time",
        value={"value": "late_afternoon"},
    )
    memory = db.get(Memory, first.memory_id)
    conf_1 = memory.confidence
    assert memory.stage == WriteStage.CANDIDATE

    second = _observe(
        db,
        "Owner again booked a meeting for the late afternoon slot.",
        memory_class=MemoryClass.PREFERENCE,
        key="calendar.meeting_time",
        value={"value": "late_afternoon"},
    )
    assert second.action == "corroborated"
    assert second.promoted is False
    db.refresh(memory)
    assert memory.confidence > conf_1
    assert memory.stage == WriteStage.CANDIDATE  # 2 < PROMOTE_MIN_EVIDENCE

    third = _observe(
        db,
        "Owner moved another meeting into the late afternoon.",
        memory_class=MemoryClass.PREFERENCE,
        key="calendar.meeting_time",
        value={"value": "late_afternoon"},
    )
    assert third.action == "corroborated"
    assert third.promoted is True
    db.refresh(memory)
    assert memory.stage == WriteStage.DURABLE
    assert memory.evidence_count >= PROMOTE_MIN_EVIDENCE
    assert memory.confidence >= PROMOTE_MIN_CONFIDENCE
    assert "promoted" in _audit_actions(db, memory.id)


def test_semantic_duplicate_merges_as_evidence_not_new_row(db: Session) -> None:
    """Behavior: an unkeyed near-duplicate observation becomes evidence on the
    existing memory instead of a new row."""
    first = _observe(
        db,
        "Owner always reviews the deploy checklist before releasing.",
        memory_class=MemoryClass.SEMANTIC,
    )
    second = _observe(
        db,
        "Owner always reviews the deploy checklist before releasing anything.",
        memory_class=MemoryClass.SEMANTIC,
    )
    assert second.action == "corroborated"
    assert second.memory_id == first.memory_id
    assert len(db.execute(select(Memory)).scalars().all()) == 1


# --------------------------------------------------------------- contradiction


def test_contradictory_inferred_does_not_overwrite_explicit(db: Session) -> None:
    """Behavior: inference NEVER rewrites an explicit owner preference; the
    conflict is audited and surfaced in inspection."""
    taught = service.remember_explicit(
        db,
        EMBEDDER,
        text="I prefer dark mode in the editor.",
        memory_class=MemoryClass.PREFERENCE,
        key="editor.theme",
        value={"value": "dark"},
    )
    result = _observe(
        db,
        "Owner seems to prefer the light theme lately.",
        memory_class=MemoryClass.PREFERENCE,
        key="editor.theme",
        value={"value": "light"},
    )
    assert result.action == "contradicted_explicit_kept"
    memory = db.get(Memory, taught.memory_id)
    assert memory.value_json == {"value": "dark"}
    assert memory.status == MemoryStatus.ACTIVE
    assert memory.explicit is True
    assert "contradicted" in _audit_actions(db, memory.id)
    inspection = service.inspect_memory(db, memory.id)
    assert inspection["conflicts"], "conflict must surface in inspection"
    assert inspection["conflicts"][0]["detail"]["kept"] == "explicit"


def test_inferred_contradiction_decays_and_supersedes_when_better_evidenced(
    db: Session,
) -> None:
    """Behavior: inferred-vs-inferred contradiction decays the incumbent and
    supersedes it once the challenger is better-evidenced."""
    incumbent = _observe(
        db,
        "Owner prefers tea in the morning routine.",
        memory_class=MemoryClass.PREFERENCE,
        key="drink.morning",
        value={"value": "tea"},
    )
    old = db.get(Memory, incumbent.memory_id)
    conf_before = old.confidence

    r1 = _observe(
        db,
        "Owner prefers coffee in the morning routine.",
        memory_class=MemoryClass.PREFERENCE,
        key="drink.morning",
        value={"value": "coffee"},
    )
    assert r1.action == "contradiction_recorded"
    db.refresh(old)
    assert old.confidence < conf_before  # decayed
    assert old.status == MemoryStatus.ACTIVE  # not yet superseded

    r2 = _observe(
        db,
        "Owner again asked for coffee during the morning routine.",
        memory_class=MemoryClass.PREFERENCE,
        key="drink.morning",
        value={"value": "coffee"},
    )
    assert r2.action == "superseded_previous"
    db.refresh(old)
    assert old.status == MemoryStatus.SUPERSEDED
    assert old.superseded_by == r2.memory_id
    challenger = db.get(Memory, r2.memory_id)
    assert challenger.evidence_count > 0
    assert challenger.status == MemoryStatus.ACTIVE


# ------------------------------------------------------------------ supersede


def test_old_preference_can_be_superseded(db: Session) -> None:
    """Behavior: superseding keeps the old row (status=superseded +
    superseded_by) and retrieval only sees the new one."""
    taught = service.remember_explicit(
        db,
        EMBEDDER,
        text="I prefer summaries in English.",
        memory_class=MemoryClass.PREFERENCE,
        key="summary.language",
        value={"value": "en"},
    )
    new = service.supersede_memory(
        db,
        EMBEDDER,
        taught.memory_id,
        actor=Actor.OWNER,
        text="I prefer summaries in Turkish now.",
        value={"value": "tr-TR"},
        reason="owner changed language preference",
    )
    old = db.get(Memory, taught.memory_id)
    assert old.status == MemoryStatus.SUPERSEDED
    assert old.superseded_by == new.id
    hits = hybrid_search(
        db,
        EMBEDDER,
        "summaries language preference",
        RetrievalFilters(memory_class=MemoryClass.PREFERENCE.value),
    )
    ids = [h.memory.id for h in hits]
    assert new.id in ids
    assert old.id not in ids  # superseded rows excluded ALWAYS


def test_explicit_reteach_supersedes_previous_value(db: Session) -> None:
    """Behavior: owner re-teaching a keyed preference with a new value
    supersedes the old explicit row via /observe semantics."""
    taught = service.remember_explicit(
        db,
        EMBEDDER,
        text="I prefer light mode in the editor.",
        memory_class=MemoryClass.PREFERENCE,
        key="editor.theme",
        value={"value": "light"},
    )
    result = _observe(
        db,
        "From now on use dark mode in the editor.",
        memory_class=MemoryClass.PREFERENCE,
        key="editor.theme",
        value={"value": "dark"},
        explicit=True,
    )
    assert result.action == "superseded_previous"
    old = db.get(Memory, taught.memory_id)
    assert old.status == MemoryStatus.SUPERSEDED


# ------------------------------------------------------- inspection + editing


def test_owner_can_inspect_why_a_memory_exists(db: Session) -> None:
    """Behavior: inspection returns provenance + evidence rows + version
    history + audit trail."""
    result = _observe(
        db,
        "Owner prefers short bullet-point emails.",
        memory_class=MemoryClass.PREFERENCE,
        key="email.style",
        value={"value": "bullets"},
    )
    _observe(
        db,
        "Owner sent another bullet-style email today.",
        memory_class=MemoryClass.PREFERENCE,
        key="email.style",
        value={"value": "bullets"},
    )
    inspection = service.inspect_memory(db, result.memory_id)
    assert inspection["provenance"]["origin"] == "observation"
    assert inspection["provenance"]["policy_reason"]
    assert len(inspection["evidence"]) == 2
    assert [v["version"] for v in inspection["versions"]] == [1]
    actions = [a["action"] for a in inspection["audit"]]
    assert "created" in actions and "corroborated" in actions


def test_owner_can_correct_memory_version_history_grows(db: Session) -> None:
    """Behavior: owner correction -> version+1, new version row, content
    updated, re-embedded."""
    result = service.remember_explicit(
        db,
        EMBEDDER,
        text="Owner's invoice day is the third of the month.",
        memory_class=MemoryClass.SEMANTIC,
        key="billing.invoice_day",
        value={"value": 3},
    )
    before_vec = db.execute(select(MemoryEmbedding)).scalars().one().embedding
    edited = service.edit_memory(
        db,
        EMBEDDER,
        result.memory_id,
        actor=Actor.OWNER,
        text="Owner's invoice day is the fifth of the month.",
        value={"value": 5},
        change_reason="owner correction",
    )
    assert edited.version == 2
    assert edited.value_json == {"value": 5}
    versions = db.execute(select(MemoryVersion).order_by(MemoryVersion.version)).scalars().all()
    assert [v.version for v in versions] == [1, 2]
    assert versions[1].edited_by == Actor.OWNER
    assert versions[1].change_reason == "owner correction"
    after_vec = db.execute(select(MemoryEmbedding)).scalars().one().embedding
    assert list(before_vec) != list(after_vec)  # re-embedded on text change


def test_explicit_memory_rejects_non_owner_edit(db: Session) -> None:
    """Behavior: explicit owner memories only changeable by Actor.OWNER."""
    result = service.remember_explicit(
        db,
        EMBEDDER,
        text="I prefer metric units everywhere.",
        memory_class=MemoryClass.PREFERENCE,
        key="units.system",
        value={"value": "metric"},
    )
    with pytest.raises(MemorySubsystemError) as excinfo:
        service.edit_memory(
            db,
            EMBEDDER,
            result.memory_id,
            actor=Actor.POLICY,
            text="Owner prefers imperial units.",
        )
    assert excinfo.value.error_class == MemoryErrorClass.EXPLICIT_PROTECTED


def test_pin_sets_pinned_and_retention(db: Session) -> None:
    result = _observe(
        db,
        "Owner always archives invoices as PDF.",
        memory_class=MemoryClass.PROCEDURAL,
        key="procedure:invoice-archive",
    )
    memory = service.pin_memory(db, result.memory_id, actor=Actor.OWNER)
    assert memory.pinned is True
    assert memory.retention_class == RetentionClass.PINNED
    assert "pinned" in _audit_actions(db, memory.id)


# --------------------------------------------------------------------- forget


def test_owner_can_forget_memory_hard_delete(db: Session) -> None:
    """Behavior: forget hard-deletes the row + versions + evidence +
    embeddings; the audit row records counts but NEVER content."""
    result = service.remember_explicit(
        db,
        EMBEDDER,
        text="Owner's dentist appointment ritual happens every March.",
        memory_class=MemoryClass.EPISODIC,
    )
    memory_id = result.memory_id
    counts = service.forget_memory(db, memory_id, actor=Actor.OWNER)
    assert counts == {"versions": 1, "evidence": 1, "embeddings": 1}
    assert db.get(Memory, memory_id) is None
    assert db.execute(select(MemoryVersion)).scalars().all() == []
    assert db.execute(select(MemoryEvidence)).scalars().all() == []
    assert db.execute(select(MemoryEmbedding)).scalars().all() == []
    event = db.execute(
        select(MemoryAuditEvent).where(MemoryAuditEvent.action == "forgotten")
    ).scalars().one()
    blob = str(event.detail_json) + str(event.key or "")
    assert "dentist" not in blob.lower()  # audit must not carry content


def test_forgotten_memory_absent_from_fallback_semantic_and_structured(db: Session) -> None:
    """Behavior (unit variant): deleted memory is gone from both retrieval
    paths (the pgvector proof runs in integration)."""
    result = service.remember_explicit(
        db,
        EMBEDDER,
        text="Owner keeps the yoga class on Tuesday evenings.",
        memory_class=MemoryClass.EPISODIC,
    )
    service.forget_memory(db, result.memory_id, actor=Actor.OWNER)
    hits = hybrid_search(db, EMBEDDER, "yoga class Tuesday", RetrievalFilters())
    assert all(h.memory.id != result.memory_id for h in hits)
    structured = hybrid_search(
        db, EMBEDDER, None, RetrievalFilters(memory_class=MemoryClass.EPISODIC.value)
    )
    assert all(h.memory.id != result.memory_id for h in structured)


# -------------------------------------------------------------- secrets guard


def test_secrets_guard_refuses_and_audits_without_content(db: Session) -> None:
    """Behavior: storing a token is refused with a typed error; the audit
    records the pattern name only."""
    with pytest.raises(MemorySubsystemError) as excinfo:
        _observe(
            db,
            "Remember my GitHub token ghp_" + "a" * 30,
            memory_class=MemoryClass.SEMANTIC,
            explicit=True,
        )
    assert excinfo.value.error_class == MemoryErrorClass.SECRET_REJECTED
    assert db.execute(select(Memory)).scalars().all() == []  # no row
    event = db.execute(
        select(MemoryAuditEvent).where(MemoryAuditEvent.action == "refused_secret")
    ).scalars().one()
    assert event.detail_json == {"pattern": "github_token"}
    assert "ghp_" not in str(event.detail_json)


# ------------------------------------------------------------------ lifecycle


def test_retention_sweeper_expires_session_rows(db: Session) -> None:
    """Behavior: session-retention rows expire after their TTL; standard and
    pinned rows survive."""
    session_row = _observe(db, "Owner glanced at the weather widget briefly today.")
    assert db.get(Memory, session_row.memory_id).retention_class == RetentionClass.SESSION
    durable = service.remember_explicit(
        db, EMBEDDER, text="I prefer tea at breakfast.", memory_class=MemoryClass.PREFERENCE
    )
    swept = lifecycle.sweep_expired(
        db, now=datetime.now(UTC) + lifecycle.SESSION_TTL + timedelta(minutes=1)
    )
    assert swept == 1
    assert db.get(Memory, session_row.memory_id) is None
    assert db.get(Memory, durable.memory_id) is not None


def test_reindex_rebuilds_embeddings_for_new_model(db: Session) -> None:
    """Behavior: re-embedding migration builds rows for the new (model_id,
    model_version) without touching canonical memory rows."""

    class NewEmbedder(DeterministicEmbedder):
        model_id = "deterministic-ngram-v2"
        model_version = "2"

    service.remember_explicit(db, EMBEDDER, text="I prefer window seats on trains.",
                              memory_class=MemoryClass.PREFERENCE)
    service.remember_explicit(db, EMBEDDER, text="I prefer aisle seats on planes.",
                              memory_class=MemoryClass.PREFERENCE)
    count = lifecycle.reindex(db, NewEmbedder(seed="pagentos-memory-v2"))
    assert count == 2
    embeddings = db.execute(select(MemoryEmbedding)).scalars().all()
    models = {e.model_id for e in embeddings}
    assert models == {"deterministic-ngram", "deterministic-ngram-v2"}
    assert len(embeddings) == 4  # two per memory, one per model


def test_procedural_pattern_proposed_from_repeated_episodes(db: Session) -> None:
    """Behavior: >= 3 similar episodic sequences yield a PROPOSED procedure
    memory (candidate stage, capped confidence) without touching app code."""
    for day in (1, 2, 3):
        _observe(
            db,
            f"Ran the invoice export workflow on day {day}.",
            memory_class=MemoryClass.EPISODIC,
            value={"steps": ["export-csv", "validate", "load"]},
            links=MemoryLinks(occurred_at=datetime(2026, 8, day, tzinfo=UTC)),
        )
    proposals = lifecycle.detect_procedures(db, EMBEDDER)
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.memory_class == MemoryClass.PROCEDURAL
    assert proposal.stage == WriteStage.CANDIDATE  # proposal only, never durable
    assert proposal.confidence <= SINGLE_OBSERVATION_MAX_CONFIDENCE
    assert proposal.evidence_count == 3
    assert "procedure_proposed" in _audit_actions(db, proposal.id)
    # Idempotent: running again does not duplicate the proposal.
    assert lifecycle.detect_procedures(db, EMBEDDER) == []


# -------------------------------------------------------------- entity graph


def test_entity_graph_links_task_artifact_conversation_project(db: Session) -> None:
    """Behavior: task -> artifact -> conversation -> project relationships are
    retrievable via the entity graph and memory link filters."""
    project = service.create_entity(db, kind="project", name="atlas")
    task = service.create_entity(db, kind="task", name="research-voice-latency")
    artifact = service.create_entity(db, kind="document", name="voice-latency-report")
    service.create_edge(db, src_id=task.id, dst_id=project.id, relation="belongs_to")
    service.create_edge(db, src_id=artifact.id, dst_id=task.id, relation="produced_by")

    task_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    _observe(
        db,
        "Owner decided the latency budget for Atlas voice control.",
        memory_class=MemoryClass.PROJECT,
        key="atlas.latency_budget",
        links=MemoryLinks(
            project_id=project.id,
            task_id=task_id,
            artifact_id=artifact_id,
            conversation_id=conversation_id,
        ),
    )
    edges = service.entity_edges(db, project.id)
    assert any(e.relation == "belongs_to" for e in edges)
    by_task = hybrid_search(db, EMBEDDER, None, RetrievalFilters(task_id=task_id))
    by_artifact = hybrid_search(db, EMBEDDER, None, RetrievalFilters(artifact_id=artifact_id))
    by_convo = hybrid_search(
        db, EMBEDDER, None, RetrievalFilters(conversation_id=conversation_id)
    )
    by_project = hybrid_search(db, EMBEDDER, None, RetrievalFilters(project_id=project.id))
    assert len(by_task) == len(by_artifact) == len(by_convo) == len(by_project) == 1


# --------------------------------------------------------------------- audit


def test_audit_trail_records_all_mutations(db: Session) -> None:
    """Behavior: every mutation writes memory_audit_events (with trace ids)."""
    result = service.remember_explicit(
        db, EMBEDDER, text="I prefer green tea after lunch.",
        memory_class=MemoryClass.PREFERENCE, key="drink.afternoon",
    )
    service.edit_memory(
        db, EMBEDDER, result.memory_id, actor=Actor.OWNER,
        text="I prefer black tea after lunch.", change_reason="correction",
    )
    service.pin_memory(db, result.memory_id, actor=Actor.OWNER)
    service.forget_memory(db, result.memory_id, actor=Actor.OWNER)
    actions = _audit_actions(db, result.memory_id)
    assert actions == ["created", "edited", "pinned", "forgotten"]
    events = service.list_audit_events(db, limit=10)
    assert events[0]["action"] == "forgotten"  # newest first
