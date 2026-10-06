"""``CalDavCalendarProvider`` against ``httpx.MockTransport`` (docs/M21_MAIL_CALENDAR_SPEC.md
§2, §4, ADR-0084): the REPORT ``calendar-query`` body and a PUT of a proposed VEVENT.

Card radicale-caldav-live: the fake server answers REPORT with ``radicale_report.xml`` - a
207 multistatus CAPTURED from the real dev-stack Radicale - never with raw ``.ics`` text a
real CalDAV server does not send. The expected counts below come from ``calendar.ics`` (the
source the capture was PUT from) or from a regex over the capture, never from the parser
under test."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import pytest

from app.calendar.ics import expand_events_report, parse_calendar
from app.calendar.providers import (
    CalDavAuthError,
    CalDavCalendarProvider,
    CalDavConflictError,
    CalDavError,
    CalendarApiError,
    ProposalInput,
)
from tests.mail_calendar_support import CALENDAR_PATH, FIXTURES_DIR

RADICALE_REPORT_PATH = FIXTURES_DIR / "radicale_report.xml"
REPORT_XML = RADICALE_REPORT_PATH.read_bytes()
EMPTY_MULTISTATUS = (
    b"<?xml version='1.0' encoding='utf-8'?>\n"
    b'<multistatus xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"/>'
)
WINDOW = (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 15, tzinfo=UTC))
BASE = "https://caldav.example/owner/takvim"
#: The capture's one event that is not in calendar.ics: its summary needs XML escaping.
BUTCE_UID = "ev-butce@fixture.example"


def _mock_httpx(monkeypatch, handler) -> None:
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def _multistatus(body: bytes = REPORT_XML) -> httpx.Response:
    return httpx.Response(207, content=body, headers={"Content-Type": "text/xml; charset=utf-8"})


def _radicale(request: httpx.Request) -> httpx.Response:
    """The collection exists (PROPFIND 207) and REPORT answers the captured multistatus."""
    if request.method == "PROPFIND":
        return _multistatus(EMPTY_MULTISTATUS)
    if request.method == "REPORT":
        return _multistatus()
    return httpx.Response(201)


def _provider() -> CalDavCalendarProvider:
    return CalDavCalendarProvider(base_url=BASE, username="owner", password="secret")


def _proposal(uid: str | None = None) -> ProposalInput:
    return ProposalInput(
        kind="create" if uid is None else "reschedule",
        event_uid=uid,
        summary="Diş hekimi",
        start=datetime(2026, 9, 10, 15, 0, tzinfo=UTC),
        end=datetime(2026, 9, 10, 16, 0, tzinfo=UTC),
    )


def test_the_fixture_is_a_real_radicale_multistatus_not_raw_ics() -> None:
    text = RADICALE_REPORT_PATH.read_text(encoding="utf-8")
    assert text.startswith("<?xml")
    assert "Captured 2026-10-06" in text and "Radicale 3.8.1" in text
    assert "<multistatus" in text and "<C:calendar-data>" in text
    assert len(re.findall(r"<C:calendar-data>", text)) >= 3
    # The bytes really are what Radicale serves: no hand-written BEGIN:VCALENDAR before
    # the first node, a percent-encoded item href, the server's own namespace prefixes.
    assert "<href>/owner/fixture-capture/ev-doktor%40fixture.example.ics</href>" in text
    assert text.index("<multistatus") < text.index("BEGIN:VCALENDAR")


def test_events_sends_a_report_calendar_query_and_parses_the_response(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "REPORT":
            seen["body"] = request.content.decode("utf-8")
            seen["depth"] = request.headers.get("depth")
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    provider = _provider()
    # A window UNDER MAX_WINDOW_DAYS (M2, security review) that still covers every
    # fixture event (all anchored around September 2026).
    occs = provider.events(*WINDOW)
    assert "calendar-query" in str(seen["body"])
    assert "time-range" in str(seen["body"])
    assert seen["depth"] == "1"
    assert not provider.last_window_clamped
    # The source the capture was PUT from, expanded the same way: every occurrence of
    # every one of calendar.ics's 7 resources, recurring ones included, plus the one
    # escaping-case event the capture added (a single occurrence).
    expected, _ = expand_events_report(
        parse_calendar(CALENDAR_PATH.read_text(encoding="utf-8")), start=WINDOW[0], end=WINDOW[1]
    )
    assert len(occs) == len(expected) + 1
    assert {o.uid for o in occs} == {o.uid for o in expected} | {BUTCE_UID}


def test_multistatus_gives_one_event_per_calendar_data_node(monkeypatch) -> None:
    _mock_httpx(monkeypatch, _radicale)
    occs = _provider().events(*WINDOW)
    text = RADICALE_REPORT_PATH.read_text(encoding="utf-8")
    n_nodes = len(re.findall(r"<C:calendar-data>", text))
    uids_in_capture = set(re.findall(r"^UID:(\S+)$", text, flags=re.MULTILINE))
    assert n_nodes == len(uids_in_capture) == 8
    assert {o.uid for o in occs} == uids_in_capture
    assert any(o.summary == "Öğle yemeği (Zeynep)" for o in occs)
    # calendar-data is XML text: Radicale sent "Ali &amp; Ayşe &lt;bütçe&gt;", and the
    # owner must hear what he wrote, not the wire's escaping.
    (butce,) = [o for o in occs if o.uid == BUTCE_UID]
    assert butce.summary == "Ali & Ayşe <bütçe> toplantısı"
    assert "Ali &amp; Ayşe &lt;bütçe&gt; toplantısı" in text


def test_an_empty_multistatus_is_no_events(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "REPORT":
            return _multistatus(EMPTY_MULTISTATUS)
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    assert _provider().events(*WINDOW) == []


def test_an_event_this_provider_wrote_reads_back_in_the_owners_local_time(monkeypatch) -> None:
    """Found by the integration run against the real Radicale: ``create`` writes UTC
    (``DTSTART:...Z``), so "yarın saat 10'da" came back as an occurrence at 07:00 UTC and
    "Yarın ne var?" said "(07:00)". A read speaks the owner's wall clock."""
    from zoneinfo import ZoneInfo

    from app.calendar.ics import build_vevent

    istanbul = ZoneInfo("Europe/Istanbul")
    start = datetime(2026, 9, 10, 10, 0, tzinfo=istanbul)
    vevent = build_vevent(
        uid="mine@pagentos", summary="Toplantı", start=start, end=start.replace(hour=11)
    )
    assert "DTSTART:20260910T070000Z" in vevent
    report = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<multistatus xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><response>'
        "<href>/owner/takvim/mine%40pagentos.ics</href><propstat><prop><C:calendar-data>"
        f"{vevent}</C:calendar-data></prop><status>HTTP/1.1 200 OK</status></propstat>"
        "</response></multistatus>"
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "REPORT":
            return _multistatus(report)
        if request.method == "GET":
            return httpx.Response(200, text=vevent, headers={"Content-Type": "text/calendar"})
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    (occ,) = _provider().events(*WINDOW)
    assert occ.start == start
    assert occ.start.strftime("%H:%M") == "10:00"
    assert occ.end.strftime("%H:%M") == "11:00"


