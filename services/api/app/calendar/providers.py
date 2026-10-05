"""Calendar providers behind Protocols (docs/M21_MAIL_CALENDAR_SPEC.md §2, ADR-0084).

``CalendarProvider``/``CalendarWriter`` are the whole surface ``app.calendar.service.
CalendarService`` ever touches. ``CalDavCalendarProvider`` (``httpx``: PROPFIND + REPORT
``calendar-query``, PUT a VEVENT) and ``IcsUrlCalendarProvider`` (a read-only subscription
URL) are the real implementations, both built on the in-repo parser in
``app.calendar.ics`` — no second iCalendar parser anywhere. ``FakeCalendarProvider``/
``FakeCalendarWriter`` serve the deterministic fixture calendar for tests and the corpus;
production never imports them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.calendar.ics import (
    MAX_WINDOW_DAYS,  # M2: canonical value now lives in app.calendar.ics; re-exported here
    Occurrence,
    build_vevent,
    clamp_window,
    expand_events_report,
    parse_calendar,
)


@dataclass(frozen=True, slots=True)
class ProposalInput:
    """What ``CalendarWriter.create``/``update`` need — assembled by ``CalendarService``
    from a ``CalendarProposalRow`` at commit time, never a live ORM row."""

    kind: str  # create | reschedule
    event_uid: str | None
    summary: str
    start: datetime
    end: datetime
    location: str | None = None
    #: B46 (req 356, 357): the validated rule and the reminder read back to the owner.
    rrule: str | None = None
    reminder_minutes: int | None = None


class CalendarProvider(Protocol):
    def calendars(self) -> list[str]: ...

    def events(self, start: datetime, end: datetime) -> list[Occurrence]: ...

    def get_event(self, uid: str) -> Occurrence | None: ...

    def free_slots(
        self, start: datetime, end: datetime, duration_minutes: int
    ) -> list[tuple[datetime, datetime]]: ...


class CalendarWriter(Protocol):
    def create(self, proposal: ProposalInput) -> str:
        """Creates the event and returns its provider uid."""
        ...

    def update(self, event_uid: str, changes: ProposalInput) -> str:
        """Updates ``event_uid`` (a reschedule) and returns the (possibly unchanged) uid."""
        ...

    def delete(self, event_uid: str) -> None:
        """B46 (req 354): removes ``event_uid`` - reached only by a confirmed cancel."""
        ...


# --------------------------------------------------------------- shared free-slot math


def compute_free_slots(
    busy: list[Occurrence], *, start: datetime, end: datetime, duration_minutes: int
) -> list[tuple[datetime, datetime]]:
    """The gaps in ``[start, end)`` at least ``duration_minutes`` long, given the
    (already-expanded) ``busy`` occurrences — the one free-slot algorithm both providers
    and ``FakeCalendarProvider`` share, so a real CalDAV answer and the fixture answer are
    computed the same way."""
    from datetime import timedelta

    timed = sorted(
        (o for o in busy if not o.all_day and o.end > start and o.start < end),
        key=lambda o: o.start,
    )
    slots: list[tuple[datetime, datetime]] = []
    cursor = start
    min_gap = timedelta(minutes=duration_minutes)
    for occ in timed:
        occ_start = max(occ.start, start)
        if occ_start - cursor >= min_gap:
            slots.append((cursor, occ_start))
        cursor = max(cursor, min(occ.end, end))
    if end - cursor >= min_gap:
        slots.append((cursor, end))
    return slots


def find_conflicts(
    busy: list[Occurrence], *, start: datetime, end: datetime, exclude_uid: str | None = None
) -> list[dict[str, str]]:
    """``[{uid, summary}]`` of every existing occurrence overlapping ``[start, end)`` —
    named in a proposal's read-back verbatim (spec §3), never silently dropped."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for occ in busy:
        if occ.all_day:
            continue
        if exclude_uid is not None and occ.uid == exclude_uid:
            continue
        if occ.start < end and occ.end > start:
            key = f"{occ.uid}:{occ.start.isoformat()}"
            if key in seen:
                continue
            seen.add(key)
            out.append({"uid": occ.uid, "summary": occ.summary})
    return out


# ---------------------------------------------------------------------------- CalDAV


