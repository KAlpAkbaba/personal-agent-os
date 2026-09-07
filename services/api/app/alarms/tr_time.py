"""Deterministic Turkish "when" parsing for wake alarms (M18.3 spec §3.8).

Pure and offline: no model, no clock of its own, no network. The caller supplies ``now``
and the owner's IANA timezone; this module returns the next matching instant IN THAT ZONE,
converted to UTC only at the boundary. Never UTC for an owner-facing schedule (spec §3.1):
"yarın sabah 07:30" means 07:30 wall-clock in Europe/Istanbul, and the DST-safety rule
``app.routines.triggers`` already follows applies here for exactly the same reason — a
fixed UTC offset computed once would fire an hour early or late across a transition without
ever raising, so every arithmetic step happens on LOCAL wall-clock fields and the tz is
re-applied afterwards.

Recognised (spec §6's phrase list, plus the shapes the same phrases take without the verb):

    yarın sabah 07:30 / yarın 07:30'da / yarın sabah yedi buçukta
    saat 08:00'e / 08:00'de / sabah 7'de
    her hafta içi 07:15'te / her gün 07:00'de / hafta sonu 09:00'da
    pazartesi 07:00'de (any weekday name, or several)
    90 saniye sonra / beş dakika sonra / on dakika / iki saat sonra

Everything else is an :class:`UnparsedWhen` — never a guess. An alarm the owner cannot
place in time is worse than a question, so the tool asks rather than inventing a moment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE: Final = "Europe/Istanbul"


def turkish_casefold(text: str) -> str:
    """Casefold that keeps the Turkish dotted/dotless i distinct.

    Deliberately a local four-line copy of ``app.voice.intents.turkish_casefold`` rather
    than an import: this package must stay free of the voice subsystem so that an alarm can
    never fail to ring because something in the voice import graph did (the package's own
    structural test asserts exactly that). Four lines of casefolding is a smaller risk than
    that edge, and the two cannot drift in a way that matters — Turkish orthography is not
    going to change.
    """
    return text.replace("İ", "i").replace("I", "ı").lower()

#: Weekday names as an owner says them -> Python's Monday=0 index.
_WEEKDAY_NAMES: Final[dict[str, int]] = {
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

WEEKDAYS_ALL: Final[tuple[int, ...]] = (0, 1, 2, 3, 4, 5, 6)
WEEKDAYS_WEEKDAY: Final[tuple[int, ...]] = (0, 1, 2, 3, 4)
WEEKDAYS_WEEKEND: Final[tuple[int, ...]] = (5, 6)

#: Spoken numbers 0..59, enough for a clock and for a relative offset. Written out rather
#: than derived from ``app.narration.numbers`` because that module GENERATES readings and
#: this one RECOGNISES them: the inverse of a generator is not the generator.
_NUMBER_WORDS: Final[dict[str, int]] = {
    "sıfır": 0,
    "sifir": 0,
    "bir": 1,
    "iki": 2,
    "üç": 3,
    "uc": 3,
    "dört": 4,
    "dort": 4,
    "beş": 5,
    "bes": 5,
    "altı": 6,
    "alti": 6,
    "yedi": 7,
    "sekiz": 8,
    "dokuz": 9,
    "on": 10,
    "yirmi": 20,
    "otuz": 30,
    "kırk": 40,
    "kirk": 40,
    "elli": 50,
    "altmış": 60,
    "altmis": 60,
    "doksan": 90,
    "yüz": 100,
    "yuz": 100,
}

#: "sabah" (morning) and "akşam" (evening) disambiguate a bare hour; they never override an
#: explicit 24-hour time. "gece" is treated as evening for the same reason.
_MORNING_WORDS: Final[tuple[str, ...]] = ("sabah", "sabahleyin", "sabaha")
_EVENING_WORDS: Final[tuple[str, ...]] = ("akşam", "aksam", "gece", "akşama", "aksama")

_HHMM_RE: Final = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)")
#: "7 30 da" / "7 30'da": the ASR splits a spoken "yedi otuz" into two numerals. Two-digit
#: minutes only, and accepted only with a case suffix or a clock context word (corpus
#: a.create.5 / a.gen.30: "yarın 7 30 da beni uyandır" parsed to nothing).
_HM_SPACED_RE: Final = re.compile(
    r"(?<![\d:])(?P<h>[01]?\d|2[0-3])\s+(?P<m>[0-5]\d)(?![\d:])"
    r"(?P<sfx>\s*'?\s*(?:d[ae]|t[ae]|y[ıie]|[ıiea])\b)?"
)
_BARE_HOUR_RE: Final = re.compile(r"(?<!\d)([01]?\d|2[0-3])(?!\d)\s*(?:'?[a-zçğıöşü]{1,4})?\b")
_SECONDS_RE: Final = re.compile(r"(?<!\d)(\d{1,5})\s*(?:sn|saniye)")
_MINUTES_RE: Final = re.compile(r"(?<!\d)(\d{1,4})\s*(?:dk|dakika)")
_HOURS_RE: Final = re.compile(r"(?<!\d)(\d{1,3})\s*saat")

MAX_RELATIVE_SECONDS: Final = 24 * 3600


class UnparsedWhen(ValueError):
    """The owner's "when" could not be placed in time deterministically.

    Raised, never swallowed into a default: a wake alarm at a guessed moment is the one
    failure mode that cannot be corrected after the fact.
    """


@dataclass(frozen=True, slots=True)
class ParsedWhen:
    """A resolved "when".

    ``at`` is the next matching instant as a tz-aware UTC datetime; ``local_time`` is the
    "HH:MM" the owner will hear back; ``weekdays`` is non-empty only for a recurring
    alarm. ``relative_seconds`` is set for a "N saniye sonra" style offset, in which case
    ``weekdays`` is empty and ``local_time`` is the clock time the offset lands on.
    """

    at: datetime
    local_time: str
    timezone: str
    weekdays: tuple[int, ...] = ()
    relative_seconds: int | None = None
    matched: str = ""

    @property
    def is_recurring(self) -> bool:
        return bool(self.weekdays)

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "local_time": self.local_time,
            "timezone": self.timezone,
            "weekdays": list(self.weekdays),
            "relative_seconds": self.relative_seconds,
            "matched": self.matched,
        }


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except Exception as exc:  # noqa: BLE001 - an unknown zone is a caller error, said plainly
        raise UnparsedWhen(f"unknown IANA timezone: {timezone!r}") from exc


def _tokens(text: str) -> list[str]:
    folded = turkish_casefold(text)
    # Keep digits, letters, ':' and '.' inside times; everything else becomes a separator.
    cleaned = re.sub(r"[^0-9a-zçğıöşü:.]+", " ", folded)
    return [t for t in cleaned.split() if t]


def _word_number(tokens: list[str], index: int) -> tuple[int | None, int]:
    """A Turkish number written in words starting at ``index``: returns (value, tokens
    consumed). Handles the two-token compounds a clock needs ("on beş", "kırk iki")."""
    if index >= len(tokens):
        return None, 0
    first = _NUMBER_WORDS.get(tokens[index].strip(".:"))
    if first is None:
        return None, 0
    if first >= 10 and index + 1 < len(tokens):
        second = _NUMBER_WORDS.get(tokens[index + 1].strip(".:"))
        if second is not None and second < 10:
            return first + second, 2
    return first, 1


#: Case suffixes a spoken hour carries: "yediye", "sekizde", "beşte", "yediyi".
_CLOCK_CASE_SUFFIXES: Final[tuple[str, ...]] = (
    "de",
    "da",
    "te",
    "ta",
    "yi",
    "yı",
    "ye",
    "ya",
    "i",
    "ı",
    "e",
    "a",
)

_HALF_WORDS: Final[tuple[str, ...]] = ("buçuk", "bucuk", "buçukta", "bucukta")
_QUARTER_WORDS: Final[tuple[str, ...]] = ("çeyrek", "ceyrek")


def _split_number_word(raw: str) -> tuple[str | None, bool]:
    """A number word, bare or with a case suffix: ("otuz", False), ("otuz", True) for
    "otuzda", (None, False) for anything else."""
    token = raw.strip(".:")
    if token in _NUMBER_WORDS:
        return token, False
    for suffix in _CLOCK_CASE_SUFFIXES:
        if token.endswith(suffix) and token[: -len(suffix)] in _NUMBER_WORDS:
            return token[: -len(suffix)], True
    return None, False


def _spoken_minute(rest: list[str]) -> tuple[int | None, int, bool]:
    """The minutes after a spoken hour: (value, tokens consumed, carried a case suffix).
    "otuzda" is thirty with a suffix; "kırk beşte" is forty-five with the suffix on the
    last word. The suffix is what corroborates "yedi otuzda" as a clock (corpus a.gen.36:
    "yarın yedi otuzda beni uyandır" parsed to nothing without it)."""
    if not rest:
        return None, 0, False
    first, first_suffixed = _split_number_word(rest[0])
    if first is None:
        return None, 0, False
    value = _NUMBER_WORDS[first]
    if value >= 10 and not first_suffixed and len(rest) > 1:
        second, second_suffixed = _split_number_word(rest[1])
        if second is not None and _NUMBER_WORDS[second] < 10:
            return value + _NUMBER_WORDS[second], 2, second_suffixed
    return value, 1, first_suffixed


def _spoken_clock(tokens: list[str]) -> tuple[int, int] | None:
    """"yedi buçuk", "yediyi çeyrek geçiyor", "sekize çeyrek var", "yedi kırk iki".

    A bare number is NOT a clock. "Beni bir ara uyandır" contains "bir", and reading that
    as 01:00 would set an alarm for one in the morning off a sentence that named no time at
    all — the exact "never guess" failure this module refuses. So a spoken hour is only
    accepted with corroboration: a case suffix ("sekizde"), a following "buçuk"/"çeyrek",
    the word "saat", or an immediately preceding daypart word ("sabah yedi").
    """
    for i, raw in enumerate(tokens):
        token = raw.strip(".:")
        # "yediyi" / "sekize" / "sekizde" carry a suffix; try the bare stem too.
        base = token
        suffixed = False
        for suffix in _CLOCK_CASE_SUFFIXES:
            if base.endswith(suffix) and base[: -len(suffix)] in _NUMBER_WORDS:
                base = base[: -len(suffix)]
                suffixed = True
                break
        hour = _NUMBER_WORDS.get(base)
        if hour is None or not (0 <= hour <= 23):
            continue
        rest = tokens[i + 1 :]
        # "on beşte" / "yirmi birde" / "on beş otuzda": a compound HOUR, with the case
        # suffix on its last word. Read before the minutes, or "on beşte" is 10:05.
        if hour in (10, 20) and not suffixed and rest:
            unit, unit_suffixed = _split_number_word(rest[0])
            unit_value = _NUMBER_WORDS[unit] if unit is not None else 0
            if 0 < unit_value < 10 and hour + unit_value <= 23:
                hour += unit_value
                suffixed = unit_suffixed
                rest = rest[1:]
        fraction = bool(rest and (rest[0] in _HALF_WORDS or rest[0] in _QUARTER_WORDS))
        minute, consumed, minute_suffixed = (None, 0, False) if fraction else _spoken_minute(rest)
        corroborated = (
            suffixed
            or minute_suffixed
            or fraction
            or any(t.startswith("saat") for t in tokens)
            or (i > 0 and (tokens[i - 1] in _MORNING_WORDS or tokens[i - 1] in _EVENING_WORDS))
        )
        if not corroborated:
            continue
        if rest and rest[0] in _HALF_WORDS:
            return hour, 30
        if rest and rest[0] in _QUARTER_WORDS:
            if any(w.startswith("geç") or w.startswith("gec") for w in rest[1:2]):
                return hour, 15
            if any(w.startswith("var") for w in rest[1:2]):
                return (hour - 1) % 24, 45
            return hour, 15
        if minute is not None and consumed and 0 <= minute <= 59:
            return hour, minute
        return hour, 0
    return None


def _clock_from(text: str, tokens: list[str]) -> tuple[int, int] | None:
    match = _HHMM_RE.search(text)
    if match:
        return int(match.group(1)), int(match.group(2))
    clock_context = any(t.startswith("saat") for t in tokens) or any(
        t in _MORNING_WORDS or t in _EVENING_WORDS for t in tokens
    )
    spaced = _HM_SPACED_RE.search(text)
    if spaced and (spaced.group("sfx") or clock_context):
        return int(spaced.group("h")), int(spaced.group("m"))
    spoken = _spoken_clock(tokens)
    if spoken is not None:
        return spoken
    # A bare digit hour ("saat 8'e", "sabah 7de"). Only when a clock context word is
    # present, so "90 saniye" and "5 dakika" can never be read as an hour.
    if any(t.startswith("saat") for t in tokens) or any(
        t in _MORNING_WORDS or t in _EVENING_WORDS for t in tokens
    ):
        bare = _BARE_HOUR_RE.search(text)
        if bare:
            return int(bare.group(1)), 0
    return None


def _apply_daypart(hour: int, minute: int, tokens: list[str]) -> tuple[int, int]:
    """"akşam yedi" is 19:00; "sabah yedi" is 07:00. An hour already >= 13 is explicit and
    is never shifted, and neither is an hour written as an explicit "HH:MM" >= 13."""
    if hour >= 13:
        return hour, minute
    if any(t in _EVENING_WORDS for t in tokens) and hour < 12:
        return hour + 12, minute
    return hour, minute


def _relative_seconds(text: str, tokens: list[str]) -> int | None:
    """"90 saniye sonra", "beş dakika", "iki saat sonra". ``sonra`` is optional: the owner
    says "beş dakika ertele" and means five minutes from now."""
    total = 0
    found = False
    for regex, factor in ((_SECONDS_RE, 1), (_MINUTES_RE, 60), (_HOURS_RE, 3600)):
        match = regex.search(text)
        if match:
            total += int(match.group(1)) * factor
            found = True
    if not found:
        # word-number forms: "beş dakika", "on dakika", "iki saat"
        for i, token in enumerate(tokens):
            unit = None
            if token.startswith("saniye") or token == "sn":
                unit = 1
            elif token.startswith("dakika") or token == "dk":
                unit = 60
            elif token.startswith("saat"):
                unit = 3600
            if unit is None or i == 0:
                continue
            # "saat 08:00" is a clock, not an offset: a preceding number is required.
            value, consumed = _word_number(tokens[max(0, i - 2) : i], 0)
            if value is None and i >= 1:
                value, consumed = _word_number(tokens[i - 1 : i], 0)
            if value is not None and consumed:
                total += value * unit
                found = True
                break
    if not found or total <= 0:
        return None
    if total > MAX_RELATIVE_SECONDS:
        raise UnparsedWhen(
            f"relative offset {total}s exceeds the {MAX_RELATIVE_SECONDS}s bound"
        )
    return total


def _weekdays_from(tokens: list[str]) -> tuple[int, ...]:
    joined = " ".join(tokens)
    if "hafta içi" in joined or "hafta ici" in joined:
        return WEEKDAYS_WEEKDAY
    if "hafta sonu" in joined:
        return WEEKDAYS_WEEKEND
    named = tuple(
        sorted({_WEEKDAY_NAMES[t] for t in tokens if t in _WEEKDAY_NAMES})
    )
    if named:
        return named
    if "her" in tokens and any(t.startswith("gün") or t.startswith("gun") for t in tokens):
        return WEEKDAYS_ALL
    return ()


def _next_local(
    now_local: datetime, hour: int, minute: int, *, tomorrow: bool, weekdays: tuple[int, ...]
) -> datetime:
    """Wall-clock arithmetic in the owner's zone, then re-localised (module docstring).

    ``fold=0`` is deliberate for the ambiguous hour of a DST fall-back: the FIRST occurrence
    of a repeated wall-clock time is the earlier instant, and waking the owner early is the
    recoverable half of that trade.
    """
    target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0, fold=0)
    if tomorrow:
        target = target + timedelta(days=1)
        target = target.replace(hour=hour, minute=minute, second=0, microsecond=0, fold=0)
    if weekdays:
        for offset in range(0, 8):
            candidate = (now_local + timedelta(days=offset)).replace(
                hour=hour, minute=minute, second=0, microsecond=0, fold=0
            )
            if candidate.weekday() in weekdays and candidate > now_local:
                return candidate
        raise UnparsedWhen("no matching weekday within a week — refusing to guess")
    if target <= now_local:
        target = (target + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0, fold=0
        )
    return target


def parse_when_text(
    text: str, *, now: datetime, timezone: str = DEFAULT_TIMEZONE
) -> ParsedWhen:
    """Parse an owner's spoken "when" into the next matching instant.

    Raises :class:`UnparsedWhen` for anything it cannot place deterministically.
    """
    if not isinstance(text, str) or not text.strip():
        raise UnparsedWhen("empty 'when' text")
    zone = _zone(timezone)
    now_local = now.astimezone(zone)
    folded = turkish_casefold(text)
    tokens = _tokens(text)

    offset = _relative_seconds(folded, tokens)
    if offset is not None:
        at_local = now_local + timedelta(seconds=offset)
        return ParsedWhen(
            at=at_local.astimezone(UTC),
            local_time=at_local.strftime("%H:%M"),
            timezone=timezone,
            relative_seconds=offset,
            matched="relative",
        )

    clock = _clock_from(folded, tokens)
    if clock is None:
        raise UnparsedWhen(f"no clock time or offset found in {text!r}")
    hour, minute = _apply_daypart(clock[0], clock[1], tokens)
    weekdays = _weekdays_from(tokens)
    tomorrow = any(t.startswith("yarın") or t.startswith("yarin") for t in tokens)
    at_local = _next_local(now_local, hour, minute, tomorrow=tomorrow, weekdays=weekdays)
    return ParsedWhen(
        at=at_local.astimezone(UTC),
        local_time=f"{hour:02d}:{minute:02d}",
        timezone=timezone,
        weekdays=weekdays,
        matched="recurring" if weekdays else ("tomorrow" if tomorrow else "next"),
    )


def parse_when_struct(
    when: dict[str, Any], *, now: datetime, timezone: str = DEFAULT_TIMEZONE
) -> ParsedWhen:
    """The structured form the tool schema accepts (spec §3.8): ``relative_seconds``, or
    ``date`` ("tomorrow" | ISO date) + ``time`` ("HH:MM"), optionally with ``weekdays``."""
    zone = _zone(timezone)
    now_local = now.astimezone(zone)

    relative = when.get("relative_seconds")
    if relative is not None:
        if isinstance(relative, bool) or not isinstance(relative, int) or relative <= 0:
            raise UnparsedWhen("relative_seconds must be a positive int")
        if relative > MAX_RELATIVE_SECONDS:
            raise UnparsedWhen(
                f"relative_seconds exceeds the {MAX_RELATIVE_SECONDS}s bound"
            )
        at_local = now_local + timedelta(seconds=relative)
        return ParsedWhen(
            at=at_local.astimezone(UTC),
            local_time=at_local.strftime("%H:%M"),
            timezone=timezone,
            relative_seconds=relative,
            matched="relative_seconds",
        )

    raw_time = when.get("time")
    if not isinstance(raw_time, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", raw_time):
        raise UnparsedWhen("'time' must be 'HH:MM' 24h")
    hour, minute = (int(part) for part in raw_time.split(":"))

    weekdays_raw = when.get("weekdays") or []
    if not isinstance(weekdays_raw, list):
        raise UnparsedWhen("'weekdays' must be a list of 0..6")
    weekdays: tuple[int, ...] = tuple(sorted({int(d) for d in weekdays_raw}))
    for day in weekdays:
        if not (0 <= day <= 6):
            raise UnparsedWhen("'weekdays' entries must be 0 (Mon) .. 6 (Sun)")

    date_raw = when.get("date")
    if isinstance(date_raw, str) and date_raw and date_raw != "tomorrow":
        try:
            parsed_date = datetime.fromisoformat(date_raw).date()
        except ValueError as exc:
            raise UnparsedWhen(
                f"'date' is neither 'tomorrow' nor an ISO date: {date_raw!r}"
            ) from exc
        at_local = datetime.combine(parsed_date, time(hour, minute), tzinfo=zone)
        if at_local <= now_local:
            raise UnparsedWhen("that date and time is already in the past")
    else:
        at_local = _next_local(
            now_local, hour, minute, tomorrow=date_raw == "tomorrow", weekdays=weekdays
        )

    return ParsedWhen(
        at=at_local.astimezone(UTC),
        local_time=f"{hour:02d}:{minute:02d}",
        timezone=timezone,
        weekdays=weekdays,
        matched="struct",
    )


def next_occurrence_after(
    *, local_time: str, weekdays: tuple[int, ...] | list[int], after: datetime, timezone: str
) -> datetime:
    """The next instant matching ``local_time`` on one of ``weekdays``, strictly after
    ``after`` — the recurring alarm's re-schedule step, on the same DST-safe path."""
    zone = _zone(timezone)
    hour, minute = (int(part) for part in local_time.split(":"))
    return _next_local(
        after.astimezone(zone), hour, minute, tomorrow=False, weekdays=tuple(weekdays)
    ).astimezone(UTC)


__all__ = [
    "DEFAULT_TIMEZONE",
    "MAX_RELATIVE_SECONDS",
    "WEEKDAYS_ALL",
    "WEEKDAYS_WEEKDAY",
    "WEEKDAYS_WEEKEND",
    "ParsedWhen",
    "UnparsedWhen",
    "next_occurrence_after",
    "parse_when_struct",
    "parse_when_text",
]
