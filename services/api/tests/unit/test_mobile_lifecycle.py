"""M9 unit tests: the mobile lifecycle state machine.

Acceptance bullet: "microphone/realtime voice works under normal mobile
lifecycle". On a machine with no device that cannot mean "we listened to a
microphone"; it means the control plane does the right thing when the OS moves
the app around, and that is what is asserted here — no audio, no timers, no I/O.

The barge-in machine is *reused*, not re-implemented: several tests reach into
`lifecycle.realtime` and assert the VOICE_SPEC §2 behaviour is the same object's
behaviour, so a change there cannot silently diverge from what mobile does.
"""

from __future__ import annotations

import pytest

from app.mobile.errors import MobileError, MobileErrorClass
from app.mobile.lifecycle import AppState, MicState, MobileLifecycle, Playback
from app.voice.realtime import RealtimeSession, RealtimeState

CURSOR_A = {"section_id": "s1", "paragraph_id": "p2", "sentence_index": 0, "char_offset": 0}
CURSOR_B = {"section_id": "s3", "paragraph_id": "p4", "sentence_index": 1, "char_offset": 0}


def started() -> MobileLifecycle:
    machine = MobileLifecycle()
    machine.start_narration(session_id="n1", cursor=CURSOR_A)
    return machine


# ---------------------------------------------------------------- fresh state


def test_a_new_client_is_foreground_silent_and_micless() -> None:
    machine = MobileLifecycle()
    assert machine.app_state == AppState.FOREGROUND
    assert machine.playback == Playback.STOPPED
    assert machine.mic == MicState.CLOSED
    assert machine.snapshot()["requires_reauth"] is False


# ------------------------------------------------------------------- the mic


def test_the_mic_opens_only_in_the_foreground() -> None:
    machine = MobileLifecycle()
    session = machine.open_realtime()
    assert isinstance(session, RealtimeSession)
    assert machine.mic == MicState.OPEN
    with pytest.raises(MobileError) as exc:
        machine.open_realtime()
    assert exc.value.error_class == MobileErrorClass.VALIDATION_ERROR


def test_backgrounding_releases_an_in_flight_realtime_session() -> None:
    """iOS/Android do not hand a general-purpose app a background microphone."""
    machine = MobileLifecycle()
    session = machine.open_realtime()
    machine.to_background()
    assert machine.app_state == AppState.BACKGROUND
    assert machine.mic == MicState.CLOSED
    assert session.state == RealtimeState.CLOSED
    # The affordance is raised; the mic is never silently reopened.
    assert machine.realtime_resume_required is True


def test_the_mic_cannot_be_opened_from_the_background() -> None:
    machine = MobileLifecycle()
    machine.to_background()
    with pytest.raises(MobileError):
        machine.open_realtime()


def test_returning_to_the_foreground_does_not_reopen_the_mic() -> None:
    machine = MobileLifecycle()
    machine.open_realtime()
    machine.to_background()
    machine.to_foreground()
    assert machine.app_state == AppState.FOREGROUND
    assert machine.mic == MicState.CLOSED
    assert machine.realtime_resume_required is True
    # ...but the owner can open a new one.
    machine.open_realtime()
    assert machine.mic == MicState.OPEN
    assert machine.realtime_resume_required is False


def test_an_owner_close_raises_no_resume_affordance() -> None:
    machine = MobileLifecycle()
    machine.open_realtime()
    machine.close_realtime()
    assert machine.mic == MicState.CLOSED
    assert machine.realtime_resume_required is False


# -------------------------------------------------------- barge-in, delegated


def test_barge_in_is_the_reused_voice_machine() -> None:
    machine = MobileLifecycle()
    session = machine.open_realtime()
    machine.assistant_start_speaking("uzun cevap")
    machine.owner_speech_started("dur")
    # The VOICE_SPEC §2 sequence belongs to the realtime machine, unchanged.
    assert "assistant_speech_cut" in session.event_kinds()
    assert session.barge_in_count == 1
    assert session.state == RealtimeState.LISTENING
    assert session.barge_in_latency_events() == 1


