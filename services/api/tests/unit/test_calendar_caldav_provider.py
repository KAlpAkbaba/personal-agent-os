"""``CalDavCalendarProvider`` against ``httpx.MockTransport`` (docs/M21_MAIL_CALENDAR_SPEC.md
§2, §4, ADR-0084): the REPORT ``calendar-query`` body and a PUT of a proposed VEVENT."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.calendar.ics import parse_calendar
from app.calendar.providers import CalDavCalendarProvider, ProposalInput
from tests.mail_calendar_support import CALENDAR_PATH


def _mock_httpx(monkeypatch, handler) -> None:
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def test_events_sends_a_report_calendar_query_and_parses_the_response(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = request.content.decode("utf-8")
        seen["depth"] = request.headers.get("depth")
        return httpx.Response(200, text=CALENDAR_PATH.read_text(encoding="utf-8"))

    _mock_httpx(monkeypatch, handler)
    provider = CalDavCalendarProvider(
        base_url="https://caldav.example/calendars/owner", username="owner", password="secret"
    )
    # A window UNDER MAX_WINDOW_DAYS (M2, security review) that still covers every
    # fixture event (all anchored around September 2026) — a full CALENDAR YEAR window
    # would now be clamped (the spec §2 bound this test must not itself violate), which
    # is exactly what `test_window_wider_than_the_cap_is_clamped_and_still_parses` below
    # proves on purpose; this test's own job is only "REPORT sent, response parsed".
    occs = provider.events(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 15, tzinfo=UTC))
    assert seen["method"] == "REPORT"
    assert "calendar-query" in str(seen["body"])
    assert "time-range" in str(seen["body"])
    assert seen["depth"] == "1"
    assert not provider.last_window_clamped
    assert len(occs) == len(parse_calendar(CALENDAR_PATH.read_text(encoding="utf-8"))) or len(
        occs
    ) >= 7


def test_window_wider_than_the_cap_is_clamped_and_still_parses(monkeypatch) -> None:
    """M2 (security review): a caller asking for a whole year gets the SAME REPORT
    answered, but the RESULT is clamped to ``MAX_WINDOW_DAYS`` from the window's own
    start — never an unbounded expansion, and the provider names the clamp so
    ``CalendarService`` can fold it into the receipt."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=CALENDAR_PATH.read_text(encoding="utf-8"))

    _mock_httpx(monkeypatch, handler)
    provider = CalDavCalendarProvider(
        base_url="https://caldav.example/calendars/owner", username="owner", password="secret"
    )
    occs = provider.events(datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
    assert provider.last_window_clamped
    # The fixture's events sit in September 2026 - past a 62-day window from Jan 1 -
    # so the clamped answer is correctly empty, never a crash or an unclamped full year.
    assert occs == []


def test_create_puts_a_vevent_and_returns_its_uid(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode("utf-8")
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(201)
        return httpx.Response(200, text="")

    _mock_httpx(monkeypatch, handler)
    provider = CalDavCalendarProvider(
        base_url="https://caldav.example/calendars/owner", username="owner", password="secret"
    )
    proposal = ProposalInput(
        kind="create",
        event_uid=None,
        summary="Diş hekimi",
        start=datetime(2026, 9, 10, 15, 0, tzinfo=UTC),
        end=datetime(2026, 9, 10, 16, 0, tzinfo=UTC),
    )
    uid = provider.create(proposal)
    assert uid
    assert "BEGIN:VEVENT" in str(seen["body"])
    assert "Diş hekimi" in str(seen["body"]) or "Di\\ hekimi" in str(seen["body"])
    assert uid in str(seen["url"])
    # The password must never appear anywhere a receipt/ledger row could echo it (proven
    # again at the service layer) — here, simply, it travels as Basic auth, never as body.
    assert "secret" not in str(seen["body"])


def test_update_puts_the_changed_vevent_at_the_same_uid(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode("utf-8")
            return httpx.Response(204)
        return httpx.Response(200, text="")

    _mock_httpx(monkeypatch, handler)
    provider = CalDavCalendarProvider(
        base_url="https://caldav.example/calendars/owner", username="owner", password="secret"
    )
    changes = ProposalInput(
        kind="reschedule",
        event_uid="ev-dis@fixture.example",
        summary="Diş hekimi",
        start=datetime(2026, 9, 10, 16, 0, tzinfo=UTC),
        end=datetime(2026, 9, 10, 17, 0, tzinfo=UTC),
    )
    uid = provider.update("ev-dis@fixture.example", changes)
    assert uid == "ev-dis@fixture.example"
    assert "ev-dis@fixture.example" in str(seen["url"])