class CalDavCalendarProvider:
    """``httpx``: PROPFIND the calendar collection, REPORT ``calendar-query`` for a
    window, PUT a VEVENT for :class:`CalendarWriter`. iCalendar bytes are parsed by
    ``app.calendar.ics`` — no second parser."""

    def __init__(
        self, *, base_url: str, username: str, password: str, timeout: float = 15.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = (username, password)
        self._timeout = timeout
        #: M2 (security review): whether the LAST events()/free_slots() call had its
        #: window narrowed to MAX_WINDOW_DAYS, and whether the RRULE expansion caps
        #: (app.calendar.ics) actually bit — read by app.calendar.service right after the
        #: call and folded into the receipt (``window_clamped``/``truncated``).
        self.last_window_clamped = False
        self.last_truncated = False

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self._base_url, auth=self._auth, timeout=self._timeout)

    def calendars(self) -> list[str]:
        with self._client() as client:
            response = client.request(
                "PROPFIND",
                "/",
                headers={"Depth": "1", "Content-Type": "application/xml"},
            )
            response.raise_for_status()
            return [self._base_url]

    def _report(self, start: datetime, end: datetime) -> list[Occurrence]:
        """The raw fetch+expand — NOT window-clamped itself (``get_event`` below needs a
        wide internal lookup window regardless of the spec §2 agenda/free_slots bound);
        clamping happens in the PUBLIC ``events``/``free_slots`` entry points instead."""
        body = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
            "<D:prop><C:calendar-data/></D:prop>"
            '<C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT">'
            f'<C:time-range start="{start.strftime("%Y%m%dT%H%M%SZ")}" '
            f'end="{end.strftime("%Y%m%dT%H%M%SZ")}"/>'
            "</C:comp-filter></C:comp-filter></C:filter>"
            "</C:calendar-query>"
        )
        with self._client() as client:
            response = client.request(
                "REPORT",
                "/",
                content=body,
                headers={"Depth": "1", "Content-Type": "application/xml"},
            )
            response.raise_for_status()
            events = parse_calendar(response.text)
            occurrences, truncated = expand_events_report(events, start=start, end=end)
            self.last_truncated = truncated
            return occurrences

    def events(self, start: datetime, end: datetime) -> list[Occurrence]:
        clamped_start, clamped_end, clamped = clamp_window(start, end)
        self.last_window_clamped = clamped
        return self._report(clamped_start, clamped_end)

    def get_event(self, uid: str) -> Occurrence | None:
        from datetime import timedelta

        now = datetime.now(start_timezone())
        occs = self._report(now - timedelta(days=365), now + timedelta(days=365))
        for occ in occs:
            if occ.uid == uid:
                return occ
        return None

    def free_slots(
        self, start: datetime, end: datetime, duration_minutes: int
    ) -> list[tuple[datetime, datetime]]:
        clamped_start, clamped_end, clamped = clamp_window(start, end)
        self.last_window_clamped = clamped
        busy = self._report(clamped_start, clamped_end)
        return compute_free_slots(
            busy, start=clamped_start, end=clamped_end, duration_minutes=duration_minutes
        )

    def create(self, proposal: ProposalInput) -> str:
        event_uid = proposal.event_uid or f"pagentos-{uuid.uuid4()}@pagentos"
        body = build_vevent(
            uid=event_uid,
            summary=proposal.summary,
            start=proposal.start,
            end=proposal.end,
            location=proposal.location,
            rrule=proposal.rrule,
            reminder_minutes=proposal.reminder_minutes,
        )
        with self._client() as client:
            response = client.put(
                f"/{event_uid}.ics", content=body, headers={"Content-Type": "text/calendar"}
            )
            response.raise_for_status()
        return event_uid

    def update(self, event_uid: str, changes: ProposalInput) -> str:
        body = build_vevent(
            uid=event_uid,
            summary=changes.summary,
            start=changes.start,
            end=changes.end,
            location=changes.location,
            rrule=changes.rrule,
            reminder_minutes=changes.reminder_minutes,
        )
        with self._client() as client:
            response = client.put(
                f"/{event_uid}.ics", content=body, headers={"Content-Type": "text/calendar"}
            )
            response.raise_for_status()
        return event_uid

    def delete(self, event_uid: str) -> None:
        """B46 (req 354): a confirmed cancel. An event already gone (404) is the
        outcome the owner asked for."""
        with self._client() as client:
            response = client.delete(f"/{event_uid}.ics")
            if response.status_code == 404:
                return
            response.raise_for_status()


