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


#: B46 (req 356): the recurrence words an owner says. Longer stems first, so "pazartesi"
#: is never read as "pazar" and "cumartesi" never as "cuma".
_RRULE_DAY_STEMS: tuple[tuple[str, str], ...] = (
    ("pazartesi", "MO"),
    ("cumartesi", "SA"),
    ("salı", "TU"),
    ("sali", "TU"),
    ("çarşamba", "WE"),
    ("carsamba", "WE"),
    ("perşembe", "TH"),
    ("persembe", "TH"),
    ("cuma", "FR"),
    ("pazar", "SU"),
)
_EVERY_N_RE = re.compile(r"(\d{1,2}|iki|üç|uc|dört|dort)\s*(gün|gun|hafta|ay)(?:da|de|ta|te)\s*bir")
_NUMBER_WORDS: dict[str, int] = {"iki": 2, "üç": 3, "uc": 3, "dört": 4, "dort": 4}
_COUNT_RE = re.compile(r"(\d{1,3})\s*(?:kez|kere|defa)")
_WEEKDAYS_PHRASE_RE = re.compile(r"hafta\s*i[çc](?:i|leri)")
_MONTH_FORMS = frozenset({"ay", "ayın", "ayin", "aylık", "aylik"})
_YEAR_FORMS = frozenset({"yıl", "yil", "sene", "yılın", "yilin", "yıllık", "yillik"})
_WEEK_FORMS = frozenset({"hafta", "haftalık", "haftalik"})
_DAY_FORMS = frozenset({"gün", "gun", "sabah", "akşam", "aksam", "gece"})
#: B46 (req 357): an owner who says "hatırlat" without an amount hears this in the read-back.
DEFAULT_REMINDER_MINUTES = 15
_REMINDER_WORD_RE = re.compile(r"hat[ıi]rlat|haber\s*ver|uyar")
_REMINDER_AMOUNT_RE = re.compile(r"(\d{1,4})\s*(dakika|dk|saat|gün|gun)\s*(?:önce|once)")
_REMINDER_PHRASES: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"yar[ıi]m\s*saat\s*(?:önce|once)"), 30),
    (re.compile(r"on\s*be[şs]\s*dakika\s*(?:önce|once)"), 15),
    (re.compile(r"bir\s*saat\s*(?:önce|once)"), 60),
    (re.compile(r"bir\s*g[üu]n\s*(?:önce|once)"), 1440),
    (re.compile(r"ba[şs]la(?:rken|d[ıi][ğg][ıi]nda)|zaman[ıi]nda"), 0),
)


def _tr_lower(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def extract_recurrence(text: str) -> str | None:
    """The RRULE the owner's words ask for - "her gün", "her hafta pazartesi", "hafta içi
    her gün", "iki haftada bir", "her ay", "her yıl", "pazartesileri", "10 kez" - or None
    when they asked for a single event. A weekday name alone is a date, never a rule."""
    from app.calendar.ics import RRuleError, validate_rrule

    lowered = _tr_lower(text or "")
    tokens = re.findall(r"[a-zçğıöşü]+|\d+", lowered)
    days: list[str] = []
    plural_day = False
    for tok in tokens:
        for stem, code in _RRULE_DAY_STEMS:
            if tok.startswith(stem):
                if code not in days:
                    days.append(code)
                if tok.endswith(("leri", "ları", "lari")):
                    plural_day = True
                break
    every = "her" in tokens
    token_set = set(tokens)
    interval = 1
    by_day: list[str] = []
    every_n = _EVERY_N_RE.search(lowered)
    if every_n:
        amount = every_n.group(1)
        interval = int(amount) if amount.isdigit() else _NUMBER_WORDS[amount]
        freq = {"gün": "DAILY", "gun": "DAILY", "hafta": "WEEKLY", "ay": "MONTHLY"}[
            every_n.group(2)
        ]
        by_day = days if freq == "WEEKLY" else []
    elif _WEEKDAYS_PHRASE_RE.search(lowered):
        freq, by_day = "WEEKLY", ["MO", "TU", "WE", "TH", "FR"]
    elif (every and token_set & {"ay", "ayın", "ayin"}) or token_set & {"aylık", "aylik"}:
        freq = "MONTHLY"
    elif (every and token_set & _YEAR_FORMS) or token_set & {"yıllık", "yillik"}:
        freq = "YEARLY"
    elif (
        (every and (token_set & _WEEK_FORMS or days))
        or plural_day
        or token_set
        & {
            "haftalık",
            "haftalik",
        }
    ):
        freq, by_day = "WEEKLY", days
    elif every and token_set & _DAY_FORMS:
        freq = "DAILY"
    else:
        return None
    parts = [f"FREQ={freq}"]
    if interval > 1:
        parts.append(f"INTERVAL={interval}")
    count = _COUNT_RE.search(lowered)
    if count:
        parts.append(f"COUNT={int(count.group(1))}")
    if by_day:
        parts.append("BYDAY=" + ",".join(by_day))
    try:
        return validate_rrule(";".join(parts))
    except RRuleError:
        return None


def extract_reminder_minutes(text: str) -> int | None:
    """Minutes before the start the owner asked to be reminded - "15 dakika önce hatırlat",
    "yarım saat önce", "bir gün önce", "başlarken" - or None when they asked for no reminder.
    A reminder word with no amount is :data:`DEFAULT_REMINDER_MINUTES`, said in the read-back."""
    lowered = _tr_lower(text or "")
    if not _REMINDER_WORD_RE.search(lowered):
        return None
    amount = _REMINDER_AMOUNT_RE.search(lowered)
    if amount:
        n, unit = int(amount.group(1)), amount.group(2)
        minutes = n if unit in ("dakika", "dk") else n * 60 if unit == "saat" else n * 1440
    else:
        minutes = next(
            (value for pattern, value in _REMINDER_PHRASES if pattern.search(lowered)),
            DEFAULT_REMINDER_MINUTES,
        )
    return minutes if 0 <= minutes <= 7 * 24 * 60 else None


def without_reminder(text: str) -> str:
    """The sentence with its reminder clause removed, so "15 dakika önce hatırlat" is never
    read as the event's duration nor "bir saat önce" as its length."""
    out = _REMINDER_AMOUNT_RE.sub(" ", _tr_lower(text or ""))
    for pattern, _ in _REMINDER_PHRASES:
        out = pattern.sub(" ", out)
    return out


__all__ = [
    "DEFAULT_EVENT_MINUTES",
    "DEFAULT_REMINDER_MINUTES",
    "DEFAULT_WINDOW_END_HOUR",
    "DEFAULT_WINDOW_START_HOUR",
    "DayHint",
    "extract_clock",
    "extract_day",
    "extract_daypart_window",
    "extract_duration_minutes",
    "extract_recurrence",
    "extract_reminder_minutes",
    "resolve_date",
    "without_reminder",
]
