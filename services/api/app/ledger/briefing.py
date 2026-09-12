"""Proactive briefing policy (M16_ACTIVITY_LEDGER_SPEC.md §4).

Decides, for a ledger event that already exists as an ``ActivityEventRow``,
whether the owner needs to hear about it and how urgently — then queues the
Turkish sentence that will eventually be spoken (``pending_briefings``).
Nothing here starts, deploys, promotes or rolls back anything (spec §1.3:
"knowledge and execution are separate permissions").
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alarms.speech import capitalize_tr
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.ledger.vocabulary import (
    EVENT_TYPE_BRIEFING_DELIVERED,
    EVENT_TYPE_BRIEFING_QUEUED,
    EVENT_TYPE_RESEARCH_COMPLETED,
    EVENT_TYPE_RESEARCH_FAILED,
    PRODUCTION_STATE_SHADOW_READY,
    SEVERITY_CRITICAL,
    SUBSYSTEM_LEDGER,
)
from app.logging import get_logger
from app.narration.numbers import cardinal
from app.notifications import delivery

logger = get_logger("app.ledger.briefing")

POLICY_IMMEDIATE = "immediate"
POLICY_COMPLETION = "completion"
POLICY_ONCE = "once"
POLICY_DIGEST = "digest"
POLICY_LEDGER_ONLY = "ledger_only"

#: lower sorts first — the order pending() hands briefings back in.
_PRIORITY = {
    POLICY_IMMEDIATE: 0,
    POLICY_COMPLETION: 10,
    POLICY_ONCE: 20,
    POLICY_DIGEST: 30,
}

#: how long an undelivered briefing stays worth surfacing (reversible choice,
#: CLAUDE.md "Asking the owner" — not specified by the spec's table).
_EXPIRY = {
    POLICY_IMMEDIATE: timedelta(days=30),
    POLICY_COMPLETION: timedelta(hours=24),
    POLICY_ONCE: timedelta(days=30),
    POLICY_DIGEST: timedelta(hours=24),
}

#: event types spec §4 row 2 names explicitly ("owner-requested long task
#: finished").
_COMPLETION_EVENT_TYPES = frozenset({EVENT_TYPE_RESEARCH_COMPLETED, EVENT_TYPE_RESEARCH_FAILED})

#: "ordinary autonomous development" (spec §4 row 4): evolution and
#: deployment activity that is not itself critical, a research completion,
#: or a shadow_ready milestone.
_DIGEST_PREFIXES = ("evolution.", "deployment.")


def classify_policy(event: ActivityEventRow | ledger_service.ActivityEvent) -> str:
    """spec §4 table, evaluated top to bottom: severity beats event class."""
    if event.severity == SEVERITY_CRITICAL:
        return POLICY_IMMEDIATE
    if event.event_type in _COMPLETION_EVENT_TYPES:
        return POLICY_COMPLETION
    if event.production_state == PRODUCTION_STATE_SHADOW_READY:
        return POLICY_ONCE
    if event.event_type.startswith(_DIGEST_PREFIXES):
        return POLICY_DIGEST
    return POLICY_LEDGER_ONLY


def speech_for(event: ActivityEventRow | ledger_service.ActivityEvent) -> str:
    """Deterministic Turkish one-sentence briefing for completion/immediate/
    once events (spec §4). Numbers are spelled Turkish words
    (``app.narration.numbers.cardinal``), never digits — this is what gets
    spoken, unlike ``factual_summary`` which is written."""
    policy = classify_policy(event)
    detail = event.detail_json or {}
    if policy == POLICY_COMPLETION and event.event_type == EVENT_TYPE_RESEARCH_COMPLETED:
        findings_n = int(detail.get("findings", 0) or 0)
        # capitalize_tr, not str.capitalize(): "iki" must become "İki", and Python's
        # capitalize() gives the dotless "Iki" -- a different letter, a spelling mistake
        # to the owner and a mispronunciation to the TTS. Found on 2026-09-11 by the
        # deliverer's own test, the first thing that ever read this sentence back.
        words = capitalize_tr(cardinal(findings_n)) if findings_n else "Sıfır"
        return (
            f"Efendim, bilginize; araştırma tamamlandı. {words} önemli sonuç çıkardım. "
            "İsterseniz özetini anlatabilirim."
        )
    if policy == POLICY_COMPLETION and event.event_type == EVENT_TYPE_RESEARCH_FAILED:
        return "Efendim, bilginize; araştırma başarısız oldu. İsterseniz ayrıntısını anlatabilirim."
    if policy == POLICY_IMMEDIATE:
        return f"Efendim, önemli bir durum var. {event.factual_summary}"
    if policy == POLICY_ONCE:
        return f"Efendim, bilginize; {event.factual_summary}"
    # digest / ledger_only briefings are accumulated and summarized at
    # delivery time (spec §4), not spoken standalone — the factual sentence
    # is what queue_briefing stores for later aggregation.
    return event.factual_summary


def queue_briefing(
    session: Session, event: ActivityEventRow, now: datetime | None = None
) -> PendingBriefingRow | None:
    """Writes ``pending_briefings`` unless the event's policy is
    ``ledger_only`` (spec §4), in which case nothing is queued and ``None``
    is returned."""
    now = now or datetime.now(UTC)
    policy = classify_policy(event)
    if policy == POLICY_LEDGER_ONLY:
        return None

    row = PendingBriefingRow(
        created_at=now,
        policy=policy,
        priority=_PRIORITY[policy],
        speech=speech_for(event),
        event_ids=[str(event.event_id)],
        delivered_at=None,
        delivered_via=None,
        expires_at=now + _EXPIRY[policy],
    )
    session.add(row)
    session.commit()

    ledger_service.record(
        session,
        ledger_service.ActivityEvent(
            event_type=EVENT_TYPE_BRIEFING_QUEUED,
            subsystem=SUBSYSTEM_LEDGER,
            action="briefing_queued",
            factual_summary=f"Bildirim kuyruğa alındı: {policy}.",
            occurred_at=now,
            evidence_refs=[{"kind": "activity_event", "ref": str(event.event_id)}],
            detail_json={"policy": policy, "briefing_id": str(row.briefing_id)},
            source="live",
            source_ref=f"pending_briefings:{row.briefing_id}:queued",
        ),
    )
    return row


def pending(session: Session, now: datetime | None = None) -> list[PendingBriefingRow]:
    """Undelivered, unexpired, ATTEMPTABLE briefings, most urgent first (spec §4).

    B07 req 16: the last of those three words is the fix. A row the speaker cannot handle
    used to stay at the head of this list for ever - it is undelivered and unexpired, so it
    was picked on every pass, failed on every pass, and blocked every notification behind it
    while it did. Quarantined rows and rows still waiting out their backoff drop out here, so
    the queue moves past the one item that cannot be delivered instead of stopping at it.
    """
    now = now or datetime.now(UTC)
    stmt = (
        select(PendingBriefingRow)
        .where(PendingBriefingRow.delivered_at.is_(None))
        .where(PendingBriefingRow.expires_at > now)
        .where(PendingBriefingRow.quarantined_at.is_(None))
        .where(
            (PendingBriefingRow.next_attempt_at.is_(None))
            | (PendingBriefingRow.next_attempt_at <= now)
        )
        .order_by(PendingBriefingRow.priority.asc(), PendingBriefingRow.created_at.asc())
    )
    return list(session.execute(stmt).scalars().all())


def record_delivery_failure(
    session: Session, briefing_id: uuid.UUID, *, reason: str, now: datetime | None = None
) -> PendingBriefingRow | None:
    """One more failed attempt on this row; quarantine it once the bound is reached.

    Commits, because the count is the only thing standing between a bad row and an infinite
    loop: losing it to a rollback somewhere upstream would restore the original defect.
    """
    moment = now or datetime.now(UTC)
    row = session.get(PendingBriefingRow, briefing_id)
    if row is None:
        return None
    attempts, next_at, quarantined = delivery.record_failure(
        attempts=row.attempts or 0, now=moment, key=briefing_id
    )
    row.attempts = attempts
    row.next_attempt_at = next_at
    row.quarantined_at = quarantined
    session.commit()
    if quarantined is not None:
        logger.warning(
            "briefing_quarantined",
            briefing_id=str(briefing_id),
            **delivery.quarantine_summary(attempts, reason=reason),
        )
    return row


#: What ``delivered_via`` says when the owner HEARD it -- written by whichever half of
#: the voice path actually put the sentence in the air: a device push, or the browser
#: shell draining the frame out of its session buffer.
VIA_VOICE: Final[str] = "voice"


def mark_delivered(
    session: Session, briefing_id: uuid.UUID, via: str, now: datetime | None = None
) -> PendingBriefingRow | None:
    """Idempotent: a briefing already marked delivered is left as it is."""
    now = now or datetime.now(UTC)
    row = session.get(PendingBriefingRow, briefing_id)
    if row is None:
        return None
    if row.delivered_at is not None:
        return row
    row.delivered_at = now
    row.delivered_via = via
    session.commit()

    ledger_service.record(
        session,
        ledger_service.ActivityEvent(
            event_type=EVENT_TYPE_BRIEFING_DELIVERED,
            subsystem=SUBSYSTEM_LEDGER,
            action="briefing_delivered",
            factual_summary=f"Bildirim iletildi: {via}.",
            occurred_at=now,
            evidence_refs=[{"kind": "pending_briefing", "ref": str(row.briefing_id)}],
            detail_json={"via": via, "policy": row.policy},
            source="live",
            source_ref=f"pending_briefings:{row.briefing_id}:delivered",
        ),
    )
    return row


__all__: list[str] = [
    "POLICY_COMPLETION",
    "POLICY_DIGEST",
    "POLICY_IMMEDIATE",
    "POLICY_LEDGER_ONLY",
    "POLICY_ONCE",
    "VIA_VOICE",
    "classify_policy",
    "mark_delivered",
    "pending",
    "queue_briefing",
    "speech_for",
]
