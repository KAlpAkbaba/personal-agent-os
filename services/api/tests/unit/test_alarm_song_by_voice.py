"""A song PER ALARM, set by voice in the same sentence (the owner, 2026-10-05: "alarmda
istediğim müzikle beni uyandıracak ya da istediğim müziği açacak").

Four layers, each tested where it lives:

* the alarm row carries its own song (``WakeAlarm.song``), set at creation or later;
* the wake sequence plays that song first, the owner's global wake song second, the
  device's tone last - every failed attempt a receipt and a named reason;
* the router reads the song out of the SAME sentence that sets the time ("Yarın 7'de beni
  Tarkan'ın Şımarık'ıyla uyandır.") into ``media_query``, with anchored patterns, and
  leaves a bare "X çal" with media.play and "Alarmı kapat." with alarm.stop;
* the voice tools find the song the way media.play does (the device's own search, the
  first real watch URL) and read it back in one short sentence.

The last block (``test_wiring_*``) needs files outside this task's area
(``tools_ambient.py``, ``step_up.py``, ``tr_time.py``) and is RED on purpose until the lead
grants them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.alarms import service as alarms_service
from app.alarms.models import (
    PLAYED_KIND_TONE_FALLBACK,
    PLAYED_KIND_YOUTUBE,
    STATE_PLAYING,
)
from app.alarms.sequence import WakeSequence
from app.alarms.tr_time import parse_when_struct
from app.ledger.models import ActivityEventRow
from app.voice.intents import Intent, alarm_song_set_match, resolve_intent
from app.voice.realtime_sessions import tools_alarms
from app.voice.realtime_sessions.tools import ToolContext
from tests.alarms_support import (
    FakeDeviceAction,
    build_session_factory,
    failed,
    happy_device_results,
    ok,
)
from tests.unit.test_alarms_routes import (  # noqa: F401 - pytest fixtures, reused as-is
    _fresh_uistate_publisher,
    app_and_client,
    artifacts_runtime,
    client,
    engine,
)

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
FIRED_AT = NOW + timedelta(seconds=30)
ALARM_SONG = "https://www.youtube.com/watch?v=SimarikTarka"
WAKE_SONG = "https://www.youtube.com/watch?v=GlobalWake01"
SEARCH_HIT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture()
def session():
    with build_session_factory()() as s:
        yield s


@pytest.fixture()
def device():
    return FakeDeviceAction(results=happy_device_results())


def _alarm(session, **kwargs):
    return alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 30}, now=NOW), **kwargs
    )


def _fire(session, device, alarm):
    return WakeSequence(device_action=device).fire(
        session,
        alarm,
        firing_id=uuid.uuid4(),
        now=FIRED_AT,
        transition=alarms_service.transition,
    )


def _played_urls(device) -> list[str]:
    return [c["payload"]["url"] for c in device.calls if c["capability"] == "browser.media_play"]


def _play_only(*urls: str):
    """``browser.media_play`` verifies only for these urls; every other one is refused by
    the page (a consent wall) - the read-back a real failed song gives."""

    def answer(payload):
        if payload["url"] in urls:
            return ok(playing=True, verified=True, current_time_s=1.0)
        return ok(playing=False, verified=False, reason="consent_wall")

    return answer


def _receipts(session, device_capability: str) -> list[ActivityEventRow]:
    """The receipts of one DEVICE call (the open and the play both file as media.play)."""

    def called(row: ActivityEventRow) -> str:
        server = ((row.detail_json or {}).get("observed_after") or {}).get("server") or {}
        return str(server.get("device_capability") or "")

    return [
        r
        for r in session.query(ActivityEventRow).all()
        if r.event_type == "action.receipt" and called(r) == device_capability
    ]


# ------------------------------------------------------------------ the alarm's own song


def test_an_alarm_created_with_a_song_carries_it(session):
    alarm = _alarm(session, song={"url": ALARM_SONG, "title": "Tarkan - Şımarık"})
    assert alarm.song == {"url": ALARM_SONG, "title": "Tarkan - Şımarık"}
    assert alarms_service.alarm_dict(alarm)["song"] == {
        "url": ALARM_SONG,
        "title": "Tarkan - Şımarık",
    }


def test_a_song_can_be_set_changed_and_cleared_on_an_existing_alarm(session):
    alarm = _alarm(session)
    assert alarms_service.alarm_dict(alarm)["song"] is None
    alarms_service.set_alarm_song(session, alarm.id, url=ALARM_SONG, title="Şımarık")
    assert alarms_service.require_alarm(session, alarm.id).song == {
        "url": ALARM_SONG,
        "title": "Şımarık",
    }
    alarms_service.set_alarm_song(session, alarm.id, url=None)
    assert alarms_service.require_alarm(session, alarm.id).song is None


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "Tarkan Şımarık", "ftp://x/y"])
def test_only_an_http_url_is_ever_an_alarm_song(session, bad):
    alarm = _alarm(session)
    with pytest.raises(alarms_service.InvalidAlarmRequest):
        alarms_service.set_alarm_song(session, alarm.id, url=bad)
    with pytest.raises(alarms_service.InvalidAlarmRequest):
        _alarm(session, song={"url": bad})


def test_a_finished_alarm_takes_no_song(session):
    alarm = _alarm(session)
    alarms_service.cancel_alarm(session, alarm.id)
    with pytest.raises(alarms_service.InvalidAlarmRequest):
        alarms_service.set_alarm_song(session, alarm.id, url=ALARM_SONG)


def test_the_alarms_page_sets_and_clears_one_alarms_song(client):  # noqa: F811 - the fixture
    created = client.post("/v1/alarms", json={"when_text": "Yarın sabah 07:30'da beni uyandır."})
    assert created.status_code == 201, created.text
    alarm_id = created.json()["alarm_id"]
    assert created.json()["song"] is None

    put = client.put(f"/v1/alarms/{alarm_id}/song", json={"url": ALARM_SONG, "title": "Şımarık"})
    assert put.status_code == 200, put.text
    assert put.json()["song"] == {"url": ALARM_SONG, "title": "Şımarık"}
    assert client.get(f"/v1/alarms/{alarm_id}").json()["song"]["url"] == ALARM_SONG

    assert (
        client.put(f"/v1/alarms/{alarm_id}/song", json={"url": "javascript:x()"}).status_code == 422
    )
    assert (
        client.put(f"/v1/alarms/{uuid.uuid4()}/song", json={"url": ALARM_SONG}).status_code == 404
    )

    cleared = client.put(f"/v1/alarms/{alarm_id}/song", json={"url": None})
    assert cleared.status_code == 200 and cleared.json()["song"] is None


# --------------------------------------------------------------- the fallback chain


def test_the_alarm_song_wins_over_the_global_wake_song(session, device):
    alarms_service.set_wake_song(session, url=WAKE_SONG, title="Genel")
    alarm = _alarm(session, song={"url": ALARM_SONG, "title": "Şımarık"})
    result = _fire(session, device, alarm)
    assert result.state == STATE_PLAYING and result.media_kind == PLAYED_KIND_YOUTUBE
    assert _played_urls(device) == [ALARM_SONG]
    assert alarm.detail_json["media_played"] == {
        "source": "alarm_song",
        "url": ALARM_SONG,
        "title": "Şımarık",
    }
    assert device.count("desktop.alarm_start") == 0


def test_a_failed_alarm_song_falls_back_to_the_global_wake_song_with_a_receipt(session, device):
    alarms_service.set_wake_song(session, url=WAKE_SONG, title="Genel")
    alarm = _alarm(session, song={"url": ALARM_SONG, "title": "Şımarık"})
    device.results["browser.media_play"] = _play_only(WAKE_SONG)
    result = _fire(session, device, alarm)
    assert result.media_kind == PLAYED_KIND_YOUTUBE
    assert _played_urls(device) == [ALARM_SONG, WAKE_SONG]
    assert alarm.detail_json["media_played"]["source"] == "wake_song"
    # The failed attempt is a receipt of its own, and the reason is readable on the row.
    assert len(_receipts(session, "browser.media_play")) == 2
    assert "consent_wall" in alarm.detail_json["media_failure_reason"]
    assert device.count("desktop.alarm_start") == 0


def test_both_songs_failing_ring_the_tone_and_say_why(session, device):
    alarms_service.set_wake_song(session, url=WAKE_SONG, title="Genel")
    alarm = _alarm(session, song={"url": ALARM_SONG, "title": "Şımarık"})
    device.results["browser.media_play"] = _play_only()
    result = _fire(session, device, alarm)
    assert result.media_kind == PLAYED_KIND_TONE_FALLBACK
    assert _played_urls(device) == [ALARM_SONG, WAKE_SONG]
    assert len(_receipts(session, "browser.media_play")) == 2
    assert device.count("desktop.alarm_start") == 1
    assert alarm.detail_json["media_failure_reason"]
    assert "media_played" not in alarm.detail_json


def test_an_alarm_without_its_own_song_plays_the_global_one_as_before(session, device):
    alarms_service.set_wake_song(session, url=WAKE_SONG, title="Genel")
    alarm = _alarm(session)
    _fire(session, device, alarm)
    assert _played_urls(device) == [WAKE_SONG]
    assert alarm.detail_json["media_played"]["source"] == "wake_song"


def test_the_global_song_is_read_when_the_alarm_rings_not_when_it_was_set(session, device):
    """The owner changes the wake song tonight; tomorrow's alarm, set yesterday, plays the
    new one - the stored snapshot is only the last resort."""
    alarms_service.set_wake_song(
        session, url="https://www.youtube.com/watch?v=OldWakeSong", title=""
    )
    alarm = _alarm(session)
    alarms_service.set_wake_song(session, url=WAKE_SONG, title="Yeni")
    device.results["browser.media_play"] = _play_only()
    _fire(session, device, alarm)
    assert _played_urls(device) == [WAKE_SONG, "https://www.youtube.com/watch?v=OldWakeSong"]


def test_the_same_url_is_never_tried_twice(session, device):
    alarms_service.set_wake_song(session, url=ALARM_SONG, title="Şımarık")
    alarm = _alarm(session, song={"url": ALARM_SONG, "title": "Şımarık"})
    device.results["browser.media_play"] = _play_only()
    _fire(session, device, alarm)
    assert _played_urls(device) == [ALARM_SONG]


def test_a_browser_that_will_not_open_goes_straight_to_the_tone(session, device):
    alarms_service.set_wake_song(session, url=WAKE_SONG, title="Genel")
    alarm = _alarm(session, song={"url": ALARM_SONG, "title": "Şımarık"})
    device.results["browser.session_open"] = failed("capability_missing")
    result = _fire(session, device, alarm)
    assert result.media_kind == PLAYED_KIND_TONE_FALLBACK
    assert device.count("browser.media_play") == 0
    assert device.count("browser.session_open") == 1


# ------------------------------------------------------------------- the router

#: (sentence, the song the owner named). Every one sets the time AND the song.
SONG_FORMS = [
    ("Yarın 7:30'da beni Tarkan'ın Şımarık'ıyla uyandır.", "Tarkan'ın Şımarık"),
    ("Pazartesi 6.30'da Sezen Aksu çalarak uyandır.", "Sezen Aksu"),
    ("Yarın sabah yedi buçukta beni Bella Ciao ile uyandır.", "Bella Ciao"),
    ("Yarın 07:30'da beni Hans Zimmer Time şarkısıyla uyandır.", "Hans Zimmer Time"),
    (
        "Yarın sabah 8'de Barış Manço'nun Gülpembe şarkısını çalarak beni uyandır.",
        "Barış Manço'nun Gülpembe",
    ),
    # The recogniser dropped the apostrophe; a capitalised name keeps its suffix readable.
    ("yarın 07.30'da beni Şımarıkıyla uyandır", "Şımarık"),
]


@pytest.mark.parametrize(("text", "song"), SONG_FORMS)
def test_the_alarm_sentence_carries_the_song_it_names(text, song):
    resolved = resolve_intent(text)
    assert resolved.intent is Intent.ALARM_CREATE
    assert resolved.media_query == song


@pytest.mark.parametrize(
    "text",
    [
        "Yarın 7:30'da beni uyandır.",
        # No apostrophe and no "ile": "sevgiyle" is a manner, not a title.
        "Yarın 7:30'da beni sevgiyle uyandır.",
        "Yarın 7:30'da beni nazikçe uyandır.",
        "Her hafta içi 07:15'te beni uyandır.",
    ],
)
def test_an_alarm_sentence_that_names_no_song_carries_none(text):
    resolved = resolve_intent(text)
    assert resolved.intent is Intent.ALARM_CREATE
    assert resolved.media_query is None


def test_a_bare_play_request_stays_media_play():
    resolved = resolve_intent("Tarkan Şımarık çal.")
    assert resolved.intent is Intent.MEDIA_PLAY


@pytest.mark.parametrize("text", ["Alarmı kapat.", "Alarmı iptal et.", "Alarmı sustur."])
def test_closing_an_alarm_never_sets_a_song(text):
    resolved = resolve_intent(text)
    assert resolved.intent in (Intent.ALARM_STOP, Intent.ALARM_CANCEL)
    assert resolved.media_query is None


@pytest.mark.parametrize(
    ("text", "song"),
    [
        ("Alarmımın şarkısını Tarkan Şımarık yap.", "Tarkan Şımarık"),
        ("Uyandırma şarkımı Sezen Aksu Gülümse yap.", "Sezen Aksu Gülümse"),
        ("Alarm müziğimi Bella Ciao yap.", "Bella Ciao"),
        ("Uyandırma şarkımı değiştir.", None),
        # "bu" points at a song, it does not name one: the tool asks, never searches "bu".
        ("Alarm müziğim bu olsun.", None),
        ("Bu şarkıyı alarm müziğim yap.", None),
    ],
)
def test_changing_the_wake_song_by_voice(text, song):
    matched = alarm_song_set_match(text)
    assert matched is not None and matched[1] == song


@pytest.mark.parametrize(
    "text",
    [
        "Alarm müziğim ne?",
        "Alarmımın şarkısı hangisi?",
        "Alarmı kapat.",
        "Tarkan Şımarık çal.",
        "Yarın 7:30'da beni Tarkan'ın Şımarık'ıyla uyandır.",
        "Şarkımı değiştir.",
    ],
)
def test_what_is_not_a_wake_song_change(text):
    assert alarm_song_set_match(text) is None


def test_the_song_set_reads_its_scope_from_the_sentence():
    assert tools_alarms.song_scope("Alarmımın şarkısını Tarkan Şımarık yap.") == "alarm"
    assert tools_alarms.song_scope("Uyandırma şarkımı Sezen Aksu Gülümse yap.") == "wake_song"


# ------------------------------------------------------------------- the voice tools


def _ctx(session, device, *, intent: str, media_query: str | None) -> ToolContext:
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="web",
        context={"last_utterance": {"intent": intent, "media_query": media_query}},
        db=session,
        now=NOW,
        call_id="call-song",
        live={"device_action": device},
    )


def test_a_song_is_found_the_way_media_play_finds_one(session, device):
    found = tools_alarms.find_song(device, "Tarkan'ın Şımarık")
    assert found.ok
    assert found.song == {"url": SEARCH_HIT, "title": "Dogum Gunun Kutlu Olsun"}
    search = device.payload_for("browser.search")
    assert search["query"] == "Tarkan'ın Şımarık youtube"
    # The search session is closed again: a set alarm leaves no tab behind.
    assert device.count("browser.session_close") == 1


def test_a_search_with_no_video_finds_nothing_and_says_so(session, device):
    device.results["browser.search"] = ok(results=[{"url": "https://example.com/x"}])
    found = tools_alarms.find_song(device, "Şımarık")
    assert not found.ok and found.error_class == "no_video_found"


def test_song_for_create_takes_the_owners_words_over_the_models(session, device):
    ctx = _ctx(session, device, intent="alarm_create", media_query="Tarkan'ın Şımarık")
    media = tools_alarms.song_for_create(ctx, {"media": {"title": "something else"}})
    assert media == {"url": SEARCH_HIT, "title": "Dogum Gunun Kutlu Olsun"}
    assert device.payload_for("browser.search")["query"] == "Tarkan'ın Şımarık youtube"


def test_song_for_create_leaves_an_explicit_url_alone(session, device):
    ctx = _ctx(session, device, intent="alarm_create", media_query="Şımarık")
    assert tools_alarms.song_for_create(ctx, {"media": {"url": ALARM_SONG}}) is None
    assert device.count("browser.search") == 0


def test_the_created_speech_reads_the_song_back_in_one_sentence():
    speech = tools_alarms.song_created_speech(
        local_time="07:00", tomorrow=True, weekdays=(), title="Şımarık"
    )
    assert speech.startswith("Yarın ")
    assert "Şımarık ile uyandıracağım" in speech
    assert speech.count(".") == 1


def test_alarm_set_song_sets_the_next_alarm_and_reads_it_back(session, device):
    alarm = _alarm(session)
    ctx = _ctx(session, device, intent="alarm_song_set", media_query="Tarkan Şımarık")
    receipt = tools_alarms.alarm_set_song(ctx, {})
    assert receipt["execution_status"] == "executed"
    assert alarms_service.require_alarm(session, alarm.id).song == {
        "url": SEARCH_HIT,
        "title": "Dogum Gunun Kutlu Olsun",
    }
    assert "Dogum Gunun Kutlu Olsun" in receipt["speech"]


def test_alarm_set_song_for_the_wake_song_changes_the_global_one(session, device):
    # The session's turn record carries no sentence ("turn" is the turn's number): the scope
    # travels as the intent the router chose.
    ctx = _ctx(session, device, intent="wake_song_set", media_query="Sezen Aksu Gülümse")
    receipt = tools_alarms.alarm_set_song(ctx, {})
    assert receipt["execution_status"] == "executed"
    assert alarms_service.get_wake_song(session) == {
        "url": SEARCH_HIT,
        "title": "Dogum Gunun Kutlu Olsun",
    }


def test_alarm_set_song_with_no_title_asks_which_song_and_changes_nothing(session, device):
    alarm = _alarm(session)
    ctx = _ctx(session, device, intent="alarm_song_set", media_query=None)
    receipt = tools_alarms.alarm_set_song(ctx, {})
    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == "no_song_named"
    assert "Hangi şarkı" in receipt["speech"]
    assert device.count("browser.search") == 0
    assert alarms_service.require_alarm(session, alarm.id).song is None


def test_alarm_set_song_with_no_alarm_says_so(session, device):
    ctx = _ctx(session, device, intent="alarm_song_set", media_query="Şımarık")
    receipt = tools_alarms.alarm_set_song(ctx, {})
    assert receipt["error_class"] == "no_alarm"


# ------------------------------------------- wiring: needs files outside this area (RED)


def test_wiring_the_set_song_tool_is_registered_and_tiered():
    """Needs ``tools_ambient.register_ambient_tools`` to call
    ``tools_alarms.register_alarm_song_tools`` and a tier in ``app/security/step_up.py``."""
    from app.security.step_up import _TIERS
    from app.voice.realtime_sessions.tools import default_registry

    assert "alarm.set_song" in set(default_registry().names())
    assert "alarm.set_song" in _TIERS


def test_wiring_the_router_sends_a_wake_song_change_to_the_tool():
    """The intent exists only once ``alarm.set_song`` is registered (the intent-tool guard
    and the corpus both execute it), so the enum, the capability row and the hook-up land
    with the wiring."""
    resolved = resolve_intent("Alarmımın şarkısını Tarkan Şımarık yap.")
    assert resolved.intent.value == "alarm_song_set"
    assert resolved.media_query == "Tarkan Şımarık"


def test_the_router_sends_a_global_wake_song_change_as_its_own_intent():
    resolved = resolve_intent("Uyandırma şarkımı Sezen Aksu Gülümse yap.")
    assert resolved.intent.value == "wake_song_set"
    assert resolved.media_query == "Sezen Aksu Gülümse"


def test_wiring_alarm_create_resolves_the_song_the_sentence_named(session, device):
    """Needs ``tools_ambient.alarm_create`` to call ``tools_alarms.song_for_create``."""
    from app.voice.realtime_sessions.tools_ambient import alarm_create

    ctx = _ctx(session, device, intent="alarm_create", media_query="Tarkan'ın Şımarık")
    ctx.live["wake_sequence"] = None
    out = alarm_create(ctx, {"when_spoken": "Yarın 7:30'da"})
    assert out["alarm"]["song"] == {"url": SEARCH_HIT, "title": "Dogum Gunun Kutlu Olsun"}
    assert "ile uyandıracağım" in out["speech"]


def test_wiring_a_bare_digit_hour_after_yarin_is_a_clock():
    """'yarın 7'de beni X ile uyandır' - the owner's own trial sentence. Needs
    ``app/alarms/tr_time.py``: a bare hour with a time suffix after a day word."""
    from app.alarms.tr_time import parse_when_text

    parsed = parse_when_text(
        "Yarın 7'de beni Tarkan'ın Şımarık'ıyla uyandır.", now=NOW, timezone="Europe/Istanbul"
    )
    assert parsed.local_time == "07:00"


# ---------------------------------------- the inspector's return of 2026-10-05 (RED first)


def test_clearing_the_song_of_an_alarm_created_with_one_clears_it(session):
    """Finding 1: a song set AT CREATION is cleared too - the column is the truth, the
    creation-time media never stands in for a song the owner took away."""
    alarm = _alarm(session, song={"url": ALARM_SONG, "title": "Şımarık"})
    alarms_service.set_alarm_song(session, alarm.id, url=None)
    alarm = alarms_service.require_alarm(session, alarm.id)
    assert alarms_service.alarm_dict(alarm)["song"] is None
    assert ALARM_SONG not in str(alarm.resolved_media_identity)


def test_a_cleared_song_is_never_played(session, device):
    alarms_service.set_wake_song(session, url=WAKE_SONG, title="Genel")
    alarm = _alarm(session, song={"url": ALARM_SONG, "title": "Şımarık"})
    alarms_service.set_alarm_song(session, alarm.id, url=None)
    device.results["browser.media_play"] = _play_only()
    _fire(session, device, alarm)
    assert _played_urls(device) == [WAKE_SONG]


def test_a_changed_song_leaves_no_trace_of_the_old_one(session, device):
    new_song = "https://www.youtube.com/watch?v=GulpembeBM1"
    alarms_service.set_wake_song(session, url=WAKE_SONG, title="Genel")
    alarm = _alarm(session, song={"url": ALARM_SONG, "title": "Şımarık"})
    alarms_service.set_alarm_song(session, alarm.id, url=new_song, title="Gülpembe")
    assert alarms_service.alarm_dict(alarm)["song"]["url"] == new_song
    device.results["browser.media_play"] = _play_only()
    _fire(session, device, alarm)
    assert _played_urls(device) == [new_song, WAKE_SONG]


class _DedupingDevice(FakeDeviceAction):
    """The real ``DeviceCommandClient``: a repeated idempotency key returns the OLD command's
    result and nothing new reaches the device."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.seen: dict[str, object] = {}

    def run(self, *, capability, payload, idempotency_key, timeout_s):
        if idempotency_key in self.seen:
            return self.seen[idempotency_key]
        result = super().run(
            capability=capability,
            payload=payload,
            idempotency_key=idempotency_key,
            timeout_s=timeout_s,
        )
        self.seen[idempotency_key] = result
        return result