def start_timezone():
    from zoneinfo import ZoneInfo

    from app.calendar.ics import DEFAULT_TIMEZONE

    return ZoneInfo(DEFAULT_TIMEZONE)


class IcsUrlCalendarProvider:
    """A read-only ICS subscription URL (spec §2) — no ``CalendarWriter``: a subscription
    calendar cannot be written to, so ``app.main.create_app`` never pairs this with a
    writer."""

    def __init__(self, *, url: str, timeout: float = 15.0) -> None:
        self._url = url
        self._timeout = timeout
        #: M2 (security review) — see ``CalDavCalendarProvider``'s identical fields.
        self.last_window_clamped = False
        self.last_truncated = False

    def _events(self) -> list:
        response = httpx.get(self._url, timeout=self._timeout)
        response.raise_for_status()
        return parse_calendar(response.text)

    def calendars(self) -> list[str]:
        return [self._url]

    def _expand(self, start: datetime, end: datetime) -> list[Occurrence]:
        """The raw expand — NOT window-clamped (``get_event`` needs a wide internal
        lookup window); clamping happens in the PUBLIC ``events``/``free_slots`` below."""
        occurrences, truncated = expand_events_report(self._events(), start=start, end=end)
        self.last_truncated = truncated
        return occurrences

    def events(self, start: datetime, end: datetime) -> list[Occurrence]:
        clamped_start, clamped_end, clamped = clamp_window(start, end)
        self.last_window_clamped = clamped
        return self._expand(clamped_start, clamped_end)

    def get_event(self, uid: str) -> Occurrence | None:
        from datetime import timedelta

        now = datetime.now(start_timezone())
        for occ in self._expand(now - timedelta(days=365), now + timedelta(days=365)):
            if occ.uid == uid:
                return occ
        return None

    def free_slots(
        self, start: datetime, end: datetime, duration_minutes: int
    ) -> list[tuple[datetime, datetime]]:
        clamped_start, clamped_end, clamped = clamp_window(start, end)
        self.last_window_clamped = clamped
        busy = self._expand(clamped_start, clamped_end)
        return compute_free_slots(
            busy, start=clamped_start, end=clamped_end, duration_minutes=duration_minutes
        )


# ------------------------------------------------- Google Calendar / Microsoft Graph
#
# Card mail-accounts-connect: the owner's calendars on his connected Gmail and Microsoft
# 365 accounts, read over their JSON APIs with the account's OAuth access token
# (``token`` - ``app.accounts.service.AccountsService.access_token`` behind it). Read-only
# here: event writes stay on the existing CalDAV writer path.


@dataclass(frozen=True, slots=True)
class AccountOccurrence(Occurrence):
    """An occurrence that knows which of the owner's accounts it came from."""

    account: str = ""

    def as_dict(self) -> dict[str, Any]:
        out = Occurrence.as_dict(self)
        if self.account:
            out["account"] = self.account
        return out


class CalendarApiError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__(f"calendar api answered HTTP {status}")
        self.status = status


