"""An accepted Aktivra event: one notification, one ledger line, one ``aktivra_events`` row.

``aktivra.important`` is urgent - it passes quiet hours, and because the kind is in the one
importance list (``app.telephony.policy``) the alarm rung rings the phone and the call policy
may phone the owner, with the category word "Aktivra". ``aktivra.info`` is a normal
notification and waits for the morning like any other.

The event id is claimed BEFORE the notification is written, in the same transaction: a retry,
or two copies arriving at once, find the claim and get the first answer back - never a second
notification, never a second ring.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.aktivra.models import AktivraEventRow
from app.ledger import service as ledger
from app.ledger import vocabulary as v
from app.logging import get_logger
from app.notifications import service as notifications
from app.notifications.models import PRIORITY_NORMAL, PRIORITY_URGENT
from app.telephony.policy import KIND_AKTIVRA_IMPORTANT

logger = get_logger("app.aktivra")

KIND_IMPORTANT: Final[str] = KIND_AKTIVRA_IMPORTANT
KIND_INFO: Final[str] = "aktivra.info"
SEVERITY_IMPORTANT: Final[str] = "important"
SEVERITY_INFO: Final[str] = "info"
RETENTION: Final[timedelta] = timedelta(days=30)
_MODULE: Final[str] = "app.aktivra"


@dataclasses.dataclass(frozen=True, slots=True)
class Accepted:
    notification_id: uuid.UUID | None
    #: False = the event id was already here; the first answer is returned.
    created: bool


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def find(db: Session, event_id: str) -> AktivraEventRow | None:
    return db.execute(
        select(AktivraEventRow).where(AktivraEventRow.event_id == event_id)
    ).scalar_one_or_none()


def events_in_last_hour(db: Session, *, now: datetime) -> int:
    return db.execute(
        select(func.count())
        .select_from(AktivraEventRow)
        .where(AktivraEventRow.received_at > now - timedelta(hours=1))
    ).scalar_one()


def accept(
    db: Session,
    *,
    event_id: str,
    title: str,
    summary: str,
    severity: str,
    occurred_at: datetime | None = None,
    now: datetime | None = None,
) -> Accepted:
    """Record the event once. The caller has checked the token, the body and the hourly cap."""
    moment = now or datetime.now(UTC)
    existing = find(db, event_id)
    if existing is not None:
        return Accepted(existing.notification_id, created=False)

    claim = AktivraEventRow(event_id=event_id, severity=severity, received_at=moment)
    db.add(claim)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raced = find(db, event_id)
        return Accepted(raced.notification_id if raced else None, created=False)

    important = severity == SEVERITY_IMPORTANT
    row = notifications.record(
        db,
        kind=KIND_IMPORTANT if important else KIND_INFO,
        title=title,
        body=summary,
        priority=PRIORITY_URGENT if important else PRIORITY_NORMAL,
        group_key=f"aktivra:{event_id}",
        data={
            "source": "aktivra",
            "event_id": event_id,
            "occurred_at": _aware(occurred_at).isoformat() if occurred_at else None,
        },
        now=moment,
    )
    claim.notification_id = row.id
    db.commit()
    ledger.record(
        db,
        ledger.ActivityEvent(
            event_type=v.EVENT_TYPE_AKTIVRA_RECEIVED,
            subsystem=v.SUBSYSTEM_AKTIVRA,
            action=v.EVENT_TYPE_AKTIVRA_RECEIVED,
            factual_summary=f"Aktivra'dan {'önemli' if important else 'bilgi'} olay geldi.",
            source=v.EVENT_TYPE_AKTIVRA_RECEIVED,
            source_ref=f"notification:{row.id}",
            occurred_at=moment,
            module=_MODULE,
            detail_json={"event_id": event_id, "severity": severity},
        ),
    )
    logger.info(
        "aktivra_event_accepted",
        notification_id=str(row.id),
        event_id=event_id,
        severity=severity,
    )
    return Accepted(row.id, created=True)


def reject(
    db: Session, *, fingerprint: str | None, reason: str, now: datetime | None = None
) -> None:
    """One ``aktivra.rejected`` line: the reason and the fingerprint, never the token."""
    moment = now or datetime.now(UTC)
    ledger.record(
        db,
        ledger.ActivityEvent(
            event_type=v.EVENT_TYPE_AKTIVRA_REJECTED,
            subsystem=v.SUBSYSTEM_AKTIVRA,
            action=v.EVENT_TYPE_AKTIVRA_REJECTED,
            factual_summary="Aktivra kanalında geçersiz belirteçle bir istek reddedildi.",
            source=v.EVENT_TYPE_AKTIVRA_REJECTED,
            source_ref=f"rejected:{uuid.uuid4()}",
            status=v.STATUS_FAILED,
            occurred_at=moment,
            module=_MODULE,
            detail_json={"fingerprint": fingerprint, "reason": reason},
        ),
    )
    logger.warning("aktivra_event_rejected", fingerprint=fingerprint, reason=reason)


def status(db: Session, *, now: datetime | None = None) -> tuple[datetime | None, int]:
    """(when the last event arrived, how many in the last 24 h)."""
    moment = now or datetime.now(UTC)
    last = db.execute(select(func.max(AktivraEventRow.received_at))).scalar_one()
    count = db.execute(
        select(func.count())
        .select_from(AktivraEventRow)
        .where(AktivraEventRow.received_at > moment - timedelta(hours=24))
    ).scalar_one()
    return (_aware(last) if last is not None else None), count


def sweep_expired(db: Session, *, now: datetime | None = None) -> int:
    """Delete rows older than 30 days; their notifications stay (their own rules)."""
    moment = now or datetime.now(UTC)
    result = db.execute(
        delete(AktivraEventRow).where(AktivraEventRow.received_at < moment - RETENTION)
    )
    db.commit()
    return int(result.rowcount or 0)


__all__ = [
    "KIND_IMPORTANT",
    "KIND_INFO",
    "RETENTION",
    "SEVERITY_IMPORTANT",
    "SEVERITY_INFO",
    "Accepted",
    "accept",
    "events_in_last_hour",
    "find",
    "reject",
    "status",
    "sweep_expired",
]
