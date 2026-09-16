"""Memory lifecycle mechanics: audit, embeddings, evidence/promotion,
retention sweep, re-embedding migration and procedural pattern detection.

Cross-cutting bookkeeping used by service.py. Everything here is synchronous
session-scoped code (routes wrap calls in asyncio.to_thread, matching the
artifacts/voice modules).

Confidence model (deterministic, documented):
    confidence_for_evidence(n) = min(0.95, 0.3 + 0.2 * (n - 1))
so 1 observation -> 0.3 (under the 0.4 single-observation cap), 3 corroborating
observations -> 0.7 which is exactly the promotion threshold. Promotion to
durable requires BOTH evidence_count >= PROMOTE_MIN_EVIDENCE and confidence >=
PROMOTE_MIN_CONFIDENCE (types.py, frozen). A contradiction against an inferred
memory decays its confidence by CONTRADICTION_DECAY.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging import get_logger, trace_id_var
from app.memory.embedding import Embedder
from app.memory.models import (
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.memory.types import (
    PROMOTE_MIN_CONFIDENCE,
    PROMOTE_MIN_EVIDENCE,
    Actor,
    MemoryClass,
    MemoryStatus,
    RetentionClass,
    WriteStage,
)

logger = get_logger("app.memory.lifecycle")

BASE_CONFIDENCE = 0.3
EVIDENCE_STEP = 0.2
MAX_INFERRED_CONFIDENCE = 0.95
CONTRADICTION_DECAY = 0.15
MIN_CONFIDENCE = 0.05

# Retention sweep windows (session/short ladder rungs; pinned is never swept).
SESSION_TTL = timedelta(hours=24)
SHORT_TTL = timedelta(days=7)

# Procedural detection: >= this many similar episodic sequences propose a
# procedure memory (proposal only: CANDIDATE stage, capped confidence).
PROCEDURE_MIN_OCCURRENCES = 3


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; normalize to UTC-aware for arithmetic."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def confidence_for_evidence(count: int) -> float:
    if count <= 0:
        return 0.0
    return min(MAX_INFERRED_CONFIDENCE, BASE_CONFIDENCE + EVIDENCE_STEP * (count - 1))


# ------------------------------------------------------------------------ audit


def record_audit(
    session: Session,
    *,
    action: str,
    memory_id: uuid.UUID | None,
    memory_class: str | None,
    key: str | None,
    actor: Actor,
    detail: dict[str, Any] | None = None,
) -> MemoryAuditEvent:
    """Append one audit event carrying the current trace_id. Never content-free
    guarantees are violated by callers: `forget`/`refused_secret` details must
    not include memory text/value."""
    event = MemoryAuditEvent(
        action=action,
        memory_id=memory_id,
        memory_class=memory_class,
        key=key,
        actor=actor.value,
        detail_json=detail or {},
        trace_id=trace_id_var.get(),
    )
    session.add(event)
    logger.info(
        "memory_audit",
        action=action,
        memory_id=str(memory_id) if memory_id else None,
        memory_class=memory_class,
        actor=actor.value,
    )
    return event


# ------------------------------------------------------------------- embeddings


def upsert_embedding(session: Session, embedder: Embedder, memory: Memory) -> MemoryEmbedding:
    """Create/replace the embedding row for (memory, embedder.model_id).

    Called on create and on every text edit (re-embed on change). The vector
    column is pgvector on PostgreSQL; on SQLite the ORM stores the value via the
    generic type and semantic retrieval uses the in-memory fallback.
    """
    row = session.execute(
        select(MemoryEmbedding).where(
            MemoryEmbedding.memory_id == memory.id,
            MemoryEmbedding.model_id == embedder.model_id,
        )
    ).scalar_one_or_none()
    vector = embedder.embed(memory.text)
    if row is None:
        row = MemoryEmbedding(
            memory_id=memory.id,
            model_id=embedder.model_id,
            model_version=embedder.model_version,
            dim=embedder.dim,
            embedding=vector,
        )
        session.add(row)
    else:
        row.model_version = embedder.model_version
        row.dim = embedder.dim
        row.embedding = vector
    return row


def reindex(session: Session, new_embedder: Embedder) -> int:
    """Re-embedding migration: rebuild memory_embeddings rows for a new
    (model_id, model_version) across ALL memories, never touching canonical
    rows. Returns the number of rebuilt rows."""
    memories = session.execute(select(Memory)).scalars().all()
    count = 0
    for memory in memories:
        upsert_embedding(session, new_embedder, memory)
        count += 1
    session.flush()
    record_audit(
        session,
        action="reindexed",
        memory_id=None,
        memory_class=None,
        key=None,
        actor=Actor.SYSTEM,
        detail={
            "model_id": new_embedder.model_id,
            "model_version": new_embedder.model_version,
            "dim": new_embedder.dim,
            "rows": count,
        },
    )
    session.commit()
    return count


def embedding_coverage(session: Session, embedder: Embedder) -> dict[str, Any]:
    """B37 req 54: how much of the memory table the ACTIVE model has indexed, and how
    many rows every model holds - the number a re-index is judged by."""
    memories = int(session.scalar(select(func.count()).select_from(Memory)) or 0)
    per_model = {
        str(model_id): int(count)
        for model_id, count in session.execute(
            select(MemoryEmbedding.model_id, func.count()).group_by(MemoryEmbedding.model_id)
        )
    }
    embedded = per_model.get(embedder.model_id, 0)
    return {
        "model_id": embedder.model_id,
        "memories": memories,
        "embedded": embedded,
        "missing": max(0, memories - embedded),
        "models": dict(sorted(per_model.items())),
    }


def reindex_missing(session: Session, embedder: Embedder) -> int:
    """B37 req 54: embed only the memories that have no row for this model - the
    resumable half of a provider change. Returns the rows written; a second pass
    writes none."""
    done = select(MemoryEmbedding.memory_id).where(MemoryEmbedding.model_id == embedder.model_id)
    memories = session.execute(select(Memory).where(Memory.id.not_in(done))).scalars().all()
    count = 0
    for memory in memories:
        upsert_embedding(session, embedder, memory)
        count += 1
    if count:
        session.flush()
        record_audit(
            session,
            action="reindexed",
            memory_id=None,
            memory_class=None,
            key=None,
            actor=Actor.SYSTEM,
            detail={
                "model_id": embedder.model_id,
                "model_version": embedder.model_version,
                "dim": embedder.dim,
                "rows": count,
                "only_missing": True,
            },
        )
    session.commit()
    return count


# ------------------------------------------------------- evidence + promotion


def snapshot_version(
    session: Session,
    memory: Memory,
    *,
    edited_by: Actor,
    change_reason: str,
) -> MemoryVersion:
    """Append the memory's CURRENT state as an immutable version row."""
    row = MemoryVersion(
        memory_id=memory.id,
        version=memory.version,
        text=memory.text,
        value_json=dict(memory.value_json or {}),
        stage=memory.stage,
        explicit=memory.explicit,
        confidence=memory.confidence,
        edited_by=edited_by.value,
        change_reason=change_reason,
    )
    session.add(row)
    return row


