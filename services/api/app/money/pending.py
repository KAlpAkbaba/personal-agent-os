"""Whether a bare "evet" / "hayır" / "geri al" is about money right now.

The router has no database. A bare "Evet." is the owner's answer to "750 liralık bir harcama
yaptınız mı?" only while that question is open - asked in the last ``OPEN_MINUTES`` and not
yet answered - and a bare "Geri al." takes back a booked spend only while that booking was
just announced. This process-local note is set where the question is asked and the booking is
announced (``app.money.loop``, in the API process the router runs in) and cleared where they
are answered. It only WIDENS what the router hears: the tools read the database for the
question or the booking itself, so a lost note (a restart) costs the bare word, never a
wrong booking; "evet harcadım" and "harcamayı geri al" do not need it.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Final

OPEN_MINUTES: Final = 10

_lock = threading.Lock()
_question_at: datetime | None = None
_booking_at: datetime | None = None


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def note_question(at: datetime) -> None:
    global _question_at
    with _lock:
        _question_at = _aware(at)


def note_booking(at: datetime) -> None:
    global _booking_at
    with _lock:
        _booking_at = _aware(at)


def clear_question() -> None:
    global _question_at
    with _lock:
        _question_at = None


def clear_booking() -> None:
    global _booking_at
    with _lock:
        _booking_at = None


def reset() -> None:
    clear_question()
    clear_booking()


def _open(at: datetime | None, now: datetime | None) -> bool:
    if at is None:
        return False
    moment = _aware(now or datetime.now(UTC))
    return timedelta(0) <= moment - at <= timedelta(minutes=OPEN_MINUTES)


def question_open(now: datetime | None = None) -> bool:
    return _open(_question_at, now)


def booking_open(now: datetime | None = None) -> bool:
    return _open(_booking_at, now)


__all__ = [
    "OPEN_MINUTES",
    "booking_open",
    "clear_booking",
    "clear_question",
    "note_booking",
    "note_question",
    "question_open",
    "reset",
]