def test_every_fallback_song_is_its_own_command(session):
    """Finding 2: own song C, the live global B, the stored copy A - three keys, three plays."""
    device = _DedupingDevice(results=happy_device_results())
    stored_copy = "https://www.youtube.com/watch?v=OldWakeSong"
    alarms_service.set_wake_song(session, url=stored_copy, title="Eski")
    alarm = _alarm(session)
    alarms_service.set_wake_song(session, url=WAKE_SONG, title="Yeni")
    alarm.song = {"url": ALARM_SONG, "title": "Şımarık"}
    session.commit()
    device.results["browser.media_play"] = _play_only(stored_copy)
    result = _fire(session, device, alarm)
    assert _played_urls(device) == [ALARM_SONG, WAKE_SONG, stored_copy]
    assert result.media_kind == PLAYED_KIND_YOUTUBE
    assert alarm.detail_json["media_played"]["url"] == stored_copy


@pytest.mark.parametrize(
    "text",
    [
        "Yarın 7:30'da beni seçtiğim müzikle uyandır.",
        "Yarın 7:30'da beni bu şarkıyla uyandır.",
        "Yarın 7:30'da beni o şarkıyla uyandır.",
        "Yarın 7:30'da beni şu şarkıyla uyandır.",
        "Yarın 7:30'da beni bir şarkıyla uyandır.",
        "Yarın 7:30'da beni aynı şarkıyla uyandır.",
        "Yarın 7:30'da beni her zamanki şarkıyla uyandır.",
    ],
)
def test_a_pointing_or_generic_word_is_never_a_title(text):
    """Finding 3: these name no song; nothing is searched, the approved wake song plays."""
    resolved = resolve_intent(text)
    assert resolved.intent is Intent.ALARM_CREATE
    assert resolved.media_query is None


