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


def expand_events(events: list[VEvent], *, start: datetime, end: datetime) -> list[Occurrence]:
    """Every occurrence of every event overlapping ``[start, end)``, earliest first.

    A non-recurring event is one occurrence, checked directly; a recurring event is
    walked through ``dateutil.rrule`` from its own DTSTART, stopping the walk itself once
    an occurrence starts at or after ``end`` (never truncated by a COUNT/UNTIL the rule did
    not actually carry — an unbounded rule is bounded by the window instead).
    """
    out: list[Occurrence] = []
    for ev in events:
        duration = ev.dtend - ev.dtstart
        if ev.rrule:
            rule_text = ev.rrule if ev.rrule.upper().startswith("RRULE:") else f"RRULE:{ev.rrule}"
            rule = rrulestr(rule_text, dtstart=ev.dtstart)
            exdate_keys = {_occ_key(d) for d in ev.exdates}
            for occ_start in rule:
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
    out.sort(key=lambda o: o.start)
    return out


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
    "Occurrence",
    "VEvent",
    "build_vevent",
    "expand_events",
    "parse_calendar",
    "parse_datetime",
]
