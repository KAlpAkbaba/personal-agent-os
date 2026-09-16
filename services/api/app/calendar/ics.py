"""An in-repo iCalendar parser + RRULE expansion (docs/M21_MAIL_CALENDAR_SPEC.md §2).

No new dependency: ``dateutil.rrule`` is already resolved into ``uv.lock`` as a transitive
dependency of ``boto3`` -> ``botocore`` (S3 already needs it), so importing it here adds
nothing to ``pyproject.toml``. This module is the ONE place a VEVENT is parsed or a
recurrence expanded — ``CalDavCalendarProvider`` and ``IcsUrlCalendarProvider`` both hand
their raw ICS bytes here rather than each keeping a private parser.

Parses exactly what the spec needs: VCALENDAR/VEVENT (VTIMEZONE is read only far enough to
skip it — a ``TZID`` is resolved through the system's IANA tzdata via :mod:`zoneinfo`
rather than by re-deriving the VTIMEZONE's own STANDARD/DAYLIGHT arithmetic, which is
already correct for a real zone name like ``Europe/Istanbul``), DTSTART/DTEND with TZID or
``VALUE=DATE`` (all-day), RRULE, EXDATE, RFC 5545 §3.1 line folding and value escaping.
Read-only: nothing here serialises an event back to ICS text (a CalDAV PUT body is built
directly by the provider, never round-tripped through this parser).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

#: The product default (CLAUDE.md, spec §2): a bare local time with no TZID and no
#: VTIMEZONE resolves here, never to the host's own (unrelated) local zone.
DEFAULT_TIMEZONE = "Europe/Istanbul"

#: M2 (security review): spec §2's own bound on how wide a window agenda/free_slots may
#: answer — declared here (the ONE place expansion happens) and re-exported from
#: ``app.calendar.providers`` for backward compatibility. Previously declared in
#: ``providers.py`` and never actually enforced anywhere; :func:`clamp_window` is what
#: enforces it now.
MAX_WINDOW_DAYS = 62
#: Per-event and per-window occurrence caps: a subscribed calendar with one absurd
#: recurring event must not be able to make an agenda answer unboundedly large.
MAX_OCCURRENCES_PER_EVENT = 1000
MAX_OCCURRENCES_PER_WINDOW = 10000
#: The DoS ``expand_events`` used to be open to (M2, verified live): a `FREQ=SECONDLY`
#: event anchored decades before the requested window forces ``dateutil.rrule`` to
#: materialise every intervening occurrence one at a time before ever reaching the
#: window — the library has no way to jump ahead for an arbitrary rule. Neither
#: ``rrule.between()`` nor ``rrule.xafter()`` change this (both iterate from DTSTART
#: internally too); the only way to bound the WORK rather than just the RESULT is to cap
#: the raw number of occurrences dateutil is allowed to generate while searching,
#: independent of how many of them actually land inside ``[start, end)``.
MAX_RRULE_RAW_SCAN = 200_000
#: B46 (req 357, 358): the furthest ahead a reminder may be, and how many one event keeps.
MAX_REMINDER_MINUTES = 7 * 24 * 60
MAX_ALARMS_PER_EVENT = 5


@dataclass(frozen=True, slots=True)
class VEvent:
    """One VEVENT block, unexpanded (a recurring event is ONE of these, not N)."""

    uid: str
    summary: str
    dtstart: datetime  # always tz-aware
    dtend: datetime  # always tz-aware
    all_day: bool
    rrule: str | None = None
    exdates: tuple[datetime, ...] = ()
    location: str | None = None
    #: B46 (req 357): minutes before the start each VALARM asks for.
    alarms: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One concrete occurrence of a (possibly recurring) event, within a window."""

    uid: str
    summary: str
    start: datetime
    end: datetime
    all_day: bool
    #: B46 (req 358): the event's reminders (minutes before its start).
    reminders: tuple[int, ...] = ()
    #: B46: an occurrence of a recurring event - it cannot be moved or removed alone.
    recurring: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "summary": self.summary,
            # The event's OWN zone (Europe/Istanbul, per the fixture and the product
            # default), never normalised to UTC — the owner's local time is the truth an
            # answer speaks, and truth.json's own oracle carries the same "+03:00".
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "all_day": self.all_day,
        }