def test_a_200_text_calendar_answer_still_parses(monkeypatch) -> None:
    """The legacy shape (a server - or the old fake - answering REPORT with the raw
    calendar) keeps working: the provider reads the Content-Type, one function."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "REPORT":
            return httpx.Response(
                200,
                text=CALENDAR_PATH.read_text(encoding="utf-8"),
                headers={"Content-Type": "text/calendar; charset=utf-8"},
            )
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    occs = _provider().events(*WINDOW)
    assert {o.uid for o in occs} == {
        "ev-dis@fixture.example",
        "ev-ekip@fixture.example",
        "ev-bayram@fixture.example",
        "ev-musteri@fixture.example",
        "ev-doktor@fixture.example",
        "ev-spor@fixture.example",
        "ev-ogle@fixture.example",
    }


def test_a_multistatus_with_a_doctype_is_refused(monkeypatch) -> None:
    hostile = b"<?xml version='1.0'?><!DOCTYPE m [<!ENTITY a 'aaaa'>]><multistatus xmlns=\"DAV:\"/>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "REPORT":
            return _multistatus(hostile)
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(CalDavError):
        _provider().events(*WINDOW)


def test_window_wider_than_the_cap_is_clamped_and_still_parses(monkeypatch) -> None:
    """M2 (security review): a caller asking for a whole year gets the SAME REPORT
    answered, but the RESULT is clamped to ``MAX_WINDOW_DAYS`` from the window's own
    start — never an unbounded expansion, and the provider names the clamp so
    ``CalendarService`` can fold it into the receipt."""
    _mock_httpx(monkeypatch, _radicale)
    provider = _provider()
    occs = provider.events(datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
    assert provider.last_window_clamped
    # The fixture's events sit in September 2026 - past a 62-day window from Jan 1 -
    # so the clamped answer is correctly empty, never a crash or an unclamped full year.
    assert occs == []


# ------------------------------------------------------------- the collection itself


def test_a_missing_collection_is_made_once_with_mkcalendar(monkeypatch) -> None:
    calls: list[str] = []
    made: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "PROPFIND":
            return httpx.Response(404, text="The requested resource could not be found.")
        if request.method == "MKCALENDAR":
            made["url"] = str(request.url)
            made["body"] = request.content.decode("utf-8")
            return httpx.Response(201)
        if request.method == "REPORT":
            return _multistatus(EMPTY_MULTISTATUS)
        return httpx.Response(201)

    _mock_httpx(monkeypatch, handler)
    provider = _provider()
    assert provider.events(*WINDOW) == []
    assert calls == ["PROPFIND", "MKCALENDAR", "REPORT"]
    assert str(made["url"]).rstrip("/") == BASE
    assert "Takvim" in str(made["body"]) and "calendar-timezone" not in str(made["body"])
    calls.clear()
    provider.events(*WINDOW)
    provider.create(_proposal())
    assert calls == ["REPORT", "PUT"]


def test_an_existing_collection_is_not_made_again(monkeypatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    provider = _provider()
    provider.events(*WINDOW)
    provider.events(*WINDOW)
    assert calls == ["PROPFIND", "REPORT", "REPORT"]


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_login_is_an_honest_account_error(monkeypatch, status) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="Access to the requested resource forbidden.")

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(CalDavAuthError) as caught:
        _provider().events(*WINDOW)
    err = caught.value
    # The class every account read already raises (CalendarApiError), named for the
    # receipt the way account_missing is, and spoken in Turkish - never the password.
    assert isinstance(err, CalendarApiError)
    assert err.error_class == "account_invalid"
    assert err.status == status
    assert "efendim" in err.speech
    assert "secret" not in str(err) and "secret" not in err.speech


def test_a_refused_login_on_report_is_the_same_honest_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "REPORT":
            return httpx.Response(401, text="Access to the requested resource forbidden.")
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(CalDavAuthError):
        _provider().events(*WINDOW)


# ---------------------------------------------------------------------------- writes


def test_create_puts_a_vevent_and_returns_its_uid(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode("utf-8")
            seen["auth"] = request.headers.get("authorization")
            seen["if_none_match"] = request.headers.get("if-none-match")
            seen["if_match"] = request.headers.get("if-match")
            return httpx.Response(201)
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    uid = _provider().create(_proposal())
    assert uid
    assert "BEGIN:VEVENT" in str(seen["body"])
    assert "Diş hekimi" in str(seen["body"])
    assert str(seen["url"]) == f"{BASE}/{uid}.ics"
    # A NEW event never overwrites an existing resource: Radicale answers 412 instead.
    assert seen["if_none_match"] == "*"
    assert seen["if_match"] is None
    # The password must never appear anywhere a receipt/ledger row could echo it (proven
    # again at the service layer) — here, simply, it travels as Basic auth, never as body.
    assert "secret" not in str(seen["body"])


def test_a_412_on_create_is_a_uid_conflict(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(412, text="Precondition failed.")
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(CalDavConflictError) as caught:
        _provider().create(_proposal())
    assert caught.value.error_class == "uid_conflict"
    assert caught.value.status == 412


def test_update_puts_the_changed_vevent_at_the_same_uid(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode("utf-8")
            seen["if_none_match"] = request.headers.get("if-none-match")
            seen["if_match"] = request.headers.get("if-match")
            return httpx.Response(204)
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    uid = _provider().update("ev-dis@fixture.example", _proposal("ev-dis@fixture.example"))
    assert uid == "ev-dis@fixture.example"
    assert str(seen["url"]) == f"{BASE}/ev-dis@fixture.example.ics"
    # An update overwrites the owner's own event: neither precondition header.
    assert seen["if_none_match"] is None and seen["if_match"] is None


def test_an_update_after_a_read_goes_to_the_resource_the_server_named(monkeypatch) -> None:
    """An event another client (the iPhone) wrote under a name that is not ``<uid>.ics``:
    the multistatus ``href`` is where it lives, and that is where update/delete go."""
    report = REPORT_XML.replace(
        b"/owner/fixture-capture/ev-dis%40fixture.example.ics", b"/owner/takvim/ABC-123.ics"
    )
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        if request.method == "REPORT":
            return _multistatus(report)
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    provider = _provider()
    provider.events(*WINDOW)
    provider.update("ev-dis@fixture.example", _proposal("ev-dis@fixture.example"))
    provider.delete("ev-dis@fixture.example")
    assert ("PUT", f"{BASE}/ABC-123.ics") in seen
    assert ("DELETE", f"{BASE}/ABC-123.ics") in seen


def test_delete_of_a_missing_event_is_silent(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(404, text="The requested resource could not be found.")
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    assert _provider().delete("gone@pagentos") is None


# ------------------------------------------------------------------------- get_event


def test_get_event_reads_the_item_by_its_name_without_a_report(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    one = CALENDAR_PATH.read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(
                200, text=one, headers={"Content-Type": "text/calendar; charset=utf-8"}
            )
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    # A uid whose event sits inside the provider's own +-365-day lookup from today.
    occ = _provider().get_event("ev-dis@fixture.example")
    assert ("GET", f"{BASE}/ev-dis@fixture.example.ics") in calls
    assert "REPORT" not in [m for m, _ in calls]
    assert occ is not None and occ.uid == "ev-dis@fixture.example"


def test_get_event_falls_back_to_report_when_the_name_is_unknown(monkeypatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(404, text="The requested resource could not be found.")
        return _radicale(request)

    _mock_httpx(monkeypatch, handler)
    occ = _provider().get_event("ev-ogle@fixture.example")
    assert calls[-2:] == ["GET", "REPORT"]
    assert occ is not None and occ.summary == "Öğle yemeği (Zeynep)"