def test_speaking_over_narration_pauses_it_and_does_not_auto_resume() -> None:
    machine = started()
    machine.open_realtime()
    machine.owner_speech_started("bir saniye")
    assert machine.playback == Playback.PAUSED
    # The owner interrupted deliberately: resuming is "devam", not automatic.
    machine.owner_speech_ended()
    assert machine.playback == Playback.PAUSED
    assert "paused:barge_in" in machine.checkpoint_reasons()


def test_speech_events_need_an_open_mic() -> None:
    machine = MobileLifecycle()
    with pytest.raises(MobileError):
        machine.owner_speech_started("merhaba")


# -------------------------------------------------------------- background


def test_narration_keeps_playing_in_the_background() -> None:
    """Background audio is the entire reason to want a native client."""
    machine = started()
    machine.to_background()
    assert machine.app_state == AppState.BACKGROUND
    assert machine.playback == Playback.PLAYING
    # ...and the position is saved, because a backgrounded app can be killed.
    assert "backgrounded" in machine.checkpoint_reasons()
    assert machine.last_checkpoint().cursor == CURSOR_A


def test_backgrounding_twice_is_idempotent() -> None:
    machine = started()
    machine.to_background()
    machine.to_background()
    assert machine.checkpoint_reasons().count("backgrounded") == 1


def test_narration_can_advance_while_backgrounded() -> None:
    machine = started()
    machine.to_background()
    machine.advance_narration(CURSOR_B)
    assert machine.cursor == CURSOR_B


# ------------------------------------------------------------- interruption


def test_a_phone_call_pauses_narration_and_takes_the_mic() -> None:
    machine = started()
    session = machine.open_realtime()
    machine.interrupt(reason="phone_call")
    assert machine.app_state == AppState.INTERRUPTED
    assert machine.playback == Playback.PAUSED
    assert machine.mic == MicState.CLOSED
    assert session.state == RealtimeState.CLOSED
    assert "paused:phone_call" in machine.checkpoint_reasons()


def test_resume_after_a_call_restores_playback_but_not_the_mic() -> None:
    machine = started()
    machine.open_realtime()
    machine.interrupt()
    machine.resume()
    assert machine.app_state == AppState.FOREGROUND
    # The owner did not ask for the interruption, so playback comes back.
    assert machine.playback == Playback.PLAYING
    # The microphone does not: reopening it is an owner decision.
    assert machine.mic == MicState.CLOSED
    assert machine.realtime_resume_required is True


def test_a_call_during_background_playback_resumes_into_the_background() -> None:
    machine = started()
    machine.to_background()
    machine.interrupt(reason="incoming_call")
    machine.resume()
    assert machine.app_state == AppState.BACKGROUND
    assert machine.playback == Playback.PLAYING


def test_to_foreground_from_interrupted_is_a_resume() -> None:
    machine = started()
    machine.to_background()
    machine.interrupt()
    machine.to_foreground()
    assert machine.app_state == AppState.FOREGROUND
    assert machine.playback == Playback.PLAYING


def test_a_manual_pause_is_not_undone_by_a_resume() -> None:
    """The owner said "dur"; a phone call must not restart the report."""
    machine = started()
    machine.pause_narration(reason="owner")
    machine.interrupt()
    machine.resume()
    assert machine.playback == Playback.PAUSED


def test_narration_cannot_start_while_interrupted() -> None:
    machine = MobileLifecycle()
    machine.interrupt()
    with pytest.raises(MobileError):
        machine.start_narration(session_id="n1", cursor=CURSOR_A)


def test_resume_is_only_valid_from_interrupted() -> None:
    machine = MobileLifecycle()
    with pytest.raises(MobileError) as exc:
        machine.resume()
    assert exc.value.error_class == MobileErrorClass.VALIDATION_ERROR


def test_interrupt_is_idempotent() -> None:
    machine = started()
    machine.interrupt()
    machine.interrupt()
    assert machine.app_state == AppState.INTERRUPTED


# ----------------------------------------------------------------- network