def add_evidence(
    session: Session,
    memory: Memory,
    *,
    kind: str,
    source_ref: dict[str, Any] | None = None,
    weight: float = 1.0,
) -> MemoryEvidence:
    """Record one corroborating observation and recompute confidence."""
    evidence = MemoryEvidence(
        memory_id=memory.id,
        kind=kind,
        source_ref_json=source_ref or {},
        weight=weight,
    )
    session.add(evidence)
    memory.evidence_count += 1
    memory.confidence = min(
        MAX_INFERRED_CONFIDENCE if not memory.explicit else 1.0,
        max(memory.confidence, confidence_for_evidence(memory.evidence_count)),
    )
    memory.last_confirmed_at = utcnow()
    memory.updated_at = utcnow()
    return evidence


def maybe_promote(session: Session, memory: Memory) -> bool:
    """Promote candidate/session -> durable when BOTH thresholds are met."""
    if memory.stage == WriteStage.DURABLE:
        return False
    if memory.evidence_count < PROMOTE_MIN_EVIDENCE:
        return False
    if memory.confidence < PROMOTE_MIN_CONFIDENCE:
        return False
    previous = memory.stage
    memory.stage = WriteStage.DURABLE.value
    if memory.retention_class == RetentionClass.SESSION:
        memory.retention_class = RetentionClass.STANDARD.value
    memory.updated_at = utcnow()
    record_audit(
        session,
        action="promoted",
        memory_id=memory.id,
        memory_class=memory.memory_class,
        key=memory.key,
        actor=Actor.POLICY,
        detail={
            "from_stage": previous,
            "evidence_count": memory.evidence_count,
            "confidence": memory.confidence,
        },
    )
    return True