def test_the_models_pointing_title_is_not_searched_either(session, device):
    ctx = _ctx(session, device, intent="alarm_create", media_query=None)
    assert tools_alarms.song_for_create(ctx, {"media": {"title": "seçtiğim müzik"}}) is None
    assert device.count("browser.search") == 0


@pytest.mark.parametrize(
    "text",
    ["Uyandırma şarkım ne olsun?", "Alarmımın şarkısını Şımarık mı yap dedim?"],
)
def test_a_question_about_the_wake_song_never_changes_it(text):
    assert alarm_song_set_match(text) is None


def _create_from(session, device, text):
    from app.voice.realtime_sessions.tools_ambient import alarm_create

    resolved = resolve_intent(text)
    ctx = _ctx(session, device, intent=resolved.intent.value, media_query=resolved.media_query)
    ctx.live["wake_sequence"] = None
    return resolved, alarm_create(ctx, {"when_spoken": text})


@pytest.mark.parametrize(
    ("text", "clock", "song"),
    [
        ("Yarın sabah yediyi çeyrek geçe Bella Ciao ile uyandır.", "07:15", "Bella Ciao"),
        ("Yarın 7'de beni Duman'ın Bu Akşam'ıyla uyandır.", "07:00", "Duman'ın Bu Akşam"),
        ("Yarın 6'da beni On Dakika ile uyandır.", "06:00", "On Dakika"),
    ],
)
def test_the_time_is_read_from_what_the_song_leaves(session, device, text, clock, song):
    """Finding 4: the title comes out first, the clock is read from the rest."""
    resolved, out = _create_from(session, device, text)
    assert resolved.media_query == song
    assert out["alarm"]["local_time"] == clock, out["speech"]
    assert out["alarm"]["recurrence"] in (None, {}) or not out["alarm"]["recurrence"].get(
        "weekdays"
    )