def test_network_loss_pauses_playback_and_keeps_realtime_context() -> None:
    machine = started()
    session = machine.open_realtime()
    machine.network_lost("wifi_to_lte")
    assert machine.playback == Playback.PAUSED
    # VOICE_SPEC §2: the realtime session is not destroyed by a transient loss.
    assert session.state != RealtimeState.CLOSED
    assert "network_lost" in session.event_kinds()
    machine.network_restored()
    assert machine.playback == Playback.PLAYING
    assert "network_restored" in session.event_kinds()


# --------------------------------------------------------------- termination


def test_termination_saves_the_cursor_and_closes_everything() -> None:
    machine = started()
    machine.open_realtime()
    machine.advance_narration(CURSOR_B)
    machine.terminate(reason="os_killed")
    assert machine.app_state == AppState.TERMINATED
    assert machine.playback == Playback.STOPPED
    assert machine.mic == MicState.CLOSED
    assert machine.last_checkpoint().cursor == CURSOR_B
    assert "terminated:os_killed" in machine.checkpoint_reasons()


def test_a_terminated_client_refuses_every_transition() -> None:
    machine = started()
    machine.terminate()
    for call in (
        lambda: machine.to_background(),
        lambda: machine.to_foreground(),
        lambda: machine.interrupt(),
        lambda: machine.open_realtime(),
        lambda: machine.start_narration(session_id="n2"),
    ):
        with pytest.raises(MobileError):
            call()
    machine.terminate()  # idempotent, still no error


def test_terminating_without_a_cursor_records_no_checkpoint() -> None:
    machine = MobileLifecycle()
    machine.terminate()
    assert machine.checkpoints == []


# ------------------------------------------------------ session revocation


def test_session_revocation_is_terminal_and_saves_nothing() -> None:
    """A revoked session cannot write to the cloud, so it must not pretend to."""
    machine = started()
    machine.open_realtime()
    machine.advance_narration(CURSOR_B)
    before = len(machine.checkpoints)
    machine.session_revoked()
    assert machine.requires_reauth is True
    assert machine.app_state == AppState.TERMINATED
    assert machine.mic == MicState.CLOSED
    assert len(machine.checkpoints) == before
    assert "session_revoked" in machine.event_kinds()


def test_after_revocation_every_call_says_session_revoked() -> None:
    machine = MobileLifecycle()
    machine.requires_reauth = True
    with pytest.raises(MobileError) as exc:
        machine.start_narration(session_id="n1")
    assert exc.value.error_class == MobileErrorClass.SESSION_REVOKED


# ------------------------------------------------------------- the full arc


def test_the_whole_acceptance_arc_in_order() -> None:
    """foreground -> background -> interrupted (call) -> resumed -> terminated."""
    machine = MobileLifecycle()
    machine.start_narration(session_id="n1", cursor=CURSOR_A)
    machine.open_realtime()
    machine.assistant_start_speaking("özet")
    machine.owner_speech_started("dur")  # barge-in, narration pauses
    machine.start_narration(session_id="n1")  # "devam"
    machine.to_background()  # mic released, audio continues
    machine.advance_narration(CURSOR_B)
    machine.interrupt(reason="phone_call")  # audio session seized
    machine.resume(reason="call_ended")  # back to background, audio back
    assert machine.app_state == AppState.BACKGROUND
    assert machine.playback == Playback.PLAYING
    machine.terminate(reason="owner_swiped_away")

    kinds = machine.event_kinds()
    for expected in (
        "narration_started",
        "mic_opened",
        "owner_speech_started",
        "mic_released",
        "backgrounded",
        "interrupted",
        "resumed",
        "terminated",
    ):
        assert expected in kinds, expected
    # Every state-losing transition left the owner's position recoverable.
    assert machine.checkpoint_reasons() == [
        "paused:barge_in",
        "backgrounded",
        "paused:phone_call",
        "terminated:owner_swiped_away",
    ]
    assert machine.last_checkpoint().cursor == CURSOR_B
    assert machine.snapshot()["app_state"] == "TERMINATED"
