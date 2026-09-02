"""M12 unit tests: the deterministic full-duplex simulator.

Everything is on a virtual clock: no sleeping, no threads, no audio device.
"""

from __future__ import annotations

import pytest

from app.voice.errors import VoiceError
from app.voice.providers import (
    RT_NETWORK_LOST,
    RT_NETWORK_RESTORED,
    RT_RESPONSE_AUDIO,
    RT_RESPONSE_DONE,
    RT_RESPONSE_STARTED,
    RT_SPEECH_STARTED,
    RT_SPEECH_STOPPED,
    RT_TOOL_CALL,
    RealtimeSessionEvent,
)
from app.voice.realtime import RealtimeState
from app.voice.simulator import (
    SimulatedRealtimeProvider,
    SimulatedTurn,
    SimulatorTimings,
    synth_frame,
)

T = SimulatorTimings()


def speak(session, frames: int, *, filler_last: bool = False) -> None:
    for i in range(frames):
        session.push_audio(synth_frame(i, frame_ms=T.frame_ms),
                           filler=filler_last and i == frames - 1)
        session.advance(T.frame_ms)


def tool_provider(arguments: dict | None = None) -> SimulatedRealtimeProvider:
    return SimulatedRealtimeProvider(
        script_factory=lambda: [SimulatedTurn.tool_call("research.start", arguments)]
    )


def test_capabilities_declare_a_conversation_capable_provider() -> None:
    caps = SimulatedRealtimeProvider().capabilities()
    assert caps.is_conversation_capable("tr-TR")
    assert caps.end_of_turn == "semantic"
    assert caps.ephemeral_credentials is True
    assert caps.requires_api_key is False


def test_credential_is_random_per_session_and_never_a_settings_value() -> None:
    prov = SimulatedRealtimeProvider()
    a = prov.mint_credential(session_id="s1", ttl_s=60)
    b = prov.mint_credential(session_id="s1", ttl_s=60)
    assert a.secret != b.secret and a.secret.startswith("sim_")
    assert prov.minted == ["sim:s1", "sim:s1"]  # refs only, never the secret
    d = a.to_client_dict()
    assert set(d) == {"provider", "secret", "expires_at", "transport", "session_ref"}


