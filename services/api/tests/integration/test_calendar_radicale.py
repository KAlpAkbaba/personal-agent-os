"""``CalDavCalendarProvider`` against the REAL dev-stack Radicale (card radicale-caldav-live,
docs/M21_MAIL_CALENDAR_SPEC.md §2).

The contract (card radicale-stack-ops): Radicale user ``owner``, collection ``takvim``; the
dev stack serves it on ``http://127.0.0.1:15232/owner/takvim/`` with the fixed dev password
``dev-takvim``. ``PAGENTOS_TEST_RADICALE_URL`` (and ``_USER``/``_PASSWORD``) override it.

A Radicale that is not up FAILS these tests - it never skips them: a skipped run is not
evidence (``docker compose ... up -d radicale --wait`` first). Every test writes under its own
uid prefix and removes what it wrote, so the shared collection stays clean; a scratch
collection made to prove the MKCALENDAR path is deleted again.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx
import pytest

from app.calendar.providers import (
    CalDavAuthError,
    CalDavCalendarProvider,
    CalDavConflictError,
    ProposalInput,
    build_calendar_provider,
    build_calendar_writer,
)
from app.calendar.service import CalendarService
from app.config import Settings

pytestmark = pytest.mark.integration

RADICALE_URL = os.environ.get("PAGENTOS_TEST_RADICALE_URL", "http://127.0.0.1:15232/owner/takvim/")
RADICALE_USER = os.environ.get("PAGENTOS_TEST_RADICALE_USER", "owner")
RADICALE_PASSWORD = os.environ.get("PAGENTOS_TEST_RADICALE_PASSWORD", "dev-takvim")
AUTH = (RADICALE_USER, RADICALE_PASSWORD)
#: A day no real dev-stack data sits on, far from "today", so a free-slot answer is exact.
DAY = datetime(2030, 3, 4, tzinfo=UTC)


def _sibling(name: str) -> str:
    """``http://host/owner/<name>/`` next to the contract collection."""
    parts = urlsplit(RADICALE_URL)
    parent = parts.path.rstrip("/").rsplit("/", 1)[0]
    return urlunsplit((parts.scheme, parts.netloc, f"{parent}/{name}/", "", ""))


def _propfind(url: str, depth: str = "0") -> httpx.Response:
    return httpx.request("PROPFIND", url, auth=AUTH, headers={"Depth": depth}, timeout=10)


def _hrefs(url: str) -> list[str]:
    """Every member href of a collection (Depth 1), the collection itself excluded."""
    response = _propfind(url, depth="1")
    if response.status_code == 404:
        return []
    assert response.status_code == 207, response.text
    root = ElementTree.fromstring(response.content)
    own = urlsplit(url).path.rstrip("/") + "/"
    return [
        h.text
        for h in root.iter("{DAV:}href")
        if h.text and h.text.rstrip("/") + "/" != own and not h.text.endswith("/")
    ]


@pytest.fixture(scope="module", autouse=True)
def radicale_is_up() -> None:
    try:
        response = httpx.get(_sibling("").rstrip("/").rsplit("/", 1)[0] + "/.web/", timeout=5)
    except httpx.HTTPError as exc:
        pytest.fail(
            f"Radicale is not reachable behind {RADICALE_URL} ({type(exc).__name__}); start the "
            "dev stack's radicale service - a skipped run proves nothing"
        )
    assert response.status_code < 500, response.text


@pytest.fixture
def prefix() -> str:
    return f"it-radicale-{uuid.uuid4().hex[:10]}-"


@pytest.fixture
def provider(prefix: str):
    p = CalDavCalendarProvider(
        base_url=RADICALE_URL, username=RADICALE_USER, password=RADICALE_PASSWORD
    )
    yield p
    # Whatever this test wrote under its prefix goes, even when an assertion failed.
    for href in _hrefs(RADICALE_URL):
        if prefix in href:
            httpx.delete(str(httpx.URL(RADICALE_URL).join(href)), auth=AUTH, timeout=10)
    assert not [h for h in _hrefs(RADICALE_URL) if prefix in h]


def _proposal(uid: str, summary: str, start: datetime, minutes: int = 60) -> ProposalInput:
    return ProposalInput(
        kind="create",
        event_uid=uid,
        summary=summary,
        start=start,
        end=start + timedelta(minutes=minutes),
    )


def test_a_missing_collection_is_made_by_the_provider() -> None:
    """The stack-ops inspector found /owner/takvim/ PROPFIND 404 on a fresh dev volume: the
    provider - not a setup script - makes it with MKCALENDAR on its first call."""
    scratch = _sibling(f"takvim-it-{uuid.uuid4().hex[:10]}")
    assert _propfind(scratch).status_code == 404
    try:
        p = CalDavCalendarProvider(
            base_url=scratch, username=RADICALE_USER, password=RADICALE_PASSWORD
        )
        assert p.events(DAY, DAY + timedelta(days=1)) == []
        made = _propfind(scratch)
        assert made.status_code == 207, made.text
        assert b"calendar" in made.content and b"Takvim" in made.content
        # Made once: a second provider on the same address finds it and writes into it.
        again = CalDavCalendarProvider(
            base_url=scratch, username=RADICALE_USER, password=RADICALE_PASSWORD
        )
        uid = again.create(_proposal(f"it-scratch-{uuid.uuid4().hex[:8]}", "Deneme", DAY))
        assert [o.uid for o in again.events(DAY, DAY + timedelta(days=1))] == [uid]
    finally:
        httpx.delete(scratch, auth=AUTH, timeout=10)
    assert _propfind(scratch).status_code == 404


