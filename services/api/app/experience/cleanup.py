"""Which memories are the machine's heartbeat rather than the owner's memory (ADR-0190).

ADR-0190 stopped WRITING them. This is how the ones already in the store are found again,
and it selects by PROVENANCE, never by guessing at a sentence: every episodic row the
Experience Engine wrote carries a deterministic key, ``experience.episodic:<event id>``, so
the rows to forget are exactly those whose ledger event type is telemetry.

Two rows are never selected, whatever their text says: one the owner marked explicit, and
one they pinned. Forgetting is the single irreversible operation in this package
(``app.memory.service.forget_memory``), and the owner's own words are not this cleanup's
to touch.

Nothing is lost either way: ``activity_events`` keeps every one of these events, which is
where "when was the owner at the machine" is actually answered from.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.experience.engine import EPISODIC_KEY_PREFIX, TELEMETRY_EVENT_TYPES
from app.ledger.models import ActivityEventRow
from app.memory.models import Memory


def telemetry_event_ids(session: Session) -> set[str]:
    """Ledger event ids whose type this product no longer remembers."""
    rows = session.execute(
        select(ActivityEventRow.event_id).where(
            ActivityEventRow.event_type.in_(sorted(TELEMETRY_EVENT_TYPES))
        )
    )
    return {str(row[0]) for row in rows}


def event_id_of(memory: Memory) -> str:
    """The ledger event an episodic memory came from, or "" when it came from elsewhere."""
    key = str(memory.key or "")
    prefix = f"{EPISODIC_KEY_PREFIX}:"
    return key[len(prefix) :] if key.startswith(prefix) else ""


def select_telemetry_memories(session: Session) -> list[Memory]:
    """Every memory that is a heartbeat, oldest first. Never an explicit or pinned row."""
    ids = telemetry_event_ids(session)
    if not ids:
        return []
    memories = (
        session.execute(
            select(Memory)
            .where(Memory.key.like(f"{EPISODIC_KEY_PREFIX}:%"))
            .order_by(Memory.created_at)
        )
        .scalars()
        .all()
    )
    return [
        memory
        for memory in memories
        if event_id_of(memory) in ids and not memory.explicit and not memory.pinned
    ]


__all__ = ["event_id_of", "select_telemetry_memories", "telemetry_event_ids"]