def apply_contradiction_decay(session: Session, memory: Memory) -> float:
    """Contradicting evidence against an INFERRED memory lowers its confidence."""
    memory.confidence = max(MIN_CONFIDENCE, memory.confidence - CONTRADICTION_DECAY)
    memory.updated_at = utcnow()
    return memory.confidence


# --------------------------------------------------------------- retention sweep


def sweep_expired(session: Session, *, now: datetime | None = None) -> int:
    """Hard-delete expired session/short-retention rows (never pinned/explicit).

    session-retention rows expire after SESSION_TTL, short-retention rows after
    SHORT_TTL (measured from last confirmation or creation). Deletion follows
    the forget path semantics: children removed in the same transaction, audit
    row without content.
    """
    now = now or utcnow()
    candidates = (
        session.execute(
            select(Memory).where(
                Memory.retention_class.in_(
                    [RetentionClass.SESSION.value, RetentionClass.SHORT.value]
                ),
                Memory.pinned.is_(False),
                Memory.explicit.is_(False),
            )
        )
        .scalars()
        .all()
    )
    swept = 0
    for memory in candidates:
        anchor = aware(memory.last_confirmed_at) or aware(memory.created_at) or now
        ttl = SESSION_TTL if memory.retention_class == RetentionClass.SESSION else SHORT_TTL
        if now - anchor < ttl:
            continue
        hard_delete_memory(session, memory, actor=Actor.SYSTEM, reason="retention_expired")
        swept += 1
    if swept:
        session.commit()
    return swept


def hard_delete_memory(
    session: Session, memory: Memory, *, actor: Actor, reason: str
) -> dict[str, int]:
    """Forget = HARD delete: versions, evidence and embeddings go with the row
    (dialect-safe explicit child deletes; PostgreSQL would cascade anyway).
    The audit row records identifiers and counts, NEVER the content."""
    memory_id = memory.id
    memory_class = memory.memory_class
    key = memory.key

    versions = session.query(MemoryVersion).filter_by(memory_id=memory_id).delete()
    evidence = session.query(MemoryEvidence).filter_by(memory_id=memory_id).delete()
    embeddings = session.query(MemoryEmbedding).filter_by(memory_id=memory_id).delete()
    # Clear dangling supersession pointers (FK is SET NULL on PostgreSQL).
    session.query(Memory).filter(Memory.superseded_by == memory_id).update(
        {Memory.superseded_by: None}
    )
    session.delete(memory)
    counts = {"versions": versions, "evidence": evidence, "embeddings": embeddings}
    record_audit(
        session,
        action="forgotten",
        memory_id=memory_id,
        memory_class=memory_class,
        key=key,
        actor=actor,
        detail={"reason": reason, "deleted": counts},
    )
    return counts


# ------------------------------------------------------ procedural detection

_WORDS = re.compile(r"[a-z0-9çğıöşü]+")


