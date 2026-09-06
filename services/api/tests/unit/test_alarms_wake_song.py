"""The owner's approved wake song (spec §3.8: "an already approved remembered wake song").

The rule under test: the system never picks a video on the owner's behalf. A remembered
title resolves to a playable item ONLY when the owner has named a URL once
(``set_wake_song`` / ``PUT /v1/alarms/wake-song``); without it the alarm still exists, on
the tone, and the source records what was asked for.
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


def test_no_media_means_the_tone_even_with_an_approved_song(session):
    # "90 saniye sonra test alarmı kur." names no music: the tone, never a guess.
    alarms_service.set_wake_song(session, url=SONG, title="Time")
    alarm = alarms_service.create_alarm(session, when=_when(), media=None, is_test=True)
    assert alarm.media_source == {"kind": "tone"}
    assert alarm.resolved_media_identity is None