def _unfold(text: str) -> list[str]:
    """RFC 5545 §3.1: a line break followed by a single space or tab is a fold, not a new
    property — it is removed and the continuation is appended to the previous line."""
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    for line in raw:
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        elif line.strip() == "":
            continue
        else:
            lines.append(line)
    return lines


def _unescape(value: str) -> str:
    """RFC 5545 §3.3.11: ``\\n``/``\\N`` -> newline, ``\\,``/``\\;`` -> literal, ``\\\\`` ->
    a single backslash — applied in one pass so an escaped backslash is never re-read as
    the start of a second escape."""
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt in ("n", "N"):
                out.append("\n")
            elif nxt in (",", ";", "\\"):
                out.append(nxt)
            else:
                out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_prop(line: str) -> tuple[str, dict[str, str], str]:
    """``"DTSTART;TZID=Europe/Istanbul:20260910T150000"`` ->
    ``("DTSTART", {"TZID": "Europe/Istanbul"}, "20260910T150000")``."""
    if ":" not in line:
        return line.strip().upper(), {}, ""
    head, value = line.split(":", 1)
    parts = head.split(";")
    name = parts[0].strip().upper()
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip().upper()] = v.strip()
    return name, params, value.strip()


def _zone(tzid: str) -> ZoneInfo:
    try:
        return ZoneInfo(tzid)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def parse_datetime(value: str, params: dict[str, str]) -> tuple[datetime, bool]:
    """(aware datetime, all_day) for one DTSTART/DTEND/EXDATE value."""
    if params.get("VALUE") == "DATE" or (len(value) == 8 and "T" not in value):
        naive = datetime.strptime(value, "%Y%m%d")
        return naive.replace(tzinfo=_zone(DEFAULT_TIMEZONE)), True
    if value.endswith("Z"):
        naive = datetime.strptime(value[:-1], "%Y%m%dT%H%M%S")
        return naive.replace(tzinfo=UTC), False
    tzid = params.get("TZID", DEFAULT_TIMEZONE)
    naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    return naive.replace(tzinfo=_zone(tzid)), False


def parse_calendar(text: str) -> list[VEvent]:
    """Every VEVENT in ``text`` (a whole VCALENDAR document), unexpanded."""
    lines = _unfold(text)
    events: list[VEvent] = []
    cur: dict[str, Any] | None = None
    exdates: list[datetime] = []
    in_vevent = False
    in_vtimezone = False
    in_valarm = False
    alarms: list[int] = []
    for line in lines:
        name, params, value = _split_prop(line)
        if name == "BEGIN" and value == "VEVENT":
            in_vevent = True
            cur = {}
            exdates = []
            alarms = []
            in_valarm = False
            continue
        if in_vevent and name == "BEGIN" and value == "VALARM":
            in_valarm = True
            continue
        if in_valarm:
            # B46 (req 357): a VALARM's own properties (TRIGGER, ACTION, DESCRIPTION - and a
            # SUMMARY on an email alarm) belong to the alarm, never to the event around it.
            if name == "END" and value == "VALARM":
                in_valarm = False
            elif name == "TRIGGER":
                minutes = parse_trigger_minutes(value, params)
                if (
                    minutes is not None
                    and minutes not in alarms
                    and len(alarms) < MAX_ALARMS_PER_EVENT
                ):
                    alarms.append(minutes)
            continue
        if name == "BEGIN" and value == "VTIMEZONE":
            in_vtimezone = True
            continue
        if name == "END" and value == "VTIMEZONE":
            in_vtimezone = False
            continue
        if in_vtimezone:
            # A VTIMEZONE's own STANDARD/DAYLIGHT offsets are read no further than this —
            # module docstring: a TZID is resolved through zoneinfo, which already has the
            # real rule for a real IANA name.
            continue
        if name == "END" and value == "VEVENT":
            in_vevent = False
            if cur is not None and "UID" in cur and "DTSTART" in cur:
                dtstart, all_day = cur["DTSTART"]
                if "DTEND" in cur:
                    dtend, _ = cur["DTEND"]
                else:
                    dtend = dtstart + (timedelta(days=1) if all_day else timedelta(hours=1))
                events.append(
                    VEvent(
                        uid=cur["UID"],
                        summary=cur.get("SUMMARY", ""),
                        dtstart=dtstart,
                        dtend=dtend,
                        all_day=all_day,
                        rrule=cur.get("RRULE"),
                        exdates=tuple(exdates),
                        location=cur.get("LOCATION"),
                        alarms=tuple(sorted(alarms)),
                    )
                )
            cur = None
            continue
        if not in_vevent or cur is None:
            continue
        if name == "UID":
            cur["UID"] = value
        elif name == "SUMMARY":
            cur["SUMMARY"] = _unescape(value)
        elif name == "LOCATION":
            cur["LOCATION"] = _unescape(value)
        elif name == "DTSTART":
            cur["DTSTART"] = parse_datetime(value, params)
        elif name == "DTEND":
            cur["DTEND"] = parse_datetime(value, params)
        elif name == "RRULE":
            cur["RRULE"] = value
        elif name == "EXDATE":
            # A single EXDATE line may carry a comma-separated list (RFC 5545 §3.8.5.1).
            for piece in value.split(","):
                if piece:
                    exd, _ = parse_datetime(piece, params)
                    exdates.append(exd)
    return events


