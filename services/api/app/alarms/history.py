"""B13 req 285: what actually happened to the owner's alarms, and when.

**No new table.** Every alarm transition has written an Activity Ledger row since M18 —
one per state entered, idempotent by ``alarms:{id}:{state}:{occurrence}``, plus a row when
the device's own fallback rang and a row when the alarm was cleaned up. The history was
already being kept; nothing could read it back as a history.

That distinction matters more here than anywhere else in the alarm subsystem, because the
alarm ROW is not a history and cannot become one. A recurring alarm reuses its row: when
``_release`` re-schedules it for tomorrow it rewinds ``terminal_state``, ``terminal_at`` and
``terminal_reason`` to ``None``. So "did my 07:30 ring on Tuesday?" is unanswerable from the
row by construction — the row only ever describes the NEXT occurrence. The ledger keeps each
occurrence separately because its idempotency key contains the occurrence.

What this module does NOT do is summarise. It returns the events, in order, with the state
each one recorded, and leaves "did it work?" to whoever is asking — an alarm that reached
PLAYING and one that reached FAILED are two facts, not one score.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    ALARM_EVENT_TYPE_BY_STATE,
    EVENT_TYPE_ALARM_CLEANED_UP,
    EVENT_TYPE_ALARM_LOCAL_FALLBACK_RANG,
    EVENT_TYPE_ALARM_LOCAL_SNOOZED,
)

#: Every ledger event type that is part of an alarm's story. Derived from the state map
#: rather than listed, so a state added to the machine cannot be missing from the history.
ALARM_EVENT_TYPES: tuple[str, ...] = (
    *ALARM_EVENT_TYPE_BY_STATE.values(),
    EVENT_TYPE_ALARM_LOCAL_FALLBACK_RANG,
    EVENT_TYPE_ALARM_LOCAL_SNOOZED,
    EVENT_TYPE_ALARM_CLEANED_UP,
)

#: The reverse map, so an entry can name the state it recorded without re-deriving it from
#: the event-type string.
_STATE_BY_EVENT_TYPE: dict[str, str] = {v: k for k, v in ALARM_EVENT_TYPE_BY_STATE.items()}

DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def module_id(alarm_id: uuid.UUID | str) -> str:
    """How ``app.alarms.service`` tags every row it writes."""
    return f"alarm:{alarm_id}"


def _entry(row: ActivityEventRow) -> dict[str, Any]:
    detail = dict(row.detail_json or {})
    return {
        "event_id": str(row.event_id),
        "event_type": row.event_type,
        # The state this row recorded, when it recorded one. `local_fallback_rang` and
        # `cleaned_up` are events without a state, and saying so is more useful than
        # inventing one.
        "state": _STATE_BY_EVENT_TYPE.get(row.event_type),
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "alarm_id": (row.related_module_id or "").removeprefix("alarm:") or None,
        "summary": row.factual_summary,
        "status": row.status,
        # The two the owner asks about by name: how they were woken, and why not by music.
        "media_kind": detail.get("media_kind"),
        "reason": detail.get("reason") or detail.get("terminal_reason"),
        "is_test": bool(detail.get("is_test")),
        # req 285 + the occurrence key: two rings of the same recurring alarm are two
        # entries, and this is what tells them apart.
        "source_ref": row.source_ref,
    }


def alarm_history(
    session: Session,
    *,
    alarm_id: uuid.UUID | str | None = None,
    since: datetime | None = None,
    include_tests: bool = True,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Newest first. One entry per recorded event, never one per alarm.

    ``alarm_id`` narrows to one alarm's own story — including the occurrences its row has
    already forgotten, which is the whole reason this reads the ledger and not the table.
    """
    stmt = select(ActivityEventRow).where(ActivityEventRow.event_type.in_(ALARM_EVENT_TYPES))
    if alarm_id is not None:
        stmt = stmt.where(ActivityEventRow.related_module_id == module_id(alarm_id))
    if since is not None:
        stmt = stmt.where(ActivityEventRow.occurred_at >= since)
    stmt = stmt.order_by(
        ActivityEventRow.occurred_at.desc(), ActivityEventRow.recorded_at.desc()
    ).limit(max(1, min(int(limit), MAX_LIMIT)))

    entries = [_entry(row) for row in session.execute(stmt).scalars().all()]
    if not include_tests:
        # Filtered here rather than in SQL: `is_test` lives in the detail JSON, and a
        # JSON predicate that works on PostgreSQL and not on SQLite is a query that passes
        # every unit test and returns the wrong rows in production.
        entries = [e for e in entries if not e["is_test"]]
    return entries


__all__ = [
    "ALARM_EVENT_TYPES",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "alarm_history",
    "module_id",
]
