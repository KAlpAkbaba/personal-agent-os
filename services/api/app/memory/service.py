"""Memory service: observation intake, explicit teach, correction, supersede,
pin, forget, inspection, audit and the entity graph.

Semantics (M5 brief; frozen foundation invariants respected):
- Duplicate detection: same class+key hits the keyed path; unkeyed
  observations merge into an existing same-class memory when cosine
  similarity >= DEDUP_SIMILARITY (corroborating evidence, not a new row).
- Contradiction handling: an inferred value contradicting an EXPLICIT active
  memory NEVER overwrites it — a `contradicted` audit event is recorded and
  the conflict surfaces in inspection. Contradicting another INFERRED memory
  decays its confidence and tracks the competing value as its own row; when
  the challenger becomes better-evidenced it supersedes the incumbent.
- Explicit memories are only changeable by Actor.OWNER (typed error otherwise).
- Forget is a HARD delete cascading to versions/evidence/embeddings, audited
  WITHOUT content.
- Every mutation writes memory_audit_events with the current trace_id.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging import get_logger, trace_id_var
from app.memory import lifecycle, policy, retrieval
from app.memory.embedding import Embedder
from app.memory.errors import MemoryErrorClass, MemorySubsystemError
from app.memory.lifecycle import record_audit, snapshot_version, upsert_embedding, utcnow
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEvidence,
    MemoryVersion,
)
from app.memory.policy import (
    ACTION_IGNORE,
    ACTION_REFUSE,
    Observation,
    WriteDecision,
    find_secret,
)
from app.memory.types import (
    ENTITY_KINDS,
    SINGLE_OBSERVATION_MAX_CONFIDENCE,
    Actor,
    MemoryClass,
    MemoryStatus,
    RetentionClass,
    WriteStage,
)

logger = get_logger("app.memory.service")

# Unkeyed same-class observations at/above this cosine similarity merge into
# the existing memory as corroborating evidence instead of a new row.
DEDUP_SIMILARITY = 0.80


@dataclass(slots=True)
class MemoryLinks:
    """Optional relationship links attached at write time."""

    project_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    artifact_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    occurred_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None


@dataclass(slots=True)
class ObserveResult:
    action: str  # ignored|created|corroborated|contradicted_explicit_kept|
    # contradiction_recorded|superseded_previous
    memory_id: uuid.UUID | None = None
    stage: str | None = None
    promoted: bool = False
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _values_equal(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    return (a or {}) == (b or {})


def _same_text(a: str | None, b: str | None) -> bool:
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


def _states_the_same_thing(memory: Memory, obs: Observation) -> bool:
    """Whether an observation says what a keyed incumbent already says.

    B17 (2026-09-13). This used to be ``_values_equal`` alone, and that made the keyed
    conflict branch below unreachable for every memory this product actually writes. A
    memory taught by voice or extracted from a conversation carries NO structured value —
    ``{} == {}`` is true for any pair of them — so "Kahveyi sade severim." and "Kahveyi az
    şekerli severim." under one key were the same statement.

    Measured, not reasoned about: teaching the second one recorded it as a second piece of
    EVIDENCE for the first. One row, still reading "sade", evidence count two. The owner
    corrected themselves and the system got more confident in the thing they corrected.
    That is also the whole answer to why requirement 45 (explicit outranks inferred) was
    recorded as "written, never triggered": the branch it lives in could not be reached.

    A structured value is still the precise statement and still decides when either side
    has one. When neither does, the text decides. When one has a value and the other does
    not, they are not the same statement — an observation that pins a value down is saying
    something the bare sentence was not.
    """
    theirs = memory.value_json or {}
    ours = obs.value or {}
    if theirs or ours:
        return theirs == ours
    return _same_text(memory.text, obs.text)


def _active_by_key(session: Session, memory_class: str, key: str) -> list[Memory]:
    return list(
        session.execute(
            select(Memory)
            .where(
                Memory.memory_class == memory_class,
                Memory.key == key,
                Memory.status == MemoryStatus.ACTIVE.value,
            )
            .order_by(Memory.created_at)
        )
        .scalars()
        .all()
    )


def _new_memory(
    session: Session,
    embedder: Embedder,
    obs: Observation,
    decision: WriteDecision,
    links: MemoryLinks,
) -> Memory:
    memory = Memory(
        memory_class=obs.memory_class.value,
        key=obs.key,
        text=obs.text,
        value_json=dict(obs.value or {}),
        stage=decision.action if isinstance(decision.action, str) else str(decision.action),
        status=MemoryStatus.ACTIVE.value,
        explicit=decision.explicit,
        confidence=decision.confidence,
        evidence_count=0,
        retention_class=decision.retention_class.value,
        project_id=links.project_id,
        conversation_id=links.conversation_id,
        task_id=links.task_id,
        artifact_id=links.artifact_id,
        device_id=links.device_id,
        occurred_at=links.occurred_at,
        valid_from=links.valid_from,
        valid_until=links.valid_until,
        provenance_json={
            "origin": "owner_statement" if decision.explicit else "observation",
            "source": dict(obs.source or {}),
            "policy_reason": decision.reason,
            "trace_id": trace_id_var.get(),
        },
        version=1,
        last_confirmed_at=utcnow(),
    )
    session.add(memory)
    session.flush()
    lifecycle.add_evidence(
        session,
        memory,
        kind="owner_statement" if decision.explicit else "observation",
        source_ref=dict(obs.source or {}),
    )
    # add_evidence must not lift a policy-capped single observation above the
    # single-observation ceiling, nor lower an explicit 1.0.
    if decision.explicit:
        memory.confidence = decision.confidence
    else:
        memory.confidence = min(
            max(memory.confidence, decision.confidence), SINGLE_OBSERVATION_MAX_CONFIDENCE
        )
    snapshot_version(session, memory, edited_by=decision.actor, change_reason="created")
    upsert_embedding(session, embedder, memory)
    record_audit(
        session,
        action="created",
        memory_id=memory.id,
        memory_class=memory.memory_class,
        key=memory.key,
        actor=decision.actor,
        detail={"stage": memory.stage, "explicit": memory.explicit},
    )
    return memory


def _corroborate(
    session: Session,
    memory: Memory,
    obs: Observation,
    *,
    actor: Actor,
) -> bool:
    # Explicit owner memories are only mutated by the owner — that includes
    # bookkeeping (evidence_count/confidence/last_confirmed). A POLICY-actor
    # match on an explicit row is a no-op on the row (M5 review #6).
    if memory.explicit and actor != Actor.OWNER:
        return False
    lifecycle.add_evidence(
        session,
        memory,
        kind="corroboration",
        source_ref=dict(obs.source or {}),
    )
    promoted = lifecycle.maybe_promote(session, memory)
    record_audit(
        session,
        action="corroborated",
        memory_id=memory.id,
        memory_class=memory.memory_class,
        key=memory.key,
        actor=actor,
        detail={
            "evidence_count": memory.evidence_count,
            "confidence": memory.confidence,
            "promoted": promoted,
        },
    )
    return promoted


def _supersede(
    session: Session,
    embedder: Embedder,
    old: Memory,
    obs: Observation,
    decision: WriteDecision,
    links: MemoryLinks,
    *,
    reason: str,
) -> Memory:
    new = _new_memory(session, embedder, obs, decision, links)
    old.status = MemoryStatus.SUPERSEDED.value
    old.superseded_by = new.id
    old.updated_at = utcnow()
    record_audit(
        session,
        action="superseded",
        memory_id=old.id,
        memory_class=old.memory_class,
        key=old.key,
        actor=decision.actor,
        detail={"superseded_by": str(new.id), "reason": reason},
    )
    return new


def record_observation(
    session: Session,
    embedder: Embedder,
    obs: Observation,
    links: MemoryLinks | None = None,
) -> ObserveResult:
    """Run the write policy and apply dedup/contradiction semantics.

    Raises MemorySubsystemError(SECRET_REJECTED) — after auditing — for
    secret-like content. Commits on success.
    """
    links = links or MemoryLinks()
    decision = policy.decide(obs)

    if decision.action == ACTION_REFUSE:
        record_audit(
            session,
            action="refused_secret",
            memory_id=None,
            memory_class=obs.memory_class.value,
            key=None,  # the key itself could carry secret-ish hints; omit
            actor=Actor.POLICY,
            detail={"pattern": decision.secret_pattern},
        )
        session.commit()
        raise MemorySubsystemError(
            MemoryErrorClass.SECRET_REJECTED,
            "refused to store secret-like content as memory",
            details={"pattern": decision.secret_pattern},
        )

    if decision.action == ACTION_IGNORE:
        return ObserveResult(action="ignored", reason=decision.reason)

    result = _apply_write(session, embedder, obs, decision, links)
    session.commit()
    return result


def _apply_write(
    session: Session,
    embedder: Embedder,
    obs: Observation,
    decision: WriteDecision,
    links: MemoryLinks,
) -> ObserveResult:
    # ---------------------------------------------------------- keyed path
    if obs.key is not None:
        incumbents = _active_by_key(session, obs.memory_class.value, obs.key)
        same_value = next(
            (m for m in incumbents if _states_the_same_thing(m, obs)), None
        )
        conflicting = [m for m in incumbents if not _states_the_same_thing(m, obs)]

        if same_value is not None and not conflicting:
            if decision.explicit and not same_value.explicit:
                # Owner confirms an inferred memory: it becomes explicit+durable.
                same_value.explicit = True
                same_value.stage = WriteStage.DURABLE.value
                same_value.confidence = 1.0
                same_value.updated_at = utcnow()
                record_audit(
                    session,
                    action="confirmed_explicit",
                    memory_id=same_value.id,
                    memory_class=same_value.memory_class,
                    key=same_value.key,
                    actor=Actor.OWNER,
                    detail={},
                )
            promoted = _corroborate(session, same_value, obs, actor=decision.actor)
            return ObserveResult(
                action="corroborated",
                memory_id=same_value.id,
                stage=same_value.stage,
                promoted=promoted,
                reason=decision.reason,
            )

        if conflicting:
            incumbent = conflicting[-1]
            if incumbent.explicit and not decision.explicit:
                # Inference must never rewrite an explicit owner memory.
                record_audit(
                    session,
                    action="contradicted",
                    memory_id=incumbent.id,
                    memory_class=incumbent.memory_class,
                    key=incumbent.key,
                    actor=Actor.POLICY,
                    detail={
                        "kept": "explicit",
                        "proposed_value": dict(obs.value or {}),
                        "proposed_text": obs.text[:256],
                    },
                )
                return ObserveResult(
                    action="contradicted_explicit_kept",
                    memory_id=incumbent.id,
                    stage=incumbent.stage,
                    reason="explicit owner memory outranks inference",
                )
            if decision.explicit:
                # Owner changed their mind: supersede the incumbent.
                new = _supersede(
                    session, embedder, incumbent, obs, decision, links,
                    reason="explicit owner re-teach",
                )
                return ObserveResult(
                    action="superseded_previous",
                    memory_id=new.id,
                    stage=new.stage,
                    reason=decision.reason,
                    details={"superseded": str(incumbent.id)},
                )
            # Inferred vs inferred: decay the incumbent, grow the challenger.
            lifecycle.apply_contradiction_decay(session, incumbent)
            record_audit(
                session,
                action="contradicted",
                memory_id=incumbent.id,
                memory_class=incumbent.memory_class,
                key=incumbent.key,
                actor=Actor.POLICY,
                detail={
                    "kept": "both",
                    "decayed_confidence": incumbent.confidence,
                    "proposed_value": dict(obs.value or {}),
                },
            )
            if same_value is not None:
                promoted = _corroborate(session, same_value, obs, actor=decision.actor)
                challenger = same_value
            else:
                challenger = _new_memory(session, embedder, obs, decision, links)
                promoted = False
            if (
                challenger.evidence_count > incumbent.evidence_count
                and challenger.confidence >= incumbent.confidence
            ):
                incumbent.status = MemoryStatus.SUPERSEDED.value
                incumbent.superseded_by = challenger.id
                incumbent.updated_at = utcnow()
                record_audit(
                    session,
                    action="superseded",
                    memory_id=incumbent.id,
                    memory_class=incumbent.memory_class,
                    key=incumbent.key,
                    actor=Actor.POLICY,
                    detail={
                        "superseded_by": str(challenger.id),
                        "reason": "better-evidenced contradicting inference",
                    },
                )
                return ObserveResult(
                    action="superseded_previous",
                    memory_id=challenger.id,
                    stage=challenger.stage,
                    promoted=promoted,
                    reason="challenger better-evidenced than incumbent",
                    details={"superseded": str(incumbent.id)},
                )
            return ObserveResult(
                action="contradiction_recorded",
                memory_id=challenger.id,
                stage=challenger.stage,
                promoted=promoted,
                reason="competing inferred value tracked; incumbent decayed",
            )

        memory = _new_memory(session, embedder, obs, decision, links)
        return ObserveResult(
            action="created", memory_id=memory.id, stage=memory.stage, reason=decision.reason
        )

    # -------------------------------------------------------- unkeyed path
    # Episodic memories are time-anchored distinct EVENTS: textually similar
    # episodes (e.g. the same workflow run on different days) must stay
    # separate rows — that repetition is exactly what procedural detection
    # consumes. Semantic dedup therefore skips the episodic class.
    hit = (
        None
        if obs.memory_class == MemoryClass.EPISODIC
        else retrieval.best_similarity(
            session, embedder, obs.text, memory_class=obs.memory_class.value
        )
    )
    if hit is not None and hit[1] >= DEDUP_SIMILARITY:
        memory, similarity = hit
        if decision.explicit and not memory.explicit:
            # Owner explicitly confirms an inferred memory.
            memory.explicit = True
            memory.stage = WriteStage.DURABLE.value
            memory.confidence = 1.0
            memory.updated_at = utcnow()
            record_audit(
                session,
                action="confirmed_explicit",
                memory_id=memory.id,
                memory_class=memory.memory_class,
                key=memory.key,
                actor=Actor.OWNER,
                detail={"via": "semantic_duplicate"},
            )
        promoted = _corroborate(session, memory, obs, actor=decision.actor)
        return ObserveResult(
            action="corroborated",
            memory_id=memory.id,
            stage=memory.stage,
            promoted=promoted,
            reason=f"semantic duplicate (similarity={similarity:.2f})",
        )
    memory = _new_memory(session, embedder, obs, decision, links)
    return ObserveResult(
        action="created", memory_id=memory.id, stage=memory.stage, reason=decision.reason
    )


def remember_explicit(
    session: Session,
    embedder: Embedder,
    *,
    text: str,
    memory_class: Any,
    key: str | None = None,
    value: dict[str, Any] | None = None,
    links: MemoryLinks | None = None,
    source: dict[str, Any] | None = None,
) -> ObserveResult:
    """Explicit owner teach: durable immediately, supersedes a keyed incumbent."""
    obs = Observation(
        text=text,
        memory_class=memory_class,
        key=key,
        value=value or {},
        explicit=True,
        source=source or {},
    )
    return record_observation(session, embedder, obs, links)


# ------------------------------------------------------------------ mutations


def get_memory(session: Session, memory_id: uuid.UUID) -> Memory:
    memory = session.get(Memory, memory_id)
    if memory is None:
        raise MemorySubsystemError(
            MemoryErrorClass.NOT_FOUND, f"unknown memory {memory_id}"
        )
    return memory


def _require_owner_for_explicit(memory: Memory, actor: Actor) -> None:
    # Pinned memories get the same protection: pinning is the owner's "never
    # auto-rewrite/expire this" mark, so no non-owner actor may touch it.
    if (memory.explicit or memory.pinned) and actor != Actor.OWNER:
        raise MemorySubsystemError(
            MemoryErrorClass.EXPLICIT_PROTECTED,
            "explicit/pinned owner memories may only be changed by the owner",
            details={"memory_id": str(memory.id)},
        )


def _reject_if_secret(
    session: Session,
    *,
    text: str | None,
    value: dict[str, Any] | None,
    actor: Actor,
    memory: Memory | None = None,
) -> None:
    """Secrets guard for edit/supersede paths, which bypass policy.decide()
    (M5 review #2). Audits the refusal with the pattern name only."""
    secret = None
    if text:
        secret = find_secret(text)
    if secret is None and value:
        secret = find_secret(repr(value))
    if secret is not None:
        record_audit(
            session,
            action="refused_secret",
            memory_id=memory.id if memory else None,
            memory_class=memory.memory_class if memory else None,
            key=memory.key if memory else None,
            actor=actor,
            detail={"pattern": secret},
        )
        session.commit()
        raise MemorySubsystemError(
            MemoryErrorClass.SECRET_REJECTED,
            "content matches a credential/secret pattern",
            details={"pattern": secret},
        )


def edit_memory(
    session: Session,
    embedder: Embedder,
    memory_id: uuid.UUID,
    *,
    actor: Actor,
    text: str | None = None,
    value: dict[str, Any] | None = None,
    change_reason: str = "",
) -> Memory:
    """Owner correction: version += 1, new version snapshot, re-embed on text
    change. Commits."""
    memory = get_memory(session, memory_id)
    _require_owner_for_explicit(memory, actor)
    if text is None and value is None:
        raise MemorySubsystemError(
            MemoryErrorClass.VALIDATION_ERROR, "edit requires text and/or value"
        )
    _reject_if_secret(session, text=text, value=value, actor=actor, memory=memory)
    text_changed = text is not None and text != memory.text
    if text is not None:
        memory.text = text
    if value is not None:
        memory.value_json = dict(value)
    memory.version += 1
    memory.updated_at = utcnow()
    if actor == Actor.OWNER:
        memory.last_confirmed_at = utcnow()
    snapshot_version(session, memory, edited_by=actor, change_reason=change_reason)
    if text_changed:
        upsert_embedding(session, embedder, memory)
    record_audit(
        session,
        action="edited",
        memory_id=memory.id,
        memory_class=memory.memory_class,
        key=memory.key,
        actor=actor,
        detail={"version": memory.version, "text_changed": text_changed,
                "change_reason": change_reason},
    )
    session.commit()
    return memory


def supersede_memory(
    session: Session,
    embedder: Embedder,
    memory_id: uuid.UUID,
    *,
    actor: Actor,
    text: str,
    value: dict[str, Any] | None = None,
    reason: str = "",
) -> Memory:
    """Replace a memory with a new active row; the old row stays for history
    with status=superseded + superseded_by. Commits."""
    old = get_memory(session, memory_id)
    _require_owner_for_explicit(old, actor)
    _reject_if_secret(session, text=text, value=value, actor=actor, memory=old)
    if old.status != MemoryStatus.ACTIVE.value:
        raise MemorySubsystemError(
            MemoryErrorClass.VALIDATION_ERROR,
            "only active memories can be superseded",
            details={"status": old.status},
        )
    decision = WriteDecision(
        action=old.stage if actor != Actor.OWNER else WriteStage.DURABLE.value,
        explicit=actor == Actor.OWNER,
        confidence=1.0 if actor == Actor.OWNER else old.confidence,
        actor=actor,
        retention_class=RetentionClass(old.retention_class)
        if old.retention_class != RetentionClass.PINNED
        else RetentionClass.STANDARD,
        reason=reason or "superseded by newer memory",
    )
    obs = Observation(
        text=text,
        memory_class=MemoryClass(old.memory_class),
        key=old.key,
        value=value or {},
        explicit=decision.explicit,
    )
    links = MemoryLinks(
        project_id=old.project_id,
        conversation_id=old.conversation_id,
        task_id=old.task_id,
        artifact_id=old.artifact_id,
        device_id=old.device_id,
    )
    new = _supersede(session, embedder, old, obs, decision, links, reason=reason or "supersede")
    session.commit()
    return new


def pin_memory(session: Session, memory_id: uuid.UUID, *, actor: Actor) -> Memory:
    memory = get_memory(session, memory_id)
    _require_owner_for_explicit(memory, actor)
    memory.pinned = True
    memory.retention_class = RetentionClass.PINNED.value
    memory.updated_at = utcnow()
    record_audit(
        session,
        action="pinned",
        memory_id=memory.id,
        memory_class=memory.memory_class,
        key=memory.key,
        actor=actor,
        detail={},
    )
    session.commit()
    return memory


def forget_memory(
    session: Session, memory_id: uuid.UUID, *, actor: Actor, reason: str = "owner_request"
) -> dict[str, int]:
    """HARD delete + audit WITHOUT content. Commits.

    Owner-gated for explicit/pinned rows (M5 review #1): forgetting is the one
    irreversible operation, so the Evolution Engine or any POLICY/SYSTEM actor
    must never be able to erase an explicit owner memory."""
    memory = get_memory(session, memory_id)
    _require_owner_for_explicit(memory, actor)
    counts = lifecycle.hard_delete_memory(session, memory, actor=actor, reason=reason)
    session.commit()
    return counts


# ----------------------------------------------------------------- inspection


def inspect_memory(session: Session, memory_id: uuid.UUID) -> dict[str, Any]:
    """The memory + WHY it exists: provenance, evidence, versions, audit trail
    and surfaced conflicts (contradicted events)."""
    memory = get_memory(session, memory_id)
    evidence = (
        session.execute(
            select(MemoryEvidence)
            .where(MemoryEvidence.memory_id == memory_id)
            .order_by(MemoryEvidence.observed_at, MemoryEvidence.id)
        )
        .scalars()
        .all()
    )
    versions = (
        session.execute(
            select(MemoryVersion)
            .where(MemoryVersion.memory_id == memory_id)
            .order_by(MemoryVersion.version)
        )
        .scalars()
        .all()
    )
    audit = (
        session.execute(
            select(MemoryAuditEvent)
            .where(MemoryAuditEvent.memory_id == memory_id)
            .order_by(MemoryAuditEvent.id)
        )
        .scalars()
        .all()
    )
    payload = retrieval.to_payload(memory)
    payload["provenance"] = memory.provenance_json
    payload["evidence"] = [
        {
            "id": str(e.id),
            "kind": e.kind,
            "source_ref": e.source_ref_json,
            "weight": e.weight,
            "observed_at": lifecycle.aware(e.observed_at).isoformat(),
        }
        for e in evidence
    ]
    payload["versions"] = [
        {
            "version": v.version,
            "text": v.text,
            "value": v.value_json,
            "stage": v.stage,
            "explicit": v.explicit,
            "confidence": v.confidence,
            "edited_by": v.edited_by,
            "change_reason": v.change_reason,
            "created_at": lifecycle.aware(v.created_at).isoformat(),
        }
        for v in versions
    ]
    payload["audit"] = [_audit_payload(a) for a in audit]
    payload["conflicts"] = [
        _audit_payload(a) for a in audit if a.action == "contradicted"
    ]
    return payload


def _audit_payload(event: MemoryAuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "action": event.action,
        "memory_id": str(event.memory_id) if event.memory_id else None,
        "memory_class": event.memory_class,
        "key": event.key,
        "actor": event.actor,
        "detail": event.detail_json,
        "trace_id": event.trace_id,
        "created_at": lifecycle.aware(event.created_at).isoformat(),
    }


def list_audit_events(session: Session, *, limit: int = 50) -> list[dict[str, Any]]:
    events = (
        session.execute(
            select(MemoryAuditEvent).order_by(MemoryAuditEvent.id.desc()).limit(limit)
        )
        .scalars()
        .all()
    )
    return [_audit_payload(e) for e in events]


# --------------------------------------------------------------- entity graph


def create_entity(
    session: Session, *, kind: str, name: str, attrs: dict[str, Any] | None = None
) -> Entity:
    if kind not in ENTITY_KINDS:
        raise MemorySubsystemError(
            MemoryErrorClass.VALIDATION_ERROR, f"unknown entity kind {kind!r}"
        )
    existing = session.execute(
        select(Entity).where(Entity.kind == kind, Entity.name == name)
    ).scalar_one_or_none()
    if existing is not None:
        if attrs:
            existing.attrs_json = {**(existing.attrs_json or {}), **attrs}
            existing.updated_at = utcnow()
            session.commit()
        return existing
    entity = Entity(kind=kind, name=name, attrs_json=attrs or {})
    session.add(entity)
    session.commit()
    return entity


def get_entity(session: Session, entity_id: uuid.UUID) -> Entity:
    entity = session.get(Entity, entity_id)
    if entity is None:
        raise MemorySubsystemError(
            MemoryErrorClass.NOT_FOUND, f"unknown entity {entity_id}"
        )
    return entity


def list_entities(session: Session, *, kind: str | None = None, limit: int = 100) -> list[Entity]:
    stmt = select(Entity).order_by(Entity.created_at).limit(limit)
    if kind is not None:
        stmt = stmt.where(Entity.kind == kind)
    return list(session.execute(stmt).scalars().all())


def create_edge(
    session: Session,
    *,
    src_id: uuid.UUID,
    dst_id: uuid.UUID,
    relation: str,
    attrs: dict[str, Any] | None = None,
) -> EntityEdge:
    get_entity(session, src_id)
    get_entity(session, dst_id)
    existing = session.execute(
        select(EntityEdge).where(
            EntityEdge.src_id == src_id,
            EntityEdge.dst_id == dst_id,
            EntityEdge.relation == relation,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    edge = EntityEdge(src_id=src_id, dst_id=dst_id, relation=relation, attrs_json=attrs or {})
    session.add(edge)
    session.commit()
    return edge


def entity_edges(session: Session, entity_id: uuid.UUID) -> list[EntityEdge]:
    return list(
        session.execute(
            select(EntityEdge)
            .where((EntityEdge.src_id == entity_id) | (EntityEdge.dst_id == entity_id))
            .order_by(EntityEdge.created_at)
        )
        .scalars()
        .all()
    )


__all__ = [
    "DEDUP_SIMILARITY",
    "MemoryLinks",
    "ObserveResult",
    "create_edge",
    "create_entity",
    "edit_memory",
    "entity_edges",
    "forget_memory",
    "get_entity",
    "get_memory",
    "inspect_memory",
    "list_audit_events",
    "list_entities",
    "pin_memory",
    "record_observation",
    "remember_explicit",
    "supersede_memory",
]