def test_a_song_that_is_not_found_is_said_at_once(session, device):
    """Finding 5: the alarm is still set, and the sentence says the song was not found."""
    device.results["browser.search"] = ok(results=[{"url": "https://example.com/x"}])
    _, out = _create_from(session, device, "Yarın 7'de beni Tarkan'ın Şımarık'ıyla uyandır.")
    assert out["execution_status"] == "executed"
    assert out["alarm"]["local_time"] == "07:00"
    assert "bulamadım" in out["speech"]
    assert out["alarm"]["song"] is None


def test_a_quarter_past_without_its_cedilla_is_not_night():
    """The corpus' ASCII variant: "yediyi ceyrek gece" is "geçe" (past), never "gece"
    (night) - 07:15, not 19:15."""
    from app.alarms.tr_time import parse_when_text

    parsed = parse_when_text(
        "yarin sabah yediyi ceyrek gece uyandir", now=NOW, timezone="Europe/Istanbul"
    )
    assert parsed.local_time == "07:15"
    night = parse_when_text("yarın gece on birde uyandır", now=NOW, timezone="Europe/Istanbul")
    assert night.local_time == "23:00"


# ------------------------------- the inspector's second return of 2026-10-05 (RED first)
#
# The sentence is cut into two parts that never overlap: the title, and the rest the clock
# is read from. The title's left edge is "beni" when it stands before the title, else the
# end of the clock phrase ("sonra", "geçe", "kala", "buçukta", a suffixed clock "7'de").
# A daypart, a day name or a number inside the title never touches the clock or repeats.

