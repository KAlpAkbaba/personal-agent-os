"""Unit tests: barge-in / realtime dialogue state machine (deterministic, no audio)."""

import pytest

from app.voice.errors import VoiceError
from app.voice.providers import FakeRealtimeProvider
from app.voice.realtime import RealtimeSession, RealtimeState


def test_barge_in_cuts_assistant_speech() -> None:
    s = RealtimeSession()
    s.assistant_start_speaking("uzun bir cevap")
    assert s.state == RealtimeState.ASSISTANT_SPEAKING
    s.owner_speech_started("pardon")  # owner overlaps -> barge-in
    kinds = s.event_kinds()
    assert "assistant_speech_cut" in kinds
    assert s.barge_in_count == 1
    # After cutting, the mic keeps listening to the owner.
    assert s.state == RealtimeState.LISTENING


def test_dur_has_top_priority_from_tool_progress() -> None:
    """'dur' interrupts even while a tool runs and the assistant reports progress."""
    s = RealtimeSession()
    s.start_tool_call("arama yapılıyor")
    s.assistant_progress("hâlâ arıyorum")
    assert s.state == RealtimeState.TOOL_RUNNING
    ev = s.owner_speech_started("dur")
    assert s.barge_in_count == 1
    assert ev.kind == "listening"
    # The cut event carries the stop-word reason.
    cut = [e for e in s.events if e.kind == "assistant_speech_cut"][0]
    assert cut.detail == "stop_word"


def test_barge_in_latency_is_bounded() -> None:
    s = RealtimeSession()
    s.assistant_start_speaking()
    s.owner_speech_started("dur")
    # Speech is cut on the very next event after owner-speech-start.
    assert s.barge_in_latency_events() == 1


def test_normal_owner_turn_without_barge_in() -> None:
    s = RealtimeSession()
    s.owner_speech_started("merhaba")  # assistant is idle -> no barge-in
    assert s.barge_in_count == 0
    assert s.state == RealtimeState.LISTENING
    s.owner_speech_ended()
    assert s.state == RealtimeState.IDLE


def test_progress_only_valid_during_tool_call() -> None:
    s = RealtimeSession()
    with pytest.raises(VoiceError):
        s.assistant_progress("olmaz")


def test_network_loss_retains_context() -> None:
    s = RealtimeSession()
    s.assistant_start_speaking()
    s.network_lost("wifi down")
    s.network_restored("wifi up")
    assert s.state != RealtimeState.CLOSED  # context retained
    assert "network_lost" in s.event_kinds()


def test_fake_realtime_provider_session_and_handle_api() -> None:
    prov = FakeRealtimeProvider()
    assert prov.capabilities().latency_class == "realtime"
    sess = prov.open_session()
    sess.assistant_start_speaking()
    sess.push_audio(b"\x00\x01")
    sess.request_barge_in()  # equivalent to owner saying 'dur'
    assert sess.barge_in_count == 1
    sess.close()
    assert sess.state == RealtimeState.CLOSED
    with pytest.raises(VoiceError):
        sess.push_audio(b"\x00")  # closed session rejects
