"""B11: recording a notification, and deciding how to reach the owner about it.

Recording and delivering are separated on purpose. ``record`` writes the row and returns -
it never waits for a transport, never fails because one is down, and never loses the
notification if every transport is down. The ladder then asks a different question, about a
row that already exists: which way of interrupting the owner has not been tried yet?

Quiet hours (req 379) are about NOISE, not about hiding. A deferred notification is in the
inbox from the moment it is recorded; what waits until morning is the attempt to interrupt.
Urgent passes through, which is the only reason the priority levels exist.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.notifications import delivery
from app.notifications.models import (
    CHANNEL_INBOX,
    LADDER,
    PRIORITIES,
    PRIORITY_NORMAL,
    PRIORITY_URGENT,
    NotificationRow,
)

logger = get_logger("app.notifications.service")

#: req 379. Local wall-clock hours; the owner's night, not UTC's. Crossing midnight is the
#: normal case, so the comparison below handles the wrap rather than assuming start < end.
QUIET_FROM: Final[time] = time(23, 0)
QUIET_UNTIL: Final[time] = time(7, 30)
#: The owner's wall clock (the alarms' and the briefing's zone). Until 2026-09-16 the window
#: was compared against the UTC time ``record`` passes, so in Istanbul the "night" ran
#: 02:00-10:30 and a 23:30 notice rang through.
QUIET_ZONE: Final[ZoneInfo] = ZoneInfo("Europe/Istanbul")


def _local(moment: datetime, zone: ZoneInfo) -> datetime:
    return (moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)).astimezone(zone)


def in_quiet_hours(
    moment: datetime,
    *,
    start: time = QUIET_FROM,
    end: time = QUIET_UNTIL,
    zone: ZoneInfo = QUIET_ZONE,
) -> bool:
    now = _local(moment, zone).time()
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def quiet_hours_end(
    moment: datetime, *, end: time = QUIET_UNTIL, zone: ZoneInfo = QUIET_ZONE
) -> datetime:
    """The next moment quiet hours are over, on the owner's wall clock, as UTC. Same day
    when the end is still ahead."""
    local = _local(moment, zone)
    today = datetime.combine(local.date(), end, tzinfo=zone)
    if today <= local:
        today = datetime.combine(local.date() + timedelta(days=1), end, tzinfo=zone)
    return today.astimezone(UTC)


def record(
    db: Session,
    *,
    kind: str,
    title: str = "",
    body: str = "",
    priority: str = PRIORITY_NORMAL,
    group_key: str = "",
    data: dict[str, Any] | None = None,
    now: datetime | None = None,
    quiet_start: time = QUIET_FROM,
    quiet_end: time = QUIET_UNTIL,
) -> NotificationRow:
    """Write the notification down. Commits, and never touches a transport.

    This is the whole reason a notification can no longer be lost: by the time any delivery
    is attempted the row exists, and the worst a failing transport can do is leave the owner
    to find it in the inbox.
    """
    moment = now or datetime.now(UTC)
    if priority not in PRIORITIES:
        priority = PRIORITY_NORMAL

    row = NotificationRow(
        kind=kind,
        title=title,
        body=body,
        priority=priority,
        group_key=group_key,
        data_json=dict(data or {}),
        created_at=moment,
    )
    # req 379: an urgent notification is the only kind that interrupts a night.
    if priority != PRIORITY_URGENT and in_quiet_hours(moment, start=quiet_start, end=quiet_end):
        row.deferred_until = quiet_hours_end(moment, end=quiet_end)

    db.add(row)
    db.flush()

    if group_key:
        _supersede_older_in_group(db, row, now=moment)
    db.commit()
    logger.info(
        "notification_recorded",
        notification_id=str(row.id),
        kind=kind,
        priority=priority,
        deferred=row.deferred_until is not None,
    )
    return row


def _supersede_older_in_group(db: Session, row: NotificationRow, *, now: datetime) -> None:
    """req 380: an unread older sibling becomes the older version of this one.

    Only UNREAD ones. A notification the owner has already read is part of what they know;
    quietly marking it superseded would edit their history to tidy a list.
    """
    older = (
        db.execute(
            select(NotificationRow).where(
                NotificationRow.group_key == row.group_key,
                NotificationRow.id != row.id,
                NotificationRow.read_at.is_(None),
                NotificationRow.superseded_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for previous in older:
        previous.superseded_at = now


def next_channel(row: NotificationRow, *, available: tuple[str, ...] = LADDER) -> str | None:
    """req 389: the next rung to try, or None when the ladder is finished.

    `available` is what this deployment can actually do right now - a device with no toast
    capability simply is not on the list, and the ladder falls to the next rung rather than
    waiting for something that will never answer.
    """
    if row.delivered_at is not None:
        return None
    attempted = set(row.attempted_json or [])
    for channel in LADDER:
        if channel in attempted:
            continue
        if channel != CHANNEL_INBOX and channel not in available:
            continue
        return channel
    return None


def deliverable(
    db: Session, *, now: datetime | None = None, limit: int = 50
) -> list[NotificationRow]:
    """Notifications the system may try to interrupt the owner about, right now."""
    moment = now or datetime.now(UTC)
    rows = (
        db.execute(
            select(NotificationRow)
            .where(
                NotificationRow.delivered_at.is_(None),
                NotificationRow.superseded_at.is_(None),
                NotificationRow.quarantined_at.is_(None),
            )
            .order_by(NotificationRow.created_at.asc())
            .limit(limit * 4)
        )
        .scalars()
        .all()
    )
    out: list[NotificationRow] = []
    for row in rows:
        if row.deferred_until is not None and _aware(row.deferred_until) > moment:
            continue
        if not delivery.may_attempt(
            attempts=row.attempts or 0,
            next_attempt_at=row.next_attempt_at,
            quarantined_at=row.quarantined_at,
            now=moment,
        ):
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def mark_delivered(
    db: Session, row: NotificationRow, *, channel: str, now: datetime | None = None
) -> NotificationRow:
    moment = now or datetime.now(UTC)
    row.delivered_at = moment
    row.delivered_via = channel
    row.attempted_json = sorted({*(row.attempted_json or []), channel})
    db.commit()
    return row


def mark_attempt_failed(
    db: Session,
    row: NotificationRow,
    *,
    channel: str,
    reason: str,
    now: datetime | None = None,
    available: tuple[str, ...] = LADDER,
) -> NotificationRow:
    """One rung did not work. Records it, and steps down the ladder.

    The row is NOT quarantined when a rung fails - only when every rung has been tried. A
    notification whose toast failed still has push and the inbox, and treating one channel's
    failure as the notification's failure is how the ladder would become decoration.
    """
    moment = now or datetime.now(UTC)
    row.attempted_json = sorted({*(row.attempted_json or []), channel})
    if next_channel(row, available=available) is None:
        row.ladder_exhausted = True
        attempts, next_at, quarantined = delivery.record_failure(
            attempts=row.attempts or 0, now=moment, key=row.id
        )
        row.attempts, row.next_attempt_at, row.quarantined_at = attempts, next_at, quarantined
        logger.warning(
            "notification_ladder_exhausted",
            notification_id=str(row.id),
            kind=row.kind,
            reason=reason,
            attempted=list(row.attempted_json),
        )
    db.commit()
    return row


def inbox(
    db: Session,
    *,
    unread_only: bool = False,
    include_superseded: bool = False,
    limit: int = 50,
) -> list[NotificationRow]:
    """req 368. The durable list, newest first - independent of any transport."""
    stmt = select(NotificationRow).order_by(NotificationRow.created_at.desc()).limit(limit)
    if unread_only:
        stmt = stmt.where(NotificationRow.read_at.is_(None))
    if not include_superseded:
        stmt = stmt.where(NotificationRow.superseded_at.is_(None))
    return list(db.execute(stmt).scalars().all())


def mark_read(
    db: Session, notification_id: uuid.UUID, *, now: datetime | None = None
) -> NotificationRow | None:
    row = db.get(NotificationRow, notification_id)
    if row is None:
        return None
    if row.read_at is None:
        row.read_at = now or datetime.now(UTC)
        db.commit()
    return row


#: B11-toast: how many presses one notification keeps. A toast offers at most three buttons
#: and is pressed once; more than this is a device repeating itself, not an owner.
MAX_PRESSES_PER_NOTIFICATION: Final[int] = 8

#: Why a press was not recorded (``record_action``'s answer).
PRESS_RECORDED: Final[str] = "recorded"
PRESS_DUPLICATE: Final[str] = "duplicate"
PRESS_UNKNOWN_NOTIFICATION: Final[str] = "unknown_notification"
PRESS_NOT_OFFERED: Final[str] = "action_not_offered"
PRESS_LIMIT: Final[str] = "limit_reached"


def record_action(
    db: Session,
    notification_id: uuid.UUID,
    action_id: str,
    pressed_at: datetime,
    *,
    device_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> str:
    """B11-toast (row 370): the owner pressed a toast button. Written onto the row.

    DATA, and only data. The press is accepted only for an action id THIS row offered (the
    device's word is not enough: it could be an older build, or not ours), recorded once per
    ``(action_id, pressed_at)`` however many heartbeats carry it, and appended to
    ``data_json["actions_pressed"]`` - which the inbox already returns. Nothing is run
    because of it here; a consumer that wants to act on ``snooze`` or ``open`` reads the row.

    It does not set ``read_at``: the contract keeps "the owner read it" for the inbox.
    """
    row = db.get(NotificationRow, notification_id)
    if row is None:
        return PRESS_UNKNOWN_NOTIFICATION
    data = dict(row.data_json or {})
    offered = {
        str(action.get("id"))
        for action in (data.get("actions") or [])
        if isinstance(action, dict)
    }
    if action_id not in offered:
        logger.warning(
            "notification_action_not_offered",
            notification_id=str(notification_id),
            action_id=action_id[:32],
        )
        return PRESS_NOT_OFFERED
    stamp = _iso(pressed_at)
    presses = [p for p in (data.get("actions_pressed") or []) if isinstance(p, dict)]
    if any(p.get("action_id") == action_id and p.get("pressed_at") == stamp for p in presses):
        return PRESS_DUPLICATE
    if len(presses) >= MAX_PRESSES_PER_NOTIFICATION:
        return PRESS_LIMIT
    presses.append(
        {
            "action_id": action_id,
            "pressed_at": stamp,
            "received_at": _iso(now or datetime.now(UTC)),
            "device_id": str(device_id) if device_id is not None else None,
        }
    )
    data["actions_pressed"] = presses
    # A new dict, so the JSON column is seen as changed.
    row.data_json = data
    db.commit()
    logger.info(
        "notification_action_recorded",
        notification_id=str(notification_id),
        action_id=action_id,
    )
    return PRESS_RECORDED


def history(db: Session, *, limit: int = 100) -> list[dict[str, Any]]:
    """req 377: what was sent, by which channel, and whether it was read.

    Every notification ever recorded, including the ones no channel carried - "we never
    reached you about this" is the part of a delivery history that matters.
    """
    rows = (
        db.execute(select(NotificationRow).order_by(NotificationRow.created_at.desc()).limit(limit))
        .scalars()
        .all()
    )
    return [
        {
            "id": str(row.id),
            "kind": row.kind,
            "priority": row.priority,
            "group_key": row.group_key or None,
            "created_at": _iso(row.created_at),
            "delivered_at": _iso(row.delivered_at),
            "delivered_via": row.delivered_via,
            "attempted": list(row.attempted_json or []),
            "deferred_until": _iso(row.deferred_until),
            "read_at": _iso(row.read_at),
            "superseded_at": _iso(row.superseded_at),
            "ladder_exhausted": bool(row.ladder_exhausted),
            "actions_pressed": [
                p.get("action_id")
                for p in ((row.data_json or {}).get("actions_pressed") or [])
                if isinstance(p, dict)
            ],
        }
        for row in rows
    ]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _aware(value).astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "QUIET_FROM",
    "QUIET_UNTIL",
    "deliverable",
    "history",
    "in_quiet_hours",
    "inbox",
    "mark_attempt_failed",
    "mark_delivered",
    "mark_read",
    "next_channel",
    "quiet_hours_end",
    "record",
    "record_action",
]