def test_the_contract_collection_exists_after_the_provider_has_run(provider) -> None:
    provider.ensure_collection()
    assert _propfind(RADICALE_URL).status_code == 207


def test_create_read_update_free_slots_delete(provider, prefix) -> None:
    uid = f"{prefix}toplanti@pagentos"
    start = DAY.replace(hour=10)
    assert provider.create(_proposal(uid, "Ali & Ayşe toplantısı", start)) == uid
    # Radicale names the item <uid>.ics - the name get_event reads it back by.
    item = httpx.get(f"{RADICALE_URL.rstrip('/')}/{uid}.ics", auth=AUTH, timeout=10)
    assert item.status_code == 200 and f"UID:{uid}" in item.text

    occs = [o for o in provider.events(DAY, DAY + timedelta(days=1)) if o.uid == uid]
    assert [(o.summary, o.start) for o in occs] == [("Ali & Ayşe toplantısı", start)]

    moved = start + timedelta(hours=1)
    provider.update(uid, _proposal(uid, "Bütçe toplantısı", moved))
    occs = [o for o in provider.events(DAY, DAY + timedelta(days=1)) if o.uid == uid]
    assert [(o.summary, o.start) for o in occs] == [("Bütçe toplantısı", moved)]
    got = provider.get_event(uid)
    # get_event looks a year either side of today; 2030 is past that, so None is right -
    # and it must not be a crash on the GET path.
    assert got is None or got.uid == uid

    slots = provider.free_slots(DAY.replace(hour=8), DAY.replace(hour=14), 60)
    assert slots == [
        (DAY.replace(hour=8), DAY.replace(hour=11)),
        (DAY.replace(hour=12), DAY.replace(hour=14)),
    ]

    provider.delete(uid)
    assert [o for o in provider.events(DAY, DAY + timedelta(days=1)) if o.uid == uid] == []
    provider.delete(uid)  # already gone: silent


def test_a_second_create_of_the_same_uid_is_a_conflict(provider, prefix) -> None:
    uid = f"{prefix}cift@pagentos"
    provider.create(_proposal(uid, "İlk", DAY.replace(hour=9)))
    with pytest.raises(CalDavConflictError):
        provider.create(_proposal(uid, "İkinci", DAY.replace(hour=9)))
    (occ,) = [o for o in provider.events(DAY, DAY + timedelta(days=1)) if o.uid == uid]
    assert occ.summary == "İlk"


def test_a_wrong_password_is_an_honest_account_error() -> None:
    wrong = CalDavCalendarProvider(
        base_url=RADICALE_URL, username=RADICALE_USER, password="not-the-password"
    )
    with pytest.raises(CalDavAuthError) as caught:
        wrong.events(DAY, DAY + timedelta(days=1))
    assert caught.value.error_class == "account_invalid"


def test_a_spoken_meeting_lands_in_radicale_through_the_application() -> None:
    """PROVEN_PROXY (card acceptance 6): the real application object (create_app, the real
    relay and router - tests/voice_corpus/harness.py) with the calendar service the
    production wiring builds from PAGENTOS_CALDAV_* settings. "Yarın saat 10'da toplantı
    ekle." -> proposal -> committed (the owner's standing decision 2026-09-19: no second
    confirmation; a later "Onayla." finds nothing pending) -> the item is in Radicale ->
    "Yarın ne var?" names it."""
    from tests.voice_corpus.harness import build_harness

    settings = Settings(
        _env_file=None,
        caldav_url=RADICALE_URL,
        caldav_user=RADICALE_USER,
        caldav_password=RADICALE_PASSWORD,
        calendar_write_enabled=True,
    )
    service = CalendarService(build_calendar_provider(settings), build_calendar_writer(settings))
    summary = f"Toplantı {uuid.uuid4().hex[:6]}"
    event_uid: str | None = None
    with build_harness() as h:
        h.runtime.register_live(calendar_service=service)
        h.calendar = service
        try:
            sid = h.new_session()
            h.say(sid, "Yarın saat 10'da toplantı ekle.")
            done = h.tool(
                sid,
                "c-1",
                "calendar.propose",
                {"when_spoken": "Yarın saat 10'da", "summary": summary},
            )
            assert done["status"] == "succeeded", done
            result = done["result"]
            assert result["capability"] == "calendar.commit", result
            assert result["execution_status"] == "executed", result
            event_uid = result["proposal"]["committed_event_uid"]
            assert event_uid

            item = httpx.get(f"{RADICALE_URL.rstrip('/')}/{event_uid}.ics", auth=AUTH, timeout=10)
            assert item.status_code == 200, item.text
            assert summary in item.text

            h.say(sid, "Onayla.", turn=2)
            again = h.tool(sid, "c-2", "calendar.commit", {})
            assert again["result"].get("execution_status") != "executed", again

            h.say(sid, "Yarın ne var?", turn=3)
            agenda = h.tool(sid, "c-3", "calendar.agenda", {"when_spoken": "yarın"})
            assert agenda["status"] == "succeeded", agenda
            assert summary in agenda["result"]["speech"], agenda["result"]
            assert "10:00" in agenda["result"]["speech"], agenda["result"]
        finally:
            if event_uid:
                httpx.delete(f"{RADICALE_URL.rstrip('/')}/{event_uid}.ics", auth=AUTH, timeout=10)
    assert not [x for x in _hrefs(RADICALE_URL) if event_uid and event_uid in x]