def _api_get(
    url: str,
    *,
    token: Any,
    params: dict[str, Any],
    transport: httpx.BaseTransport | None,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    with httpx.Client(transport=transport, timeout=timeout) as http:
        response = http.get(
            url, params=params, headers={**(headers or {}), "Authorization": f"Bearer {token()}"}
        )
    if response.status_code != 200:
        raise CalendarApiError(response.status_code)
    data = response.json()
    return data if isinstance(data, dict) else {}


def _rfc3339(value: datetime) -> str:
    from datetime import UTC

    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _ApiCalendar:
    MAX_EVENTS = 250

    def __init__(
        self, *, token: Any, transport: httpx.BaseTransport | None = None, timeout: float = 20.0
    ) -> None:
        self._token = token
        self._transport = transport
        self._timeout = timeout
        self.last_window_clamped = False
        self.last_truncated = False

    def _window(self, start: datetime, end: datetime) -> list[Occurrence]:
        raise NotImplementedError

    def events(self, start: datetime, end: datetime) -> list[Occurrence]:
        clamped_start, clamped_end, clamped = clamp_window(start, end)
        self.last_window_clamped = clamped
        return self._window(clamped_start, clamped_end)

    def get_event(self, uid: str) -> Occurrence | None:
        from datetime import timedelta

        now = datetime.now(start_timezone())
        for occ in self._window(
            now - timedelta(days=MAX_WINDOW_DAYS), now + timedelta(days=MAX_WINDOW_DAYS)
        ):
            if occ.uid == uid:
                return occ
        return None

    def free_slots(
        self, start: datetime, end: datetime, duration_minutes: int
    ) -> list[tuple[datetime, datetime]]:
        clamped_start, clamped_end, clamped = clamp_window(start, end)
        self.last_window_clamped = clamped
        return compute_free_slots(
            self._window(clamped_start, clamped_end),
            start=clamped_start,
            end=clamped_end,
            duration_minutes=duration_minutes,
        )


class GoogleCalendarProvider(_ApiCalendar):
    API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

    def calendars(self) -> list[str]:
        return ["primary"]

    def _window(self, start: datetime, end: datetime) -> list[Occurrence]:
        data = _api_get(
            self.API,
            token=self._token,
            params={
                "timeMin": _rfc3339(start),
                "timeMax": _rfc3339(end),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": self.MAX_EVENTS,
            },
            transport=self._transport,
            timeout=self._timeout,
        )
        self.last_truncated = bool(data.get("nextPageToken"))
        out: list[Occurrence] = []
        for item in data.get("items") or []:
            occ = self._occurrence(item)
            if occ is not None:
                out.append(occ)
        return out

    @staticmethod
    def _when(value: dict[str, Any]) -> tuple[datetime, bool] | None:
        if value.get("dateTime"):
            moment = datetime.fromisoformat(str(value["dateTime"]).replace("Z", "+00:00"))
            return moment, False
        if value.get("date"):
            day = datetime.fromisoformat(str(value["date"]))
            return day.replace(tzinfo=start_timezone()), True
        return None

    def _occurrence(self, item: dict[str, Any]) -> Occurrence | None:
        if item.get("status") == "cancelled" or not item.get("id"):
            return None
        start = self._when(item.get("start") or {})
        end = self._when(item.get("end") or {})
        if start is None or end is None:
            return None
        reminders: tuple[int, ...] = ()
        rem = item.get("reminders") or {}
        if not rem.get("useDefault"):
            reminders = tuple(
                int(o["minutes"]) for o in rem.get("overrides") or [] if "minutes" in o
            )
        return Occurrence(
            uid=str(item["id"]),
            summary=str(item.get("summary") or ""),
            start=start[0],
            end=end[0],
            all_day=start[1],
            reminders=reminders,
            recurring=bool(item.get("recurringEventId")),
        )


class GraphCalendarProvider(_ApiCalendar):
    API = "https://graph.microsoft.com/v1.0/me/calendarView"

    def calendars(self) -> list[str]:
        return ["calendar"]

    def _window(self, start: datetime, end: datetime) -> list[Occurrence]:
        data = _api_get(
            self.API,
            token=self._token,
            params={
                "startDateTime": _rfc3339(start),
                "endDateTime": _rfc3339(end),
                "$top": self.MAX_EVENTS,
                "$select": "id,subject,start,end,isAllDay,isCancelled,isReminderOn,"
                "reminderMinutesBeforeStart,type",
            },
            headers={"Prefer": 'outlook.timezone="UTC"'},
            transport=self._transport,
            timeout=self._timeout,
        )
        self.last_truncated = bool(data.get("@odata.nextLink"))
        out: list[Occurrence] = []
        for item in data.get("value") or []:
            occ = self._occurrence(item)
            if occ is not None:
                out.append(occ)
        return out

    @staticmethod
    def _when(value: dict[str, Any]) -> datetime | None:
        from datetime import UTC
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        raw = str(value.get("dateTime") or "")
        if not raw:
            return None
        head, _, fraction = raw.partition(".")
        moment = datetime.fromisoformat(f"{head}.{fraction[:6]}" if fraction else head)
        zone_name = str(value.get("timeZone") or "UTC")
        try:
            zone = UTC if zone_name.upper() == "UTC" else ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError):
            zone = UTC
        return moment.replace(tzinfo=zone)

    def _occurrence(self, item: dict[str, Any]) -> Occurrence | None:
        if item.get("isCancelled") or not item.get("id"):
            return None
        start = self._when(item.get("start") or {})
        end = self._when(item.get("end") or {})
        if start is None or end is None:
            return None
        reminders: tuple[int, ...] = ()
        if item.get("isReminderOn") and item.get("reminderMinutesBeforeStart") is not None:
            reminders = (int(item["reminderMinutesBeforeStart"]),)
        return Occurrence(
            uid=str(item["id"]),
            summary=str(item.get("subject") or ""),
            start=start,
            end=end,
            all_day=bool(item.get("isAllDay")),
            reminders=reminders,
            recurring=item.get("type") in ("occurrence", "exception"),
        )