#: (sentence, scheduled_for in UTC, the song). NOW is Monday 2026-10-05 10:00 UTC (13:00
#: in Istanbul, UTC+3), so "yarın 07:00" is 04:00 UTC on the 6th and "cuma" is the 9th.
SPLIT_FORMS = [
    ("On dakika sonra Şımarık'la uyandır.", "2026-10-05T10:10:00Z", "Şımarık"),
    ("Yarım saat sonra Sezen Aksu çalarak uyandır.", "2026-10-05T10:30:00Z", "Sezen Aksu"),
    ("İki saat sonra Şımarık ile uyandır.", "2026-10-05T12:00:00Z", "Şımarık"),
    ("Yarın 7'de beni Akşam Güneşi ile uyandır.", "2026-10-06T04:00:00Z", "Akşam Güneşi"),
    (
        "Cuma 7'de beni Pazartesi Sendromu ile uyandır.",
        "2026-10-09T04:00:00Z",
        "Pazartesi Sendromu",
    ),
    (
        "Yarın sekize çeyrek kala Gece Yolcuları ile uyandır.",
        "2026-10-06T04:45:00Z",
        "Gece Yolcuları",
    ),
    # A clock right after "beni" stays with the clock; the title starts after it.
    ("Beni yarın 7'de Şımarık ile uyandır.", "2026-10-06T04:00:00Z", "Şımarık"),
    # The recogniser's "geçe" without its cedilla still ends the clock (not "night").
    (
        "yarin sabah yediyi ceyrek gece Aksam Gunesi ile uyandir",
        "2026-10-06T04:15:00Z",
        "Aksam Gunesi",
    ),
    # Kept green from the first return.
    (
        "Yarın sabah yediyi çeyrek geçe Bella Ciao ile uyandır.",
        "2026-10-06T04:15:00Z",
        "Bella Ciao",
    ),
    (
        "Yarın 7'de beni Duman'ın Bu Akşam'ıyla uyandır.",
        "2026-10-06T04:00:00Z",
        "Duman'ın Bu Akşam",
    ),
    ("Yarın 6'da beni On Dakika ile uyandır.", "2026-10-06T03:00:00Z", "On Dakika"),
]