def test_short_turn_produces_events_in_order_with_timings() -> None:
    session = SimulatedRealtimeProvider().open_session()
    got: list[RealtimeSessionEvent] = []
    audio: list[bytes] = []
    session.on_event(got.append)
    session.on_audio(audio.append)
    speak(session, 5)
    last_frame_at = session.clock_ms - T.frame_ms
    session.run_until_idle()
    kinds = [e.kind for e in got]
    assert kinds[0] == RT_SPEECH_STARTED
    assert kinds[1] == RT_SPEECH_STOPPED
    assert kinds[2] == RT_RESPONSE_STARTED
    assert kinds[3] == RT_RESPONSE_AUDIO
    assert kinds[-1] == RT_RESPONSE_DONE and got[-1].payload["cancelled"] is False
    started = got[0]
    stopped = got[1]
    first_audio = got[3]
    assert started.at_ms == T.uplink_delay_ms
    assert stopped.at_ms == last_frame_at + T.uplink_delay_ms + T.end_of_turn_delay_ms
    assert first_audio.at_ms - stopped.at_ms == T.first_audio_delay_ms
    assert audio and all(len(f) == 16000 * T.frame_ms // 1000 * 2 for f in audio)
    assert session.audio_out_bytes == sum(len(f) for f in audio)
    assert session.state == RealtimeState.IDLE
    # the payload records frame sizes, never audio bytes
    assert all("bytes" in e.payload and isinstance(e.payload["bytes"], int)
               for e in got if e.kind == RT_RESPONSE_AUDIO)


def test_overlap_barge_in_cancels_response_after_stop_delay() -> None:
    session = SimulatedRealtimeProvider().open_session()
    speak(session, 5)
    session.advance(T.uplink_delay_ms + T.end_of_turn_delay_ms + T.first_audio_delay_ms + 100)
    assert session.responding
    frames_before = len(session.events_of(RT_RESPONSE_AUDIO))
    onset = session.clock_ms
    session.push_audio(b"\x01\x02")
    session.advance(T.uplink_delay_ms + T.barge_in_stop_delay_ms)
    done = session.events_of(RT_RESPONSE_DONE)[-1]
    assert done.payload["cancelled"] is True
    assert done.at_ms - onset == T.uplink_delay_ms + T.barge_in_stop_delay_ms
    assert not session.responding
    # no frame of the cancelled response leaks after the stop (the owner's
    # new turn has not ended yet, so nothing new may be playing either)
    session.advance(T.end_of_turn_delay_ms // 2)
    assert len(session.events_of(RT_RESPONSE_AUDIO)) >= frames_before
    assert all(e.at_ms <= done.at_ms for e in session.events_of(RT_RESPONSE_AUDIO))
    # M4 FSM ordering preserved: cut first, then latched
    kinds = session.fsm.event_kinds()
    assert kinds.index("assistant_speech_cut") < kinds.index("barge_in")
    assert session.fsm.barge_in_count == 1
    # ...and the owner's new turn gets a fresh answer afterwards
    session.run_until_idle()
    assert session.events_of(RT_RESPONSE_DONE)[-1].payload["cancelled"] is False


def test_explicit_stop_word_barge_in_from_tool_progress() -> None:
    session = tool_provider().open_session()
    speak(session, 5)
    session.advance(T.uplink_delay_ms + T.end_of_turn_delay_ms + T.tool_call_delay_ms + 1)
    call = session.pending_tool_call
    assert call and call["name"] == "research.start"
    assert session.state == RealtimeState.TOOL_RUNNING
    session.submit_tool_result(call["call_id"], {"status": "running", "preamble": "Bakıyorum."})
    session.advance(T.tool_preamble_delay_ms + 100)
    assert session.events_of(RT_RESPONSE_AUDIO)[-1].payload["preamble"] is True
    session.request_barge_in()  # "dur" while the preamble plays
    session.advance(T.barge_in_stop_delay_ms)
    done = session.events_of(RT_RESPONSE_DONE)[-1]
    assert done.payload == {"turn": 1, "cancelled": True, "preamble": True}
    cut = [e for e in session.fsm.events if e.kind == "assistant_speech_cut"][-1]
    assert cut.detail == "stop_word"


def test_tool_call_flow_preamble_then_completion_resumes_speech() -> None:
    session = tool_provider({"topic": "x"}).open_session()
    speak(session, 5)
    session.run_until_idle()  # waits on the tool: nothing else is scheduled
    tool = session.events_of(RT_TOOL_CALL)[0]
    assert tool.payload["arguments"] == {"topic": "x"}
    with pytest.raises(VoiceError):
        session.submit_tool_result("wrong-id", {"status": "succeeded"})
    session.submit_tool_result(tool.payload["call_id"],
                               {"status": "running", "preamble": "Bakıyorum."})
    session.run_until_idle()
    preamble_frames = [e for e in session.events_of(RT_RESPONSE_AUDIO) if e.payload["preamble"]]
    assert preamble_frames
    assert preamble_frames[0].at_ms == tool.at_ms + T.tool_preamble_delay_ms
    done_at = session.clock_ms
    session.submit_tool_result(tool.payload["call_id"], {"status": "succeeded", "n": 3})
    assert session.pending_tool_call is None
    session.run_until_idle()
    resumed = [e for e in session.events_of(RT_RESPONSE_AUDIO) if not e.payload["preamble"]]
    assert resumed[0].at_ms == done_at + T.tool_done_to_speech_ms
    assert session.fsm.event_kinds().count("tool_call_finished") == 1


def test_hesitation_filler_extends_end_of_turn() -> None:
    session = SimulatedRealtimeProvider().open_session()
    speak(session, 4, filler_last=True)
    session.advance(T.uplink_delay_ms + T.end_of_turn_delay_ms + 50)
    assert session.events_of(RT_SPEECH_STOPPED) == []  # not cut off yet
    session.advance(T.hesitation_guard_ms)
    assert len(session.events_of(RT_SPEECH_STOPPED)) == 1
    assert session.events_of(RT_SPEECH_STOPPED)[0].payload["after_filler"] is True


def test_network_loss_retains_context_and_drops_in_flight_audio() -> None:
    session = SimulatedRealtimeProvider().open_session()
    speak(session, 3)
    session.advance(T.uplink_delay_ms + T.end_of_turn_delay_ms + T.first_audio_delay_ms + 40)
    assert session.responding
    session.network_lost()
    n = len(session.events_of(RT_RESPONSE_AUDIO))
    session.advance(2000)
    assert len(session.events_of(RT_RESPONSE_AUDIO)) == n
    assert session.state != RealtimeState.CLOSED
    session.network_restored()
    assert session.event_kinds()[-2:] == [RT_NETWORK_LOST, RT_NETWORK_RESTORED]
    speak(session, 3)
    session.run_until_idle()
    assert session.events_of(RT_RESPONSE_DONE)[-1].payload["cancelled"] is False


def test_slow_timings_are_honoured_exactly() -> None:
    slow = SimulatorTimings(first_audio_delay_ms=1500, barge_in_stop_delay_ms=400)
    session = SimulatedRealtimeProvider(timings=slow).open_session()
    speak(session, 2)
    session.run_until_idle()
    stopped = session.events_of(RT_SPEECH_STOPPED)[0]
    first = session.events_of(RT_RESPONSE_AUDIO)[0]
    assert first.at_ms - stopped.at_ms == 1500


def test_closed_session_rejects_everything_and_clears_schedule() -> None:
    session = SimulatedRealtimeProvider().open_session()
    speak(session, 2)
    assert session.pending > 0
    session.close()
    assert session.pending == 0 and session.closed
    with pytest.raises(VoiceError):
        session.push_audio(b"\x00")
    with pytest.raises(VoiceError):
        session.request_barge_in()
    session.close()  # idempotent


def test_timings_reject_negative_values() -> None:
    with pytest.raises(ValueError):
        SimulatorTimings(first_audio_delay_ms=-1)
    with pytest.raises(ValueError):
        SimulatorTimings(frame_ms=0)
