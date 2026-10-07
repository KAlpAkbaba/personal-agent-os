"""A new voice session carries the previous one on (card conversation-carryover).

The owner closes the session at home and opens one at the office, on the phone shell, or
again in the morning. Attaching to the SAME session (spec §7) already moved a running
conversation between clients; a NEW session knew nothing of the one a minute earlier.
This module is the one rule that decides which earlier session a new one continues and
which live session a finished piece of work may be told to:

* :func:`pick_previous` - the most recent OTHER session with a non-empty summary whose
  last activity lies inside :data:`CARRYOVER_WINDOW`;
* :func:`pick_live` - the most recent OTHER session that is still live (created/active,
  not past its expiry) and was active inside the same window.

Both are pure over rows already read; :func:`find_previous` / :func:`find_live` are the
thin queries that feed them, so the window is defined exactly once. The owner switches the
whole behaviour off with ``VoicePreferences.conversation_carryover``; the callers read it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.voice.realtime_sessions.models import (
    REALTIME_STATE_ACTIVE,
    REALTIME_STATE_CREATED,
    RealtimeSessionRow,
)

#: How long after its last activity a session is still "the conversation we were having".
#: One constant, one reason: long enough to walk from the house PC to the car or the office
#: desk, short enough that this morning's session does not open with last night's topic.
CARRYOVER_WINDOW = timedelta(minutes=30)

#: The owner's own clock for the "(SS:DD)" in the continuation line.
OWNER_ZONE = ZoneInfo("Europe/Istanbul")

logger = get_logger("app.voice.realtime_sessions.carryover")

_LIVE_STATES = (REALTIME_STATE_CREATED, REALTIME_STATE_ACTIVE)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def last_activity(row: Any) -> datetime | None:
    """The later of ``updated_at`` and ``closed_at`` (SQLite hands naive values back)."""
    moments = [m for m in (_aware(row.updated_at), _aware(row.closed_at)) if m is not None]
    return max(moments) if moments else None


def _inside(row: Any, now: datetime, window: timedelta) -> bool:
    at = last_activity(row)
    return at is not None and now - at <= window


def pick_previous(
    rows: Iterable[Any],
    *,
    exclude_id: uuid.UUID,
    now: datetime,
    window: timedelta = CARRYOVER_WINDOW,
) -> Any | None:
    """The session a new one continues: newest summarised other session inside the window."""
    candidates = [
        row
        for row in rows
        if row.id != exclude_id
        and (row.transcript_summary or "").strip()
        and _inside(row, now, window)
    ]
    return max(candidates, key=last_activity, default=None)


def pick_live(
    rows: Iterable[Any],
    *,
    exclude_id: uuid.UUID,
    now: datetime,
    window: timedelta = CARRYOVER_WINDOW,
) -> Any | None:
    """The live session a closed session's finished work is told to (never the origin)."""

    def live(row: Any) -> bool:
        if row.state not in _LIVE_STATES:
            return False
        expires = _aware(row.expires_at)
        return expires is None or expires > now

    candidates = [
        row for row in rows if row.id != exclude_id and live(row) and _inside(row, now, window)
    ]
    return max(candidates, key=last_activity, default=None)


def _window_rows(db: Session, now: datetime, window: timedelta) -> list[RealtimeSessionRow]:
    since = now - window
    stmt = select(RealtimeSessionRow).where(
        or_(RealtimeSessionRow.updated_at >= since, RealtimeSessionRow.closed_at >= since)
    )
    return list(db.execute(stmt).scalars().all())


def find_previous(
    db: Session, *, exclude_id: uuid.UUID, now: datetime, window: timedelta = CARRYOVER_WINDOW
) -> RealtimeSessionRow | None:
    return pick_previous(
        _window_rows(db, now, window), exclude_id=exclude_id, now=now, window=window
    )


def find_live(
    db: Session, *, exclude_id: uuid.UUID, now: datetime, window: timedelta = CARRYOVER_WINDOW
) -> RealtimeSessionRow | None:
    return pick_live(_window_rows(db, now, window), exclude_id=exclude_id, now=now, window=window)


def enabled(db: Session) -> bool:
    """The owner's ``conversation_carryover`` preference, read WITHOUT writing anything.

    ``voice.service.load_preferences`` creates (and commits) a missing profile; the callers
    here are a tool handler and a background completion whose own writes must not be
    committed early or rolled back by a preference read. A profile that cannot be read
    is the default: on."""
    try:
        from app.voice.models import VoiceProfile
        from app.voice.service import OWNER_LABEL

        with db.begin_nested():
            profile = db.execute(
                select(VoiceProfile).where(VoiceProfile.label == OWNER_LABEL)
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("carryover_preference_unreadable", error=type(exc).__name__)
        return True
    if profile is None:
        return True
    return bool((profile.narration_settings_json or {}).get("conversation_carryover", True))


def device_label(db: Session, row: Any) -> str:
    """The owner's alias for the previous session's device, its name, or the client kind."""
    if row.device_id is not None:
        try:
            from app.broker.models import Device

            device = db.get(Device, row.device_id)
        except Exception:  # noqa: BLE001 - a label is a nicety; the carry must not fail on it
            device = None
        if device is not None:
            aliases = [str(a).strip() for a in (device.metadata_json or {}).get("aliases") or []]
            named = next((a for a in aliases if a), "") or str(device.name or "").strip()
            if named:
                return named
    return str(row.client_kind or "başka bir")


def record(db: Session, row: Any) -> dict[str, Any]:
    """The ``carried_from`` record a new session stores (and audits): ids and a label only,
    never the summary text."""
    ended = last_activity(row)
    return {
        "session_id": str(row.id),
        "device": device_label(db, row),
        "device_id": str(row.device_id) if row.device_id is not None else None,
        "ended_at": ended.isoformat() if ended is not None else None,
        "at": ended.astimezone(OWNER_ZONE).strftime("%H:%M") if ended is not None else "",
    }


def continuation_line(carried_from: dict[str, Any] | None) -> str:
    """The one Turkish line that introduces a carried summary in the instructions."""
    if not carried_from:
        return ""
    device = str(carried_from.get("device") or "başka bir")
    at = str(carried_from.get("at") or "")
    return (
        f"Bu konuşma az önce '{device}' cihazındaki oturumdan devam ediyor"
        + (f" ({at})" if at else "")
        + "; sahip isterse kaldığınız yerden sürdür."
    )


__all__ = [
    "CARRYOVER_WINDOW",
    "continuation_line",
    "device_label",
    "enabled",
    "find_live",
    "find_previous",
    "last_activity",
    "pick_live",
    "pick_previous",
    "record",
]
