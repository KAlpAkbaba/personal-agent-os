"""Turkish relative-date-window parsing for research recency (M13).

"Son üç gündeki yapay zekâ ajanlarıyla ilgili ... gelişmeleri araştır" -> the
owner means: only sources from the last three days. This module turns that
phrase into a concrete ``[start, end]`` UTC datetime window the plan/gather
stages act on. Pure and deterministic given ``now`` — Turkish first-class
means the spoken/typed relative-date phrasing becomes a literal date range,
never silently ignored or mistranslated.

Recognized forms (case-insensitive, diacritic-tolerant — "gün"/"gun" both
match, matching how the owner might type without a Turkish keyboard):

- ``"son <N> gün|saat|hafta|ay"`` — last N days/hours/weeks/months. ``<N>`` is
  either a digit string or a Turkish number word ``bir``..``on`` (1-10).
- ``"bugün"`` — today only (00:00 local-UTC to ``now``).
- ``"dün"`` — yesterday only (a full calendar day).

Anything not recognized returns ``None`` — callers apply a documented
default (:data:`app.research.plan.DEFAULT_RECENCY_DAYS`) rather than this
module guessing at owner intent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_NUMBER_WORDS: dict[str, int] = {
    "bir": 1,
    "iki": 2,
    "uc": 3,
    "üç": 3,
    "dort": 4,
    "dört": 4,
    "bes": 5,
    "beş": 5,
    "alti": 6,
    "altı": 6,
    "yedi": 7,
    "sekiz": 8,
    "dokuz": 9,
    "on": 10,
}

# unit token (as matched by the regex, common Turkish case-suffix inflections
# included — "gündeki" = "in the days", "haftada" = "in a week", etc.) ->
# canonical unit. Listed longest-suffix-first is not required (the trailing
# \b in _SON_RE makes the regex engine backtrack across alternatives until
# one's boundary is satisfied), but is kept in that order for readability.
_UNIT_CANONICAL: dict[str, str] = {
    "saatteki": "hour", "saatte": "hour", "saat": "hour",
    "gündeki": "day", "gundeki": "day", "günlük": "day", "gunluk": "day",
    "günde": "day", "gunde": "day", "gün": "day", "gun": "day",
    "haftadaki": "week", "haftada": "week", "hafta": "week",
    "aydaki": "month", "ayda": "month", "ay": "month",
}

_UNIT_ALT = "|".join(sorted(_UNIT_CANONICAL, key=len, reverse=True))
_SON_RE = re.compile(
    rf"\bson\s+(?P<amount>\d+|[a-zçğıöşü]+)\s+(?P<unit>{_UNIT_ALT})\b",
    re.IGNORECASE,
)
_BUGUN_RE = re.compile(r"\bbug[üu]n\b", re.IGNORECASE)
_DUN_RE = re.compile(r"\bd[üu]n\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RecencyWindow:
    """A concrete recency window, plus the Turkish label it was derived from."""

    start: datetime
    end: datetime
    label: str
    amount: int
    unit: str  # "hour" | "day" | "week" | "month"

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end

    def as_dict(self) -> dict[str, object]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "label": self.label,
            "amount": self.amount,
            "unit": self.unit,
        }


def _unit_delta(amount: int, unit: str) -> timedelta:
    if unit == "hour":
        return timedelta(hours=amount)
    if unit == "day":
        return timedelta(days=amount)
    if unit == "week":
        return timedelta(weeks=amount)
    if unit == "month":
        # No calendar-month arithmetic here on purpose: this is a recency
        # *filter*, not a calendar. 30-day approximation is documented and
        # deterministic, which is what the filter needs.
        return timedelta(days=amount * 30)
    raise ValueError(f"unknown recency unit: {unit!r}")  # pragma: no cover - unreachable


def _ensure_aware(now: datetime | None) -> datetime:
    now = now or datetime.now(UTC)
    return now if now.tzinfo is not None else now.replace(tzinfo=UTC)


def parse_recency_window(text: str, *, now: datetime | None = None) -> RecencyWindow | None:
    """Parse a Turkish relative-date phrase out of free text, or ``None``."""
    now = _ensure_aware(now)
    lowered = text.strip().lower()
    if not lowered:
        return None

    if _DUN_RE.search(lowered):
        day_start = (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = day_start + timedelta(days=1) - timedelta(microseconds=1)
        return RecencyWindow(start=day_start, end=day_end, label="dün", amount=1, unit="day")

    if _BUGUN_RE.search(lowered):
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return RecencyWindow(start=day_start, end=now, label="bugün", amount=1, unit="day")

    match = _SON_RE.search(lowered)
    if match is None:
        return None

    raw_amount = match.group("amount")
    unit_raw = match.group("unit")
    unit = _UNIT_CANONICAL.get(unit_raw)
    if unit is None:  # pragma: no cover - regex only ever matches known units
        return None

    amount = int(raw_amount) if raw_amount.isdigit() else _NUMBER_WORDS.get(raw_amount)
    if not amount or amount <= 0:
        return None

    start = now - _unit_delta(amount, unit)
    return RecencyWindow(
        start=start, end=now, label=f"son {raw_amount} {unit_raw}", amount=amount, unit=unit
    )


def default_window(now: datetime | None = None, *, days: int = 3) -> RecencyWindow:
    """The documented fallback when no recency phrase is present in the topic."""
    now = _ensure_aware(now)
    start = now - timedelta(days=days)
    return RecencyWindow(
        start=start, end=now, label=f"son {days} gün (varsayılan)", amount=days, unit="day"
    )


__all__ = ["RecencyWindow", "default_window", "parse_recency_window"]