def _occ_key(dt: datetime) -> tuple[int, int, int, int, int, int]:
    u = dt.astimezone(UTC)
    return (u.year, u.month, u.day, u.hour, u.minute, u.second)


def clamp_window(start: datetime, end: datetime) -> tuple[datetime, datetime, bool]:
    """M2 (security review): enforce :data:`MAX_WINDOW_DAYS` — a caller asking for more
    than that gets the SAME answer clamped to it (the third element is ``True`` when this
    actually narrowed the window), never an unbounded expansion. A negative or zero-width
    request (``end <= start``) is left alone; there is nothing to clamp and the caller's
    own validation (or the empty result it naturally produces) is the honest answer."""
    if end <= start:
        return start, end, False
    limit = start + timedelta(days=MAX_WINDOW_DAYS)
    if end > limit:
        return start, limit, True
    return start, end, False


def _expand_with_caps(
    events: list[VEvent], *, start: datetime, end: datetime
) -> tuple[list[Occurrence], bool]:
    """The shared core :func:`expand_events`/:func:`expand_events_report` both call.

    A non-recurring event is one occurrence, checked directly; a recurring event is
    walked through ``dateutil.rrule`` from its own DTSTART, stopping the walk once an
    occurrence starts at or after ``end`` (never truncated by a COUNT/UNTIL the rule did
    not actually carry — an unbounded rule is bounded by the window instead) — AND (M2)
    bounded by three independent caps, since the window alone does not bound the WORK
    when an event's own DTSTART sits far before it (see :data:`MAX_RRULE_RAW_SCAN`'s own
    docstring): the raw number of occurrences dateutil is allowed to generate while
    searching for this one event (``MAX_RRULE_RAW_SCAN``), the number actually kept for
    one event (``MAX_OCCURRENCES_PER_EVENT``), and the total kept across every event in
    this call (``MAX_OCCURRENCES_PER_WINDOW``).
    """
    out: list[Occurrence] = []
    truncated = False
    for ev in events:
        duration = ev.dtend - ev.dtstart
        if ev.rrule:
            rule_text = ev.rrule if ev.rrule.upper().startswith("RRULE:") else f"RRULE:{ev.rrule}"
            rule = rrulestr(rule_text, dtstart=ev.dtstart)
            exdate_keys = {_occ_key(d) for d in ev.exdates}
            per_event = 0
            for scanned, occ_start in enumerate(rule, start=1):
                if scanned > MAX_RRULE_RAW_SCAN:
                    truncated = True
                    break
                if occ_start >= end:
                    break
                occ_end = occ_start + duration
                if occ_end <= start:
                    continue
                if _occ_key(occ_start) in exdate_keys:
                    continue
                out.append(
                    Occurrence(
                        uid=ev.uid,
                        summary=ev.summary,
                        start=occ_start,
                        end=occ_end,
                        all_day=ev.all_day,
                        reminders=ev.alarms,
                        recurring=True,
                    )
                )
                per_event += 1
                if per_event >= MAX_OCCURRENCES_PER_EVENT:
                    truncated = True
                    break
                if len(out) >= MAX_OCCURRENCES_PER_WINDOW:
                    truncated = True
                    break
        else:
            if ev.dtend <= start or ev.dtstart >= end:
                continue
            out.append(
                Occurrence(
                    uid=ev.uid,
                    summary=ev.summary,
                    start=ev.dtstart,
                    end=ev.dtend,
                    all_day=ev.all_day,
                    reminders=ev.alarms,
                )
            )
        if len(out) >= MAX_OCCURRENCES_PER_WINDOW:
            truncated = True
            break
    out.sort(key=lambda o: o.start)
    return out, truncated