class MultiAccountCalendarProvider:
    """Every connected account's calendar as ONE ``CalendarProvider``, each event tagged
    with its account's name. A failing account is skipped, reported through ``on_synced``
    and marks the pass ``last_truncated`` - so ``CalendarService.sync`` removes nothing on a
    pass that could not see every account (an unreachable account is not evidence that its
    events were deleted). Only when every account fails does the call raise."""

    def __init__(self, loader: Any, *, on_synced: Any = None) -> None:
        self._loader = loader
        self._on_synced = on_synced
        self.last_window_clamped = False
        self.last_truncated = False
        #: account name -> "ok" or the exception class of its last read.
        self.last_status: dict[str, str] = {}

    def accounts(self) -> list[tuple[str, Any]]:
        return list(self._loader())

    def _report(self, name: str, error_class: str | None) -> None:
        self.last_status[name] = error_class or "ok"
        if self._on_synced is not None:
            try:
                self._on_synced(name, error_class)
            except Exception:  # noqa: BLE001 - bookkeeping never breaks a read
                pass

    def _each(self, call: Any) -> list[tuple[str, Any, Any]]:
        results: list[tuple[str, Any, Any]] = []
        errors: list[BaseException] = []
        accounts = self.accounts()
        self.last_status = {}
        self.last_window_clamped = False
        self.last_truncated = False
        for name, provider in accounts:
            try:
                value = call(provider)
            except Exception as exc:  # noqa: BLE001 - one account never silences another
                errors.append(exc)
                self._report(name, type(exc).__name__)
                continue
            self._report(name, None)
            results.append((name, provider, value))
            self.last_window_clamped |= bool(getattr(provider, "last_window_clamped", False))
            self.last_truncated |= bool(getattr(provider, "last_truncated", False))
        if errors:
            self.last_truncated = True
            if not results:
                raise errors[0]
        return results

    @staticmethod
    def _tag(name: str, occ: Occurrence) -> AccountOccurrence:
        return AccountOccurrence(
            uid=occ.uid,
            summary=occ.summary,
            start=occ.start,
            end=occ.end,
            all_day=occ.all_day,
            reminders=occ.reminders,
            recurring=occ.recurring,
            account=name,
        )

    def calendars(self) -> list[str]:
        return [
            f"{name}:{cal}"
            for name, _p, cals in self._each(lambda p: p.calendars())
            for cal in cals
        ]

    def events(self, start: datetime, end: datetime) -> list[Occurrence]:
        merged = [
            self._tag(name, occ)
            for name, _p, occs in self._each(lambda p: p.events(start, end))
            for occ in occs
        ]
        return sorted(merged, key=lambda o: o.start)

    def get_event(self, uid: str) -> Occurrence | None:
        for name, provider in self.accounts():
            try:
                occ = provider.get_event(uid)
            except Exception:  # noqa: BLE001
                continue
            if occ is not None:
                return self._tag(name, occ)
        return None

    def free_slots(
        self, start: datetime, end: datetime, duration_minutes: int
    ) -> list[tuple[datetime, datetime]]:
        clamped_start, clamped_end, _clamped = clamp_window(start, end)
        busy = self.events(clamped_start, clamped_end)
        return compute_free_slots(
            busy, start=clamped_start, end=clamped_end, duration_minutes=duration_minutes
        )


# --------------------------------------------------------------------------- fakes


