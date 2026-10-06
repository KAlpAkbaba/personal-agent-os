"""What an answered inbound call leaves behind: ONE ledger row and ONE notification.

The row (``telephony.inbound_answered``, subsystem telephony, ``source_ref = inbound:<CallSid>``)
carries the caller's number, the duration, the frame counters and the transcript TEXT - at most
8000 characters, with a ``truncated`` flag. Never audio. The notification is the owner's
"<number> aradı" with the first 300 characters of what the caller said (deterministic; a model
summary is a later step).

Once per CallSid. The status callback and the media socket's close both end in ``finalize`` and
may arrive together, and ``notifications.record`` adds a row on every call - so a per-CallSid
lock serialises them and the ledger row is the memory: it exists -> nothing is written. The
ledger is written first: a crash between the two loses a notification, never doubles one.
Unlike the outbound caller's ``_ledger``, nothing here swallows an error - a row that cannot be
written (``InvalidVocabulary`` included) is a failure the caller sees.

The day's allowance is counted from these rows (Istanbul calendar day), so a restart does not
reset it.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Any, Final, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger import service as ledger
from app.ledger import vocabulary as v
from app.ledger.models import ActivityEventRow
from app.logging import get_logger
from app.notifications import service as notifications
from app.notifications.models import PRIORITY_NORMAL
from app.notifications.service import QUIET_ZONE

logger = get_logger("app.telephony.inbound_records")

EVENT_TYPE: str = v.EVENT_TYPE_TELEPHONY_INBOUND_ANSWERED
NOTIFICATION_KIND: Final[str] = "telephony.inbound_answered"
SOURCE: Final[str] = "telephony"
MAX_TRANSCRIPT_CHARS: Final[int] = 8000
NOTIFICATION_BODY_CHARS: Final[int] = 300
SPEAKER_CALLER: Final[str] = "Arayan"
SPEAKER_JARVIS: Final[str] = "JARVIS"
UNKNOWN_CALLER_TITLE: Final[str] = "Bilinmeyen numara aradı"
NO_MESSAGE_TR: Final[str] = "Mesaj bırakmadı."

SessionScope = Callable[[], contextlib.AbstractContextManager[Session]]


class TranscriptEntry(Protocol):
    speaker: str
    text: str
    at: datetime


def source_ref(call_sid: str) -> str:
    return f"inbound:{call_sid}"


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def day_start(moment: datetime) -> datetime:
    """Midnight of ``moment``'s day on the owner's wall clock (Istanbul), as UTC."""
    local = _aware(moment).astimezone(QUIET_ZONE)
    return datetime.combine(local.date(), time(0, 0), tzinfo=QUIET_ZONE).astimezone(UTC)


def render_transcript(lines: Sequence[TranscriptEntry]) -> str:
    return "\n".join(
        f"[{_aware(line.at).astimezone(QUIET_ZONE):%H:%M:%S}] {line.speaker}: {line.text}"
        for line in lines
    )


def caller_words(lines: Sequence[TranscriptEntry]) -> str:
    return " ".join(line.text.strip() for line in lines if line.speaker == SPEAKER_CALLER).strip()


def notification_title(caller: str) -> str:
    number = (caller or "").strip()
    # Twilio's "anonymous" / "Restricted" and its well-known withheld-number placeholders
    if not number.startswith("+") or number in {"+266696687", "+7378742833", "+2562533"}:
        return UNKNOWN_CALLER_TITLE
    return f"{number} aradı"


class InboundRecorder:
    def __init__(self, session_scope: SessionScope) -> None:
        self._session_scope = session_scope
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock(self, call_sid: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(call_sid, threading.Lock())

    def finalize(
        self,
        call_sid: str,
        caller: str,
        started: datetime,
        ended: datetime,
        transcript: Sequence[TranscriptEntry],
        *,
        frames_in: int = 0,
        frames_out: int = 0,
        ended_by: str = "",
    ) -> bool:
        """Write the call down once. ``True`` when this call wrote it, ``False`` when it was
        already there."""
        ref = source_ref(call_sid)
        with self._lock(call_sid):
            with self._session_scope() as db:
                seen = db.execute(
                    select(ActivityEventRow.event_id).where(
                        ActivityEventRow.source == SOURCE, ActivityEventRow.source_ref == ref
                    )
                ).first()
                if seen is not None:
                    return False
                started = _aware(started)
                ended = _aware(ended)
                duration = max(0, int(round((ended - started).total_seconds())))
                text = render_transcript(transcript)
                truncated = len(text) > MAX_TRANSCRIPT_CHARS
                detail: dict[str, Any] = {
                    "direction": "inbound",
                    "call_sid": call_sid,
                    "caller": caller,
                    "duration_s": duration,
                    "frames_in": frames_in,
                    "frames_out": frames_out,
                    "ended_by": ended_by,
                    "transcript": text[:MAX_TRANSCRIPT_CHARS],
                    "truncated": truncated,
                }
                ledger.record(
                    db,
                    ledger.ActivityEvent(
                        event_type=EVENT_TYPE,
                        subsystem=v.SUBSYSTEM_TELEPHONY,
                        action=EVENT_TYPE,
                        factual_summary=f"Gelen arama cevaplandı: {caller or 'bilinmeyen'}, "
                        f"{duration} sn.",
                        source=SOURCE,
                        source_ref=ref,
                        status=v.STATUS_COMPLETED,
                        occurred_at=started,
                        module="app.telephony",
                        detail_json=detail,
                    ),
                )
                words = caller_words(transcript)
                notifications.record(
                    db,
                    kind=NOTIFICATION_KIND,
                    title=notification_title(caller),
                    body=words[:NOTIFICATION_BODY_CHARS] if words else NO_MESSAGE_TR,
                    priority=PRIORITY_NORMAL,
                    data={"call_sid": call_sid, "caller": caller, "duration_s": duration},
                    now=ended,
                )
        logger.info(
            "telephony_inbound_recorded",
            call_sid=call_sid,
            duration_s=duration,
            frames_in=frames_in,
            frames_out=frames_out,
            truncated=truncated,
        )
        return True

    def finalize_record(self, record: Any) -> bool:
        """``finalize`` for the bridge's ``CallRecord``."""
        return self.finalize(
            record.call_sid,
            record.caller,
            record.started,
            record.ended,
            record.transcript,
            frames_in=record.frames_in,
            frames_out=record.frames_out,
            ended_by=record.ended_by,
        )

    def seconds_used_today(self, now: datetime) -> int:
        since = day_start(now)
        with self._session_scope() as db:
            details = (
                db.execute(
                    select(ActivityEventRow.detail_json).where(
                        ActivityEventRow.event_type == EVENT_TYPE,
                        ActivityEventRow.occurred_at >= since,
                        ActivityEventRow.occurred_at < since + timedelta(days=1),
                    )
                )
                .scalars()
                .all()
            )
        return sum(int((detail or {}).get("duration_s") or 0) for detail in details)


__all__ = [
    "EVENT_TYPE",
    "MAX_TRANSCRIPT_CHARS",
    "NOTIFICATION_BODY_CHARS",
    "NOTIFICATION_KIND",
    "NO_MESSAGE_TR",
    "SPEAKER_CALLER",
    "SPEAKER_JARVIS",
    "InboundRecorder",
    "caller_words",
    "day_start",
    "notification_title",
    "render_transcript",
    "source_ref",
]