def expand_events(events: list[VEvent], *, start: datetime, end: datetime) -> list[Occurrence]:
    """Every occurrence of every event overlapping ``[start, end)``, earliest first —
    bounded (M2, module docstring); see :func:`expand_events_report` for a caller that
    also wants to know whether the caps actually bit."""
    occurrences, _truncated = _expand_with_caps(events, start=start, end=end)
    return occurrences


def expand_events_report(
    events: list[VEvent], *, start: datetime, end: datetime
) -> tuple[list[Occurrence], bool]:
    """Same as :func:`expand_events`, plus whether any of the M2 caps actually
    truncated the result — ``app.calendar.providers`` folds this into a
    ``truncated: true`` a caller can surface (a receipt, a speech line)."""
    return _expand_with_caps(events, start=start, end=end)


class RRuleError(ValueError):
    """A recurrence rule (or reminder) this writer will not send (B46 req 356, 357)."""


_RRULE_FREQS: Final[tuple[str, ...]] = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
_RRULE_DAYS: Final[tuple[str, ...]] = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_RRULE_ORDER: Final[tuple[str, ...]] = ("FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY")
MAX_RRULE_INTERVAL: Final = 99
MAX_RRULE_COUNT: Final = 730
_UNTIL_RE = re.compile(r"^\d{8}(T\d{6}Z)?$")
_TRIGGER_RE = re.compile(
    r"^(?P<sign>[+-])?P(?:(?P<w>\d+)W)?(?:(?P<d>\d+)D)?"
    r"(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)


def validate_rrule(rule: str) -> str:
    """The canonical form of a rule this writer is willing to send, or :class:`RRuleError`.

    A deliberately small vocabulary - FREQ (daily/weekly/monthly/yearly), INTERVAL, COUNT,
    UNTIL, BYDAY - because every rule the owner can say maps into it and a rule the owner
    cannot hear read back must not reach their calendar. The result is also parsed by
    dateutil, the same library the reader expands with, so what is written can be read."""
    text = (rule or "").strip()
    if text.upper().startswith("RRULE:"):
        text = text[6:]
    if not text or len(text) > 200:
        raise RRuleError("empty or oversized rule")
    parts: dict[str, str] = {}
    for piece in text.split(";"):
        if "=" not in piece:
            raise RRuleError(f"malformed part {piece!r}")
        key, value = piece.split("=", 1)
        key, value = key.strip().upper(), value.strip().upper()
        if key not in _RRULE_ORDER or key in parts:
            raise RRuleError(f"unsupported or repeated part {key}")
        parts[key] = value
    if parts.get("FREQ") not in _RRULE_FREQS:
        raise RRuleError("FREQ must be DAILY, WEEKLY, MONTHLY or YEARLY")
    interval = parts.get("INTERVAL")
    if interval is not None and not (
        interval.isdigit() and 1 <= int(interval) <= MAX_RRULE_INTERVAL
    ):
        raise RRuleError("INTERVAL out of range")
    count = parts.get("COUNT")
    if count is not None and not (count.isdigit() and 1 <= int(count) <= MAX_RRULE_COUNT):
        raise RRuleError("COUNT out of range")
    if count is not None and "UNTIL" in parts:
        raise RRuleError("COUNT and UNTIL together")
    if "UNTIL" in parts and not _UNTIL_RE.match(parts["UNTIL"]):
        raise RRuleError("UNTIL must be a date or a UTC date-time")
    if "BYDAY" in parts:
        days = parts["BYDAY"].split(",")
        if parts.get("FREQ") != "WEEKLY":
            raise RRuleError("BYDAY is accepted only on a weekly rule")
        if not days or any(d not in _RRULE_DAYS for d in days) or len(set(days)) != len(days):
            raise RRuleError("BYDAY must name weekdays (MO..SU) once each")
        parts["BYDAY"] = ",".join(sorted(days, key=_RRULE_DAYS.index))
    if parts.get("INTERVAL") == "1":
        parts.pop("INTERVAL")
    canonical = ";".join(f"{key}={parts[key]}" for key in _RRULE_ORDER if key in parts)
    try:
        rrulestr(canonical, dtstart=datetime(2026, 1, 1, tzinfo=UTC))
    except (ValueError, TypeError) as exc:
        raise RRuleError(str(exc)) from exc
    return canonical


def parse_trigger_minutes(value: str, params: dict[str, str]) -> int | None:
    """Minutes BEFORE the event's start a VALARM asks for, or None for a trigger this
    reader does not turn into a reminder (an absolute time, one related to the end, one
    after the start, or one further ahead than :data:`MAX_REMINDER_MINUTES`)."""
    if params.get("VALUE", "").upper() == "DATE-TIME":
        return None
    if params.get("RELATED", "START").upper() != "START":
        return None
    match = _TRIGGER_RE.match(value.strip().upper())
    if match is None:
        return None
    total = (
        int(match["w"] or 0) * 7 * 1440
        + int(match["d"] or 0) * 1440
        + int(match["h"] or 0) * 60
        + int(match["m"] or 0)
        + int(match["s"] or 0) // 60
    )
    if match["sign"] != "-" and total > 0:
        return None
    return total if total <= MAX_REMINDER_MINUTES else None


def build_vevent(
    *,
    uid: str,
    summary: str,
    start: datetime,
    end: datetime,
    location: str | None = None,
    rrule: str | None = None,
    reminder_minutes: int | None = None,
) -> str:
    """A single VEVENT, folded into a full VCALENDAR document — the PUT body
    ``CalDavCalendarProvider.create``/``update`` sends. Escaping mirrors :func:`_unescape`
    in reverse (a comma/semicolon/backslash in a spoken summary must survive the wire)."""

    def esc(value: str) -> str:
        return (
            value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
        )

    def fmt(dt: datetime) -> str:
        return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//PagentOS//M21//TR",
        "BEGIN:VEVENT",
        f"UID:{esc(uid)}",
        f"DTSTAMP:{fmt(datetime.now(UTC))}",
        f"DTSTART:{fmt(start)}",
        f"DTEND:{fmt(end)}",
        f"SUMMARY:{esc(summary)}",
    ]
    if location:
        lines.append(f"LOCATION:{esc(location)}")
    # B46 (req 356): the rule the owner heard read back, in its validated canonical form.
    if rrule:
        lines.append(f"RRULE:{validate_rrule(rrule)}")
    # B46 (req 357): one display alarm the given minutes before the start.
    if reminder_minutes is not None:
        minutes = int(reminder_minutes)
        if not 0 <= minutes <= MAX_REMINDER_MINUTES:
            raise RRuleError(f"reminder out of range: {minutes}")
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{esc(summary)}",
            f"TRIGGER:-PT{minutes}M",
            "END:VALARM",
        ]
    lines += ["END:VEVENT", "END:VCALENDAR", ""]
    return "\r\n".join(lines)


__all__ = [
    "DEFAULT_TIMEZONE",
    "MAX_REMINDER_MINUTES",
    "RRuleError",
    "parse_trigger_minutes",
    "validate_rrule",
    "MAX_OCCURRENCES_PER_EVENT",
    "MAX_OCCURRENCES_PER_WINDOW",
    "MAX_RRULE_RAW_SCAN",
    "MAX_WINDOW_DAYS",
    "Occurrence",
    "VEvent",
    "build_vevent",
    "clamp_window",
    "expand_events",
    "expand_events_report",
    "parse_calendar",
    "parse_datetime",
]