@pytest.mark.parametrize(("text", "at", "song"), SPLIT_FORMS)
def test_the_title_and_the_clock_never_overlap(session, device, text, at, song):
    resolved, out = _create_from(session, device, text)
    assert resolved.intent is Intent.ALARM_CREATE
    assert resolved.media_query == song
    assert out["execution_status"] == "executed", out["speech"]
    assert out["alarm"]["scheduled_for"] == at, out["speech"]
    # A bare day name repeats on that day (tr_time's rule, corpus a.song.2: "Pazartesi" is
    # every Monday): "Cuma" is Friday ONLY - "Pazartesi" in the title adds no Monday.
    weekdays = [4] if text.startswith("Cuma") else None
    assert (out["alarm"]["recurrence"] or {}).get("weekdays") == weekdays, out["alarm"]


@pytest.mark.parametrize(("text", "song"), [(t, s) for t, _, s in SPLIT_FORMS])
def test_the_clock_text_keeps_nothing_of_the_title(text, song):
    from app.voice.intents import alarm_text_without_song

    rest = alarm_text_without_song(text).split()
    for word in song.split():
        assert not any(left.startswith(word) for left in rest), rest


def test_an_alarm_created_with_a_media_url_carries_it_as_its_song(session, device):
    """The ADR addendum: a url given at creation is the alarm's own song, so it plays
    BEFORE the global wake song - and the global one only when it fails."""
    alarms_service.set_wake_song(session, url=WAKE_SONG, title="Genel")
    alarm = _alarm(session, media={"url": ALARM_SONG, "title": "Şımarık"})
    assert alarms_service.alarm_dict(alarm)["song"] == {"url": ALARM_SONG, "title": "Şımarık"}
    device.results["browser.media_play"] = _play_only()
    _fire(session, device, alarm)
    assert _played_urls(device)[:2] == [ALARM_SONG, WAKE_SONG]


def test_a_lowercased_dotted_capital_i_is_still_a_number():
    """Corpus a.song.12.v2: Python lowercases "İki" to "i" + U+0307 (combining dot); the
    clock must still read "iki saat sonra" as two hours, never "when_unparsed"."""
    from app.alarms.tr_time import parse_when_text

    parsed = parse_when_text("İki saat sonra uyandır".lower(), now=NOW, timezone="Europe/Istanbul")
    assert parsed.relative_seconds == 7200
