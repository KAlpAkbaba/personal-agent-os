"""Minimal Turkish day/time-phrase extraction for the calendar voice family
(docs/M21_MAIL_CALENDAR_SPEC.md §3).

Deliberately small and calendar-specific, not a reuse of ``app.alarms.tr_time.
parse_when_text``: that module answers a different question ("the single NEXT instant an
alarm should ring") and cannot express a bare weekday with no clock time at all ("Cuma
... boşluk bul"), or a DURATION alongside one — reusing it would either misparse "60
dakikalık" as a relative offset (its own vocabulary for "in 60 minutes") or refuse a bare
weekday outright. This module only ever answers: which day, what clock time (if any),
what duration, what daypart window — never a full recurring-alarm grammar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

#: Longest/most specific keys first — the daypart lookup below returns the FIRST stem
#: match, so "öğleden" (afternoon) must be tried before its own prefix "öğle" (noon).
_DAYPART_WINDOWS: tuple[tuple[str, tuple[int, int]], ...] = (
    ("öğleden", (12, 18)),
    ("ogleden", (12, 18)),
    ("sabah", (8, 12)),
    ("öğle", (12, 14)),
    ("ogle", (12, 14)),
    ("akşam", (18, 22)),
    ("aksam", (18, 22)),
)

_WEEKDAY_NAMES: dict[str, int] = {
    "pazartesi": 0,
    "salı": 1,
    "sali": 1,
    "çarşamba": 2,
    "carsamba": 2,
    "perşembe": 3,
    "persembe": 3,
    "cuma": 4,
    "cumartesi": 5,
    "pazar": 6,
}

_HHMM_RE = re.compile(r"(?<!\d)(\d{1,2})[:.](\d{2})(?!\d)")
#: "15'e" / "15'te" / "15e" — a bare hour attached to a clock-shaped dative/locative
#: suffix (never a bare number alone, which is far too easy to confuse with a duration).
_SUFFIXED_HOUR_RE = re.compile(r"(?<!\d)(\d{1,2})['’]?(?:e|a|de|da|te|ta)\b")
_DURATION_MIN_RE = re.compile(r"(\d{1,3})\s*dakika")
_DURATION_HOUR_RE = re.compile(r"(\d{1,2})\s*saat")

#: The default event length and the default working-hours search window when the owner
#: named neither (spec's own Friday example bounds free slots to 09:00-18:00).
DEFAULT_EVENT_MINUTES = 60
DEFAULT_WINDOW_START_HOUR = 9
DEFAULT_WINDOW_END_HOUR = 18


@dataclass(frozen=True, slots=True)
class DayHint:
    is_today: bool = False
    is_tomorrow: bool = False
    weekday: int | None = None  # Monday=0 .. Sunday=6, when a weekday NAME was said

    @property
    def named(self) -> bool:
        return self.is_today or self.is_tomorrow or self.weekday is not None


def extract_day(tokens: tuple[str, ...]) -> DayHint:
    if any(t.startswith(("bugün", "bugun")) for t in tokens):
        return DayHint(is_today=True)
    if any(t.startswith(("yarın", "yarin")) for t in tokens):
        return DayHint(is_tomorrow=True)
    for t in tokens:
        if t in _WEEKDAY_NAMES:
            return DayHint(weekday=_WEEKDAY_NAMES[t])
    return DayHint()


def resolve_date(now: datetime, hint: DayHint) -> date:
    """The concrete date ``hint`` names, relative to ``now`` — a named weekday always
    means the NEXT one strictly after today (never today itself, even if today happens to
    be that weekday): "Cuma ..." said on a Friday means next Friday."""
    if hint.is_today:
        return now.date()
    if hint.is_tomorrow:
        return (now + timedelta(days=1)).date()
    if hint.weekday is not None:
        for offset in range(1, 8):
            candidate = now + timedelta(days=offset)
            if candidate.weekday() == hint.weekday:
                return candidate.date()
    return now.date()


def extract_clock(text: str) -> tuple[int, int] | None:
    lowered = text.lower()
    match = _HHMM_RE.search(lowered)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = _SUFFIXED_HOUR_RE.search(lowered)
    if match:
        hour = int(match.group(1))
        if 0 <= hour <= 23:
            return hour, 0
    return None


def extract_duration_minutes(text: str) -> int | None:
    lowered = text.lower()
    match = _DURATION_MIN_RE.search(lowered)
    if match:
        return int(match.group(1))
    if "yarım saat" in lowered or "yarim saat" in lowered:
        return 30
    if "bir saat" in lowered:
        return 60
    match = _DURATION_HOUR_RE.search(lowered)
    if match:
        return int(match.group(1)) * 60
    return None


def extract_daypart_window(tokens: tuple[str, ...]) -> tuple[int, int] | None:
    for tok in tokens:
        for stem, window in _DAYPART_WINDOWS:
            if tok.startswith(stem):
                return window
    return None


__all__ = [
    "DEFAULT_EVENT_MINUTES",
    "DEFAULT_WINDOW_END_HOUR",
    "DEFAULT_WINDOW_START_HOUR",
    "DayHint",
    "extract_clock",
    "extract_day",
    "extract_daypart_window",
    "extract_duration_minutes",
    "resolve_date",
]
