"""The owner's approved wake song (spec §3.8: "an already approved remembered wake song").

The rule under test: the system never picks a video on the owner's behalf UNLESS the
owner has already approved one (``set_wake_song`` / ``PUT /v1/alarms/wake-song``). Once
approved, that song is what plays whenever the owner did not name something else —
whether the owner named a REMEMBERED TITLE ("seçtiğim müzik") or named NOTHING AT ALL
("Yarın 07:30'da beni uyandır."). Fixed 2026-09-08 (owner report: "the wake alarm produces
the internal beep [...] Owner-selected YouTube music is PRIMARY; the local tone is
EMERGENCY FALLBACK ONLY"): ``_media_source``'s last line used to return the tone
unconditionally whenever ``media`` was empty, never even looking at the approved song —
so a normal, otherwise-unremarkable "wake me up" alarm rang the tone forever once the
owner had set a favourite. ``media_source`` (what the owner asked for) and
``resolved_media_identity`` (what will actually play) stay distinct throughout: naming
nothing is still recorded as ``{"kind": "tone"}`` — there is no wire vocabulary for an
explicit "I want the tone" today (``MediaIn`` has no such field) — but the RESOLUTION now
honestly reflects the approved song when one exists.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.alarms import service as alarms_service
from app.alarms.tr_time import parse_when_struct
from tests.alarms_support import build_session_factory

IST = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 9, 9, 6, 0, tzinfo=IST).astimezone(UTC)
SONG = "https://www.youtube.com/watch?v=RxabLA7UQ9k"


@pytest.fixture()
def session():
    factory = build_session_factory()
    with factory() as s:
        yield s


def _when():
    return parse_when_struct({"relative_seconds": 90}, now=NOW, timezone="Europe/Istanbul")


def test_no_wake_song_until_the_owner_names_one(session):
    assert alarms_service.get_wake_song(session) is None


def test_set_then_get_round_trips_the_owner_url(session):
    stored = alarms_service.set_wake_song(session, url=SONG, title="Hans Zimmer - Time")
    assert stored == {"url": SONG, "title": "Hans Zimmer - Time"}
    assert alarms_service.get_wake_song(session) == stored


def test_only_an_http_url_is_ever_stored(session):
    with pytest.raises(alarms_service.InvalidAlarmRequest):
        alarms_service.set_wake_song(session, url="javascript:alert(1)")
    with pytest.raises(alarms_service.InvalidAlarmRequest):
        alarms_service.set_wake_song(session, url="Hans Zimmer Time")
    assert alarms_service.get_wake_song(session) is None


def test_a_remembered_title_resolves_to_the_approved_song(session):
    alarms_service.set_wake_song(session, url=SONG, title="Time")
    alarm = alarms_service.create_alarm(
        session, when=_when(), media={"remembered": "seçtiğim müzik"}, is_test=True
    )
    assert alarm.media_source == {"kind": "remembered", "name": "seçtiğim müzik"}
    assert alarm.resolved_media_identity == {"kind": "youtube", "url": SONG, "title": "Time"}


def test_a_title_without_an_approved_song_stays_unresolved(session):
    alarm = alarms_service.create_alarm(
        session, when=_when(), media={"title": "Hans Zimmer Time"}, is_test=True
    )
    assert alarm.media_source == {"kind": "remembered", "name": "Hans Zimmer Time"}
    assert alarm.resolved_media_identity is None


def test_an_explicit_url_wins_over_the_approved_song(session):
    alarms_service.set_wake_song(session, url=SONG, title="Time")
    other = "https://www.youtube.com/watch?v=other"
    alarm = alarms_service.create_alarm(session, when=_when(), media={"url": other}, is_test=True)
    assert alarm.resolved_media_identity == {"kind": "youtube", "url": other}


def test_no_media_resolves_to_the_approved_song_when_one_is_set(session):
    """The defect measured 2026-09-08: "Yarın 07:30'da beni uyandır." names no music, but
    an approved wake song already exists — the owner should not have to repeat the URL
    every day. ``media_source`` still says nothing was asked (the only vocabulary word for
    that); ``resolved_media_identity`` now honestly says what will actually play."""
    alarms_service.set_wake_song(session, url=SONG, title="Time")
    alarm = alarms_service.create_alarm(session, when=_when(), media=None, is_test=True)
    assert alarm.media_source == {"kind": "tone"}
    assert alarm.resolved_media_identity == {"kind": "youtube", "url": SONG, "title": "Time"}


def test_no_media_and_no_approved_song_stays_on_the_tone(session):
    """The mirror: with nothing named and nothing approved, the tone is exactly what it
    always was — no guess is invented in either direction."""
    alarm = alarms_service.create_alarm(session, when=_when(), media=None, is_test=True)
    assert alarm.media_source == {"kind": "tone"}
    assert alarm.resolved_media_identity is None


def test_an_explicit_empty_media_dict_also_resolves_to_the_approved_song(session):
    """``media={}`` (every field omitted) is the same "named nothing" case as
    ``media=None`` — the two must not diverge."""
    alarms_service.set_wake_song(session, url=SONG, title="Time")
    alarm = alarms_service.create_alarm(session, when=_when(), media={}, is_test=True)
    assert alarm.resolved_media_identity == {"kind": "youtube", "url": SONG, "title": "Time"}