def _episode_signature(memory: Memory) -> str | None:
    """Stable signature of an episodic workflow observation.

    Prefers an explicit step list in value_json ("steps": [...]) and falls back
    to the normalized text. Returns None when there is nothing to normalize.
    """
    value = memory.value_json or {}
    steps = value.get("steps")
    if isinstance(steps, list) and steps:
        return ">".join(str(s).strip().lower() for s in steps)
    words = _WORDS.findall(memory.text.lower())
    if not words:
        return None
    return " ".join(words)


def detect_procedures(
    session: Session,
    embedder: Embedder,
    *,
    min_occurrences: int = PROCEDURE_MIN_OCCURRENCES,
) -> list[Memory]:
    """Propose procedure memories from repeated episodic sequences.

    PROPOSAL ONLY: the created procedural memory enters at CANDIDATE stage with
    capped confidence and actor=POLICY — it never self-promotes here, and no
    application code is modified. Each proposal carries evidence rows pointing
    at the supporting episodes.
    """
    episodes = (
        session.execute(
            select(Memory).where(
                Memory.memory_class == MemoryClass.EPISODIC.value,
                Memory.status == MemoryStatus.ACTIVE.value,
            )
        )
        .scalars()
        .all()
    )
    groups: dict[tuple[str, uuid.UUID | None], list[Memory]] = {}
    for episode in episodes:
        signature = _episode_signature(episode)
        if signature is None:
            continue
        groups.setdefault((signature, episode.project_id), []).append(episode)

    proposals: list[Memory] = []
    for (signature, project_id), members in sorted(groups.items(), key=lambda kv: kv[0][0]):
        if len(members) < min_occurrences:
            continue
        digest = hashlib.sha256(signature.encode()).hexdigest()[:16]
        key = f"procedure:{digest}"
        existing = session.execute(
            select(Memory).where(
                Memory.memory_class == MemoryClass.PROCEDURAL.value,
                Memory.key == key,
                Memory.status == MemoryStatus.ACTIVE.value,
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        proposal = Memory(
            memory_class=MemoryClass.PROCEDURAL.value,
            key=key,
            text=f"Proposed procedure observed {len(members)} times: {members[0].text}",
            value_json={
                "signature": signature,
                "occurrences": len(members),
                "episode_ids": [str(m.id) for m in members],
            },
            stage=WriteStage.CANDIDATE.value,
            explicit=False,
            confidence=0.4,  # SINGLE_OBSERVATION_MAX_CONFIDENCE: proposal, not fact
            evidence_count=0,
            retention_class=RetentionClass.STANDARD.value,
            project_id=project_id,
            provenance_json={
                "origin": "procedure_detection",
                "episode_ids": [str(m.id) for m in members],
            },
        )
        session.add(proposal)
        session.flush()
        for member in members:
            add_evidence(
                session,
                proposal,
                kind="episodic_occurrence",
                source_ref={"memory_id": str(member.id)},
            )
        # Evidence bookkeeping raised confidence via the formula; clamp back to
        # the proposal cap — promotion is an owner/behavioral confirmation step.
        proposal.confidence = 0.4
        snapshot_version(
            session, proposal, edited_by=Actor.POLICY, change_reason="procedure proposal"
        )
        upsert_embedding(session, embedder, proposal)
        record_audit(
            session,
            action="procedure_proposed",
            memory_id=proposal.id,
            memory_class=proposal.memory_class,
            key=proposal.key,
            actor=Actor.POLICY,
            detail={"occurrences": len(members)},
        )
        proposals.append(proposal)
    if proposals:
        session.commit()
    return proposals


__all__ = [
    "CONTRADICTION_DECAY",
    "PROCEDURE_MIN_OCCURRENCES",
    "SESSION_TTL",
    "SHORT_TTL",
    "add_evidence",
    "apply_contradiction_decay",
    "aware",
    "confidence_for_evidence",
    "detect_procedures",
    "hard_delete_memory",
    "maybe_promote",
    "record_audit",
    "reindex",
    "snapshot_version",
    "sweep_expired",
    "upsert_embedding",
    "utcnow",
]
