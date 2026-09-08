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

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
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


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One concrete occurrence of a (possibly recurring) event, within a window."""

    uid: str
    summary: str
    start: datetime
    end: datetime
    all_day: bool

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
    for line in lines:
        name, params, value = _split_prop(line)
        if name == "BEGIN" and value == "VEVENT":
            in_vevent = True
            cur = {}
            exdates = []
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


def build_vevent(
    *,
    uid: str,
    summary: str,
    start: datetime,
    end: datetime,
    location: str | None = None,
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
    lines += ["END:VEVENT", "END:VCALENDAR", ""]
    return "\r\n".join(lines)


__all__ = [
    "DEFAULT_TIMEZONE",
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
