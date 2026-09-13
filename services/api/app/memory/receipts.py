"""B16 req 61/62: the two questions the owner can ask ABOUT a memory.

*Why do you remember this?* — `inspect_memory` has returned provenance, evidence,
versions, audit and conflicts since M5, and nothing ever turned it into a sentence. The
data was complete and unspeakable.

*Which memory did you use?* — that one had no substrate at all. Nothing anywhere recorded
that a memory had been READ BACK to the owner, so "hangi kaydı kullandın?" was a question
the system could not answer about itself.

**Where a use is stamped, and where it is not.** In the tool that speaks the memory to the
owner, never in `hybrid_search`. A search that returns forty rows so a caller can pick one
is not forty uses; it is one lookup. Stamping at retrieval would fill the ledger with rows
that mean nothing and make the honest question ("which one did you actually use") harder to
answer, not easier — the same rule `app.notifications` paid for as "a queue is not a
delivery".

**The ledger, not `memory_audit_events`.** That table is an append-only audit of
MUTATIONS, and a read is not one. The ledger is the system's evidence surface, it already
enumerates `memory.remembered` (declared since the ledger was written and, until this
batch, never emitted by anything), and `activity.explain` already answers "what did you
do" out of it — so this makes the new question answerable through a surface that exists
rather than a second one beside it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_MEMORY_REMEMBERED,
    EVENT_TYPE_MEMORY_USED,
    SEVERITY_INFO,
    SUBSYSTEM_MEMORY,
)
from app.logging import get_logger

logger = get_logger("app.memory.receipts")

#: How much of a memory's own text a receipt repeats. A ledger row is evidence, not a copy
#: of the memory: the id is what resolves back to the row, and a full transcript of every
#: preference in the activity log is a second place the owner would have to think about
#: deleting.
RECEIPT_TEXT_CHARS = 120


def _record(session: Session, event: ledger_service.ActivityEvent) -> bool:
    """Never fails the caller: a receipt is evidence, not a dependency.

    The rollback matters as much as the catch. A swallowed exception leaves the session
    poisoned and the NEXT statement fails somewhere the owner cannot connect to this -
    the failure shape `app.routines.service._record_ledger` was written after.
    """
    try:
        ledger_service.record(session, event)
        return True
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("memory_receipt_failed", error=f"{type(exc).__name__}: {exc}")
        try:
            session.rollback()
        except Exception:  # noqa: BLE001 - nothing further to do about it
            pass
        return False


def record_remembered(
    session: Session,
    *,
    memory_id: uuid.UUID,
    memory_class: str,
    text: str,
    source: str,
    source_ref: str,
    now: datetime | None = None,
    trace_id: str | None = None,
) -> bool:
    """The owner taught the system something. `memory.remembered`'s first writer."""
    return _record(
        session,
        ledger_service.ActivityEvent(
            event_type=EVENT_TYPE_MEMORY_REMEMBERED,
            subsystem=SUBSYSTEM_MEMORY,
            action="remember",
            severity=SEVERITY_INFO,
            factual_summary=f"Sahip bir kayıt öğretti: {text[:RECEIPT_TEXT_CHARS]}",
            source=source,
            source_ref=source_ref,
            occurred_at=now,
            trace_id=trace_id,
            related_module_id=f"memory:{memory_id}",
            detail_json={"memory_id": str(memory_id), "memory_class": memory_class},
        ),
    )


def record_use(
    session: Session,
    memories: list[tuple[uuid.UUID, str, str]],
    *,
    reason: str,
    source: str,
    source_ref: str,
    now: datetime | None = None,
    trace_id: str | None = None,
) -> int:
    """One receipt per memory actually read back to the owner. Returns how many landed.

    ``memories`` is ``(id, memory_class, text)`` per row. ``source_ref`` is made unique per
    memory below, because the ledger is idempotent on ``(source, source_ref)`` and a single
    key would silently collapse "I used three" into one row.
    """
    written = 0
    for memory_id, memory_class, text in memories:
        ok = _record(
            session,
            ledger_service.ActivityEvent(
                event_type=EVENT_TYPE_MEMORY_USED,
                subsystem=SUBSYSTEM_MEMORY,
                action=reason,
                severity=SEVERITY_INFO,
                factual_summary=(
                    f"Bir kayıt sahibe okundu ({reason}): {text[:RECEIPT_TEXT_CHARS]}"
                ),
                source=source,
                source_ref=f"{source_ref}:{memory_id}",
                occurred_at=now,
                trace_id=trace_id,
                related_module_id=f"memory:{memory_id}",
                detail_json={
                    "memory_id": str(memory_id),
                    "memory_class": memory_class,
                    "reason": reason,
                },
            ),
        )
        written += int(ok)
    return written


# --------------------------------------------------------------- why do you remember this


#: Provenance ORIGINS, in the owner's words - and the key is `origin`, which is what
#: `app.memory.service._new_memory` actually writes. The first draft of this table read a
#: `kind` that no writer in the product sets, so every explanation said "I did not record
#: where this came from" while the row said exactly where it came from. Caught by a test
#: about an INFERRED memory, which is the case where the answer matters most: an owner
#: asking why something is remembered that they never said.
#:
#: `test_the_provenance_vocabulary_is_the_memory_service_s_own` reads the writers and fails
#: when an origin exists that this table has no sentence for.
_ORIGIN_TR: dict[str, str] = {
    "owner_statement": "siz söylediniz",
    "observation": "konuşmalardan çıkardım",
    "procedure_detection": "tekrar eden bir alışkanlığınızdan çıkardım",
    "eval_corpus": "değerlendirme külliyatından geldi",
}


def why_sentence(payload: dict[str, Any]) -> str:
    """`inspect_memory`'s payload, said out loud.

    Four things and no more, because this is an answer somebody hears rather than reads:
    what it says, who put it there, how much it is leaning on, and whether anything has
    ever contradicted it. The rest of `inspect_memory` (every version, the whole audit
    trail) stays where a reader can page through it.
    """
    text = str(payload.get("text") or "").strip()
    explicit = bool(payload.get("explicit"))
    provenance = payload.get("provenance") or {}
    origin = str(provenance.get("origin") or "").lower()
    evidence = payload.get("evidence") or []
    conflicts = payload.get("conflicts") or []
    confidence = payload.get("confidence")

    if explicit:
        said = "siz söylediniz"
    else:
        said = _ORIGIN_TR.get(origin, "nereden geldiğini kaydetmemişim")

    parts = [f"«{text}» kaydını {said}"]

    count = len(evidence)
    if count > 1:
        parts.append(f"{count} ayrı gözlem bunu destekliyor")
    elif count == 1:
        parts.append("tek bir gözleme dayanıyor")

    if not explicit and isinstance(confidence, (int, float)):
        parts.append(f"güvenim yüzde {int(round(float(confidence) * 100))}")

    if conflicts:
        parts.append(f"ve {len(conflicts)} kez bununla çelişen bir şey duydum")

    if payload.get("pinned"):
        parts.append("ve siz bunu sabitlediniz")

    return ", ".join(parts) + " efendim."


__all__ = [
    "RECEIPT_TEXT_CHARS",
    "record_remembered",
    "record_use",
    "why_sentence",
]