class FakeCalendarProvider:
    """Loads ``tests/fixtures/mail_calendar/calendar.ics`` through the SAME parser the
    real providers use — the parser's own correctness is proven once
    (``test_calendar_ics.py``), and this fake proves nothing about parsing, only about
    ``CalendarService``'s own logic. Never imported by production."""

    def __init__(self, fixture_path: Path) -> None:
        self._events = parse_calendar(fixture_path.read_text(encoding="utf-8"))
        #: M2 (security review) — see ``CalDavCalendarProvider``'s identical fields.
        self.last_window_clamped = False
        self.last_truncated = False

    def calendars(self) -> list[str]:
        return ["fixture"]

    def _expand(self, start: datetime, end: datetime) -> list[Occurrence]:
        """The raw expand — NOT window-clamped (``get_event`` needs a wide internal
        lookup window); clamping happens in the PUBLIC ``events``/``free_slots`` below."""
        occurrences, truncated = expand_events_report(self._events, start=start, end=end)
        self.last_truncated = truncated
        return occurrences

    def events(self, start: datetime, end: datetime) -> list[Occurrence]:
        clamped_start, clamped_end, clamped = clamp_window(start, end)
        self.last_window_clamped = clamped
        return self._expand(clamped_start, clamped_end)

    def get_event(self, uid: str) -> Occurrence | None:
        from datetime import timedelta

        now = datetime.now(start_timezone())
        for occ in self._expand(now - timedelta(days=730), now + timedelta(days=730)):
            if occ.uid == uid:
                return occ
        return None

    def free_slots(
        self, start: datetime, end: datetime, duration_minutes: int
    ) -> list[tuple[datetime, datetime]]:
        clamped_start, clamped_end, clamped = clamp_window(start, end)
        self.last_window_clamped = clamped
        busy = self._expand(clamped_start, clamped_end)
        return compute_free_slots(
            busy, start=clamped_start, end=clamped_end, duration_minutes=duration_minutes
        )


@dataclass
class FakeCalendarWriter:
    """Records every create/update in-memory — reached ONLY by a confirmed
    ``calendar.commit``/REST confirm, and never by an autonomous test path."""

    created: list[ProposalInput] = field(default_factory=list)
    updated: list[tuple[str, ProposalInput]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def create(self, proposal: ProposalInput) -> str:
        event_uid = proposal.event_uid or f"fake-{uuid.uuid4()}@fixture.example"
        self.created.append(proposal)
        return event_uid

    def update(self, event_uid: str, changes: ProposalInput) -> str:
        self.updated.append((event_uid, changes))
        return event_uid

    def delete(self, event_uid: str) -> None:
        self.deleted.append(event_uid)


# --------------------------------------------------------------------------- wiring


def build_calendar_provider(settings: Any) -> CalendarProvider | None:
    """``CalDavCalendarProvider`` when a CalDAV account is configured, else
    ``IcsUrlCalendarProvider`` (read-only) when only a subscription URL is, else ``None``
    — the honest ``account_missing`` production answer (spec §2) until the owner
    configures one."""
    if settings.caldav_url:
        return CalDavCalendarProvider(
            base_url=settings.caldav_url,
            username=settings.caldav_user,
            password=settings.caldav_password,
        )
    if settings.calendar_ics_url:
        return IcsUrlCalendarProvider(url=settings.calendar_ics_url)
    return None


def build_calendar_writer(settings: Any) -> CalendarWriter | None:
    """A writer only ever pairs with CalDAV (an ICS subscription is read-only), and only
    when ``PAGENTOS_CALENDAR_WRITE_ENABLED`` is on — the same account_missing/
    send_disabled split ``app.mail.providers.build_mail_sender`` documents."""
    if not settings.caldav_url or not settings.calendar_write_enabled:
        return None
    return CalDavCalendarProvider(
        base_url=settings.caldav_url,
        username=settings.caldav_user,
        password=settings.caldav_password,
    )


__all__ = [
    "MAX_WINDOW_DAYS",
    "AccountOccurrence",
    "CalDavCalendarProvider",
    "CalendarApiError",
    "GoogleCalendarProvider",
    "GraphCalendarProvider",
    "MultiAccountCalendarProvider",
    "CalendarProvider",
    "CalendarWriter",
    "FakeCalendarProvider",
    "FakeCalendarWriter",
    "IcsUrlCalendarProvider",
    "ProposalInput",
    "build_calendar_provider",
    "build_calendar_writer",
    "compute_free_slots",
    "find_conflicts",
]
