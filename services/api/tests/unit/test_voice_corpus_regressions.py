"""Regression tests for the routing defects the Owner Utterance Corpus found (2026-09-07).

Each test names the corpus case that exposed the defect (tests/voice_corpus/corpus.py); the
corpus keeps the utterance, and these pin the ROUTER and TOOL behaviour that fixed it, at the
unit level, so the neighbouring-intent suite stays fast and the failure names the rule.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.alarms import service as alarms_service
from app.alarms.sequence import WakeSequence
from app.alarms.tr_time import parse_when_struct
from app.explain.classify import QUERY_EYE_STATE, QUERY_WORLD_STATE, classify
from app.voice.intents import (
    RESEARCH_CLASS_TECHNICAL_EXPLANATION,
    Intent,
    resolve_intent,
    spoken_minutes,
)
from app.voice.realtime_sessions import tools_ambient
from app.voice.realtime_sessions.tools import ToolContext
from tests.alarms_support import FakeDeviceAction, build_session_factory, happy_device_results

NOW = datetime(2026, 9, 9, 6, 0, tzinfo=UTC)


# ---------------------------------------------------- a.stop.4-6: bare stops while ringing


@pytest.mark.parametrize("text", ["Sustur.", "Tamam, kapat.", "Kes şunu.", "Durdur."])
def test_a_bare_stop_word_is_the_alarm_while_an_alarm_rings(text: str) -> None:
    resolved = resolve_intent(text, alarm_ringing=True)
    assert resolved.intent is Intent.ALARM_STOP, text
    assert resolved.capability == "alarm.stop"


@pytest.mark.parametrize(
    ("text", "intent"),
    [("Sustur.", Intent.NONE), ("Kes şunu.", Intent.STOP), ("Durdur.", Intent.STOP)],
)
def test_the_same_words_keep_their_meaning_with_no_alarm_ringing(text: str, intent: Intent) -> None:
    assert resolve_intent(text, alarm_ringing=False).intent is intent


def test_a_screen_command_is_never_swallowed_by_the_ringing_rule() -> None:
    """"Ekranı kapat" while the alarm rings is still the display (the alarm keeps ringing
    until the owner says so about the alarm)."""
    assert resolve_intent("Ekranı kapat.", alarm_ringing=True).intent is Intent.DISPLAY_OFF
    assert resolve_intent("Gözünü kapat.", alarm_ringing=True).intent is Intent.EYE_DISABLE


# --------------------------------------------------- a.snooze.1-2: the minutes SAID


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("10 dakika ertele.", 10),
        ("On dakika sonra tekrar çal.", 10),
        ("on beş dakika ertele", 15),
        ("Beş dakika ertele.", 5),
        ("Biraz ertele.", None),
        ("yirmi dk sonra yeniden çal", 20),
    ],
)
def test_a_snooze_carries_the_minutes_the_owner_said(text: str, minutes: int | None) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is Intent.ALARM_SNOOZE, text
    assert resolved.alarm_minutes == minutes
    assert resolved.to_dict()["alarm_minutes"] == minutes


def test_ring_again_without_a_later_is_still_a_repeat() -> None:
    """"Şarkıyı tekrar çal" (play it again) has no "later" in it and is not a snooze."""
    assert resolve_intent("Şarkıyı tekrar çal.").intent is Intent.REPEAT
    assert resolve_intent("Araştırmayı tekrar çalıştır.").intent is not Intent.ALARM_SNOOZE


def test_spoken_minutes_is_bounded_and_needs_the_unit() -> None:
    assert spoken_minutes(("on", "dakika")) == 10
    assert spoken_minutes(("dakika",)) is None
    assert spoken_minutes(("üç", "yüz", "dakika")) is None  # "yüz" is not a minute word
    assert spoken_minutes(("iki", "saat")) is None


def _ringing_alarm(session, sequence):
    alarm = alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 5}, now=NOW)
    )
    alarms_service.fire_alarm(
        session,
        alarm.id,
        sequence=sequence,
        firing_id=uuid.uuid4(),
        now=NOW + timedelta(seconds=10),
    )
    return alarm


def _ctx(session, sequence, *, last_utterance: dict | None) -> ToolContext:
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="web",
        context={"last_utterance": last_utterance} if last_utterance else {},
        db=session,
        now=NOW + timedelta(seconds=12),
        call_id="call-1",
        live={"wake_sequence": sequence},
    )


def test_the_snooze_tool_prefers_the_minutes_said_over_the_models_argument() -> None:
    with build_session_factory()() as session:
        sequence = WakeSequence(
            device_action=FakeDeviceAction(results=happy_device_results()), tts=None
        )
        alarm = _ringing_alarm(session, sequence)
        said = {
            "at": (NOW + timedelta(seconds=11)).isoformat().replace("+00:00", "Z"),
            "intent": "alarm_snooze",
            "alarm_minutes": 10,
        }
        result = tools_ambient.alarm_snooze(
            _ctx(session, sequence, last_utterance=said), {"minutes": 5}
        )
        session.refresh(alarm)
        assert alarm.snooze_minutes == 10
        assert "on dakika" in result["speech"].lower()


def test_the_snooze_tool_falls_back_to_the_argument_when_nothing_was_said() -> None:
    with build_session_factory()() as session:
        sequence = WakeSequence(
            device_action=FakeDeviceAction(results=happy_device_results()), tts=None
        )
        alarm = _ringing_alarm(session, sequence)
        said = {
            "at": (NOW + timedelta(seconds=11)).isoformat().replace("+00:00", "Z"),
            "intent": "alarm_snooze",
            "alarm_minutes": None,
        }
        tools_ambient.alarm_snooze(_ctx(session, sequence, last_utterance=said), {"minutes": 7})
        session.refresh(alarm)
        assert alarm.snooze_minutes == 7


def test_a_stale_turn_record_does_not_decide_the_minutes() -> None:
    with build_session_factory()() as session:
        sequence = WakeSequence(
            device_action=FakeDeviceAction(results=happy_device_results()), tts=None
        )
        alarm = _ringing_alarm(session, sequence)
        stale = {
            "at": (NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            "intent": "alarm_snooze",
            "alarm_minutes": 45,
        }
        tools_ambient.alarm_snooze(_ctx(session, sequence, last_utterance=stale), {"minutes": 7})
        session.refresh(alarm)
        assert alarm.snooze_minutes == 7


# ------------------------------------------------------- d.*: the display's own words


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Görüntüyü kapat.", Intent.DISPLAY_OFF),
        ("goruntuyu kapat", Intent.DISPLAY_OFF),
        ("Ekranı uyandır.", Intent.DISPLAY_WAKE),  # d.wake.3 misrouted to alarm.create
        ("ekrani uyandir", Intent.DISPLAY_WAKE),
        ("ekranlari ac", Intent.DISPLAY_WAKE),
        ("monitorleri ac", Intent.DISPLAY_WAKE),
        ("Ekran durumu ne?", Intent.DISPLAY_QUERY),
        ("Monitörler kapalı mı?", Intent.DISPLAY_QUERY),
        ("monitorler kapali mi", Intent.DISPLAY_QUERY),
    ],
)
def test_display_phrases_from_the_corpus(text: str, intent: Intent) -> None:
    assert resolve_intent(text).intent is intent, text


def test_wake_me_is_still_an_alarm_when_no_screen_is_named() -> None:
    assert resolve_intent("Yarın 07:30'da beni uyandır.").intent is Intent.ALARM_CREATE
    assert resolve_intent("Alarm durumu ne?").intent is Intent.ALARM_QUERY


# --------------------------------------------------------- e.*: the eye without diacritics


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("gozunu kapat", Intent.EYE_DISABLE),
        ("kamerayi kapat", Intent.EYE_DISABLE),
        ("gozunu ac", Intent.EYE_ENABLE),
        ("kamerayi ac", Intent.EYE_ENABLE),
        ("gözlük tak", Intent.NONE),  # "göz" is still not a prefix
    ],
)
def test_eye_phrases_survive_an_asr_that_drops_diacritics(text: str, intent: Intent) -> None:
    assert resolve_intent(text).intent is intent, text


# ------------------------------------------------- r.tech.6: technical without "teknik"


def test_how_it_works_in_the_background_is_a_technical_explanation() -> None:
    for text in (
        "Bunun arka planda nasıl çalıştığını anlat.",
        "bunun arka planda nasil calistigini anlat",
    ):
        resolved = resolve_intent(text, has_completed_research=True)
        assert resolved.intent is Intent.TECHNICAL, text
        assert resolved.research_class == RESEARCH_CLASS_TECHNICAL_EXPLANATION
        assert resolved.research_reference == "current"


def test_the_diacritic_free_research_detail_question_reaches_the_same_kind() -> None:
    """r.tech.4.v3: "az onceki arastirmanin teknik detayini acikla" must classify like its
    spelled sibling, so the two do not take different routes to the same answer."""
    with_marks = resolve_intent(
        "Az önceki araştırmanın teknik detayını açıkla.", has_completed_research=True
    )
    without = resolve_intent(
        "az onceki arastirmanin teknik detayini acikla", has_completed_research=True
    )
    assert with_marks.intent is without.intent is Intent.EXPLAIN
    assert with_marks.research_class == RESEARCH_CLASS_TECHNICAL_EXPLANATION
    assert without.research_class == RESEARCH_CLASS_TECHNICAL_EXPLANATION


# ---------------------------------------------------- p.q.1 / p.q.4: presence questions


def test_presence_questions_reach_the_live_state_kinds() -> None:
    assert classify("Beni görüyor musun?").kind == QUERY_EYE_STATE
    assert classify("beni goruyor musun").kind == QUERY_EYE_STATE
    assert classify("Şu an burada mıyım?").kind == QUERY_WORLD_STATE
    for text in ("Beni görüyor musun?", "Şu an burada mıyım?"):
        resolved = resolve_intent(text)
        assert resolved.intent is Intent.EXPLAIN, text
        assert resolved.query_kind in (QUERY_EYE_STATE, QUERY_WORLD_STATE)
