"""Unit tests: app.alarms.sequence (M18.3 spec §3.5, §11).

The physical steps and their receipts, with a fake device port. Specifically:

* media failure of EVERY kind -> the tone fallback, with the failure receipt;
* both audio paths failing -> FAILED;
* a display-wake failure never stops the audio;
* the greeting ducks and restores on the media path and does neither on the tone;
* every device call this module makes has a receipt with a ledger row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_FAILED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_UNVERIFIED,
    TERMINAL_VERIFIED,
)
from app.alarms import service as alarms_service
from app.alarms.models import (
    PLAYED_KIND_TONE_FALLBACK,
    PLAYED_KIND_YOUTUBE,
    STATE_FAILED,
    STATE_PLAYING,
)
from app.alarms.sequence import (
    ALARM_BROWSER_PROFILE,
    ALARM_BROWSER_SESSION_KIND,
    MEDIA_VERIFY_SECONDS,
    RECEIPT_BY_DEVICE_CALL,
    WakeSequence,
    media_session_id,
)
from app.alarms.tr_time import parse_when_struct
from app.ledger.models import ActivityEventRow
from app.voice.providers import FakeTTSProvider
from tests.alarms_support import (
    FakeDeviceAction,
    build_session_factory,
    failed,
    happy_device_results,
    ok,
    refused,
)

NOW = datetime(2026, 9, 9, 4, 0, tzinfo=UTC)
FIRED_AT = NOW + timedelta(seconds=30)
MEDIA_URL = "https://www.youtube.com/watch?v=abcdefg"


@pytest.fixture()
def session():
    with build_session_factory()() as s:
        yield s


@pytest.fixture()
def device():
    return FakeDeviceAction(results=happy_device_results())


def _alarm(session, *, media: dict | None = None, **kwargs):
    return alarms_service.create_alarm(
        session,
        when=parse_when_struct({"relative_seconds": 30}, now=NOW),
        media=media,
        **kwargs,
    )


def _fire(session, device, alarm, *, tts=None):
    sequence = WakeSequence(device_action=device, tts=tts)
    return sequence, sequence.fire(
        session,
        alarm,
        firing_id=uuid.uuid4(),
        now=FIRED_AT,
        transition=alarms_service.transition,
    )


def _receipt_rows(session) -> list[ActivityEventRow]:
    return [r for r in session.query(ActivityEventRow).all() if r.event_type == "action.receipt"]


# ---------------------------------------------------------------------- media path


def test_the_media_path_opens_the_dedicated_alarm_profile_and_ramps(session, device):
    alarm = _alarm(session, media={"url": MEDIA_URL})
    _, result = _fire(session, device, alarm)
    assert result.state == STATE_PLAYING
    assert result.media_kind == PLAYED_KIND_YOUTUBE

    open_payload = device.payload_for("browser.session_open")
    assert open_payload["profile"] == ALARM_BROWSER_PROFILE
    assert open_payload["session_kind"] == ALARM_BROWSER_SESSION_KIND
    assert open_payload["session_id"] == media_session_id(alarm.id)

    play_payload = device.payload_for("browser.media_play")
    assert play_payload["url"] == MEDIA_URL  # byte for byte, never rewritten
    assert play_payload["volume"] == 0.15  # the ramp START, not the end
    assert play_payload["verify_seconds"] == MEDIA_VERIFY_SECONDS

    ramp_payload = device.payload_for("browser.media_volume")
    assert ramp_payload["level"] == 0.6
    assert ramp_payload["ramp_seconds"] == 20
    assert device.count("desktop.alarm_start") == 0  # no tone alongside the music


def test_an_alarm_with_no_media_goes_straight_to_the_tone_without_a_media_receipt(session, device):
    """An alarm the owner set with the tone has no media step to fail, and inventing a
    failed receipt for a thing nobody asked for would be its own kind of lie."""
    alarm = _alarm(session)
    _, result = _fire(session, device, alarm)
    assert result.media_kind == PLAYED_KIND_TONE_FALLBACK
    assert device.count("browser.session_open") == 0
    capabilities = {r.action for r in _receipt_rows(session)}
    assert "media.play" not in capabilities


@pytest.mark.parametrize(
    "media_result",
    [
        pytest.param(ok(playing=False, verified=False, reason="challenge"), id="challenge"),
        pytest.param(
            ok(playing=False, verified=False, reason="autoplay_blocked"), id="autoplay_blocked"
        ),
        pytest.param(
            ok(playing=False, verified=False, reason="no_media_element"), id="no_media_element"
        ),
        pytest.param(ok(playing=False, verified=False, reason="consent_wall"), id="consent_wall"),
        pytest.param(failed("capability_missing"), id="capability_missing"),
        pytest.param(failed("timeout"), id="timeout"),
    ],
)
def test_every_media_failure_falls_back_to_the_tone_with_a_failure_receipt(
    session, device, media_result
):
    """Spec §11: "YouTube failure (challenge, no media element, autoplay blocked) -> tone
    fallback with the failure receipt".

    ``verified`` is the browser's own read-back that ``currentTime`` actually advanced —
    NOT that ``play()`` was called. An unverified play is a failed play here, because a
    silent tab is indistinguishable from a broken alarm to a sleeping owner.
    """
    device.results["browser.media_play"] = media_result
    alarm = _alarm(session, media={"url": MEDIA_URL})
    _, result = _fire(session, device, alarm)

    assert result.state == STATE_PLAYING
    assert result.media_kind == PLAYED_KIND_TONE_FALLBACK
    assert device.count("desktop.alarm_start") == 1

    media_receipts = [r for r in _receipt_rows(session) if r.action == "media.play"]
    assert media_receipts, "the media failure must be recorded, not silently swallowed"
    assert any(r.status in (TERMINAL_FAILED, TERMINAL_UNVERIFIED) for r in media_receipts)


def test_a_media_session_that_cannot_be_opened_falls_back_without_trying_to_play(session, device):
    device.results["browser.session_open"] = failed("browser_lifecycle_violation")
    alarm = _alarm(session, media={"url": MEDIA_URL})
    _, result = _fire(session, device, alarm)
    assert result.media_kind == PLAYED_KIND_TONE_FALLBACK
    assert device.count("browser.media_play") == 0


def test_a_failed_ramp_leaves_the_media_playing_at_the_start_volume(session, device):
    """The ramp is a refinement of a ring that is already audible: its failure is a
    quieter alarm, not a fallback to a second sound source on top of the music."""
    device.results["browser.media_volume"] = failed("timeout")
    alarm = _alarm(session, media={"url": MEDIA_URL})
    _, result = _fire(session, device, alarm)
    assert result.media_kind == PLAYED_KIND_YOUTUBE
    assert device.count("desktop.alarm_start") == 0


def test_both_audio_paths_failing_is_the_only_failed_outcome(session, device):
    device.results["browser.media_play"] = failed("challenge")
    device.results["desktop.alarm_start"] = failed("no_capable_device")
    alarm = _alarm(session, media={"url": MEDIA_URL})
    _, result = _fire(session, device, alarm)
    assert result.state == STATE_FAILED
    assert result.media_kind is None
    assert result.reason


# ------------------------------------------------- the invariant: not a beep by default


def test_a_normal_alarm_with_no_named_media_plays_the_approved_wake_song_not_a_beep(
    session, device
):
    """The owner's own words (2026-09-08): "Owner-selected YouTube music is PRIMARY; the
    local tone is EMERGENCY FALLBACK ONLY." With an approved, playable wake song and no
    media named on THIS alarm, ``fire()`` through a fake browser worker that succeeds must
    reach the music, never the beep — asserted at the resolution layer in
    ``test_alarms_wake_song.py`` and here again through the real physical sequence."""
    alarms_service.set_wake_song(session, url=MEDIA_URL, title="Sabah Şarkısı")
    alarm = _alarm(session)  # media=None: exactly "Yarın 07:30'da beni uyandır."
    _, result = _fire(session, device, alarm)
    assert result.state == STATE_PLAYING
    assert result.media_kind == PLAYED_KIND_YOUTUBE
    play_payload = device.payload_for("browser.media_play")
    assert play_payload["url"] == MEDIA_URL
    assert device.count("desktop.alarm_start") == 0  # never the tone alongside it


def test_a_normal_alarm_with_no_named_media_and_no_approved_song_still_rings_the_tone(
    session, device
):
    """The mirror: nothing named, nothing approved — the tone, and the run does not
    fail."""
    alarm = _alarm(session)
    _, result = _fire(session, device, alarm)
    assert result.state == STATE_PLAYING
    assert result.media_kind == PLAYED_KIND_TONE_FALLBACK
    assert device.count("browser.session_open") == 0


# ------------------------------------------------------- never silently fall back (C)


def test_a_media_failure_records_a_durable_readable_reason(session, device):
    """Directive 2026-09-08 item C: "Do not silently fall back. Record why." Falling back
    to the tone is fine; the owner must be able to answer "neden zil çaldı?" from
    ``GET /v1/alarms/{id}`` alone, not by reading receipts out of the ledger."""
    device.results["browser.media_play"] = ok(playing=False, verified=False, reason="challenge")
    alarm = _alarm(session, media={"url": MEDIA_URL})
    _, result = _fire(session, device, alarm)
    assert result.media_kind == PLAYED_KIND_TONE_FALLBACK
    assert alarm.detail_json.get("media_failure_reason")
    body = alarms_service.alarm_dict(alarm)
    assert body["media_failure_reason"] == alarm.detail_json["media_failure_reason"]
    assert "challenge" in body["media_failure_reason"]


def test_an_alarm_with_no_media_requested_records_no_failure_reason(session, device):
    """The mirror: an alarm that never asked for media did not "fail" to play it — there
    is nothing to explain, and inventing a reason would be its own kind of lie (the same
    rule the no-media-receipt test above already states for the receipt itself)."""
    alarm = _alarm(session)
    _, result = _fire(session, device, alarm)
    assert result.media_kind == PLAYED_KIND_TONE_FALLBACK
    assert alarms_service.alarm_dict(alarm)["media_failure_reason"] is None


# ------------------------------------------------------- verify playback, not a guess (D)


def test_a_session_that_opens_and_reports_playing_but_not_verified_still_falls_back(
    session, device
):
    """Directive 2026-09-08 item D: ``browser.session_open`` succeeding is NOT proof that
    music is playing, and neither is the browser merely claiming ``playing: true`` —
    ``verified`` (that ``currentTime`` genuinely advanced) is the one signal this module
    trusts. A tab that opened fine and even claims to be playing, but was never verified,
    is treated exactly like an outright failure."""
    device.results["browser.session_open"] = ok(opened=True)
    device.results["browser.media_play"] = ok(playing=True, verified=False)
    alarm = _alarm(session, media={"url": MEDIA_URL})
    _, result = _fire(session, device, alarm)
    assert device.count("browser.session_open") == 1  # the session DID open
    assert result.media_kind == PLAYED_KIND_TONE_FALLBACK  # but that proves nothing
    assert device.count("desktop.alarm_start") == 1


# ------------------------------------------------------------ owned context only (E)


def test_stop_playback_only_ever_addresses_this_alarms_own_media_session(session, device):
    """Directive 2026-09-08 item E: never hijack, pause or close an unrelated tab. Two
    alarms, two distinct dedicated sessions (``media_session_id`` is ``alarm-<alarm_id>``,
    spec §4) — stopping one must name only its own session, never the other's."""
    other_url = "https://www.youtube.com/watch?v=zzzzzzz"
    alarm_a = _alarm(session, media={"url": MEDIA_URL})
    alarm_b = _alarm(session, media={"url": other_url})
    sequence = WakeSequence(device_action=device, tts=None)
    sequence.fire(
        session, alarm_a, firing_id=uuid.uuid4(), now=FIRED_AT, transition=alarms_service.transition
    )
    sequence.fire(
        session, alarm_b, firing_id=uuid.uuid4(), now=FIRED_AT, transition=alarms_service.transition
    )
    assert alarm_a.media_session_id != alarm_b.media_session_id
    device.reset()
    sequence.stop_playback(session, alarm_a, reason="owner", now=FIRED_AT + timedelta(seconds=60))
    stop_payload = device.payload_for("browser.media_stop")
    assert stop_payload["session_id"] == media_session_id(alarm_a.id)
    assert stop_payload["session_id"] != media_session_id(alarm_b.id)
    assert device.count("browser.media_stop") == 1  # only the one session touched


def test_the_media_session_is_always_opened_in_the_dedicated_alarm_profile(session, device):
    """Never the research profile, never the owner's own Chrome (spec §4)."""
    alarm = _alarm(session, media={"url": MEDIA_URL})
    _fire(session, device, alarm)
    open_payload = device.payload_for("browser.session_open")
    assert open_payload["profile"] == ALARM_BROWSER_PROFILE == "alarm"
    assert open_payload["session_kind"] == ALARM_BROWSER_SESSION_KIND


# ---------------------------------------------------------------------- the display


def test_display_wake_failure_is_recorded_and_the_sequence_continues(session, device):
    device.results["desktop.display_wake"] = failed("capability_missing")
    alarm = _alarm(session)
    _, result = _fire(session, device, alarm)
    assert result.state == STATE_PLAYING
    wake_receipts = [r for r in _receipt_rows(session) if r.action == "display.wake"]
    assert len(wake_receipts) == 1
    assert wake_receipts[0].status == TERMINAL_FAILED


def test_display_wake_is_skipped_when_the_owner_turned_it_off(session, device):
    alarm = _alarm(session, display_wake_policy={"enabled": False})
    _fire(session, device, alarm)
    assert device.count("desktop.display_wake") == 0


def test_a_display_wake_without_a_read_back_is_unverified_not_verified(session, device):
    """docs/M18_ACTION_CONTRACT.md §1: a physical change is not "verified" because a
    transport call returned 200."""
    device.results["desktop.display_wake"] = ok()  # succeeded, but said nothing
    alarm = _alarm(session)
    _fire(session, device, alarm)
    wake = [r for r in _receipt_rows(session) if r.action == "display.wake"][0]
    assert wake.status == TERMINAL_UNVERIFIED


def test_a_device_refusal_is_refused_not_failed(session, device):
    """Spec §5.1: the companion answering "no, the owner just used the keyboard" is a
    SUCCESSFUL command carrying a refusal."""
    device.results["desktop.display_off"] = refused("recent_input", input_idle_s=3)
    sequence = WakeSequence(device_action=device, tts=None)
    step = sequence.display_off(session, reason="owner_away", now=NOW)
    assert step.ok is False
    assert step.reason == "recent_input"
    assert step.receipt.execution_status == EXECUTION_REFUSED
    assert step.receipt.error_class == "recent_input"


def test_display_off_verifies_from_the_devices_own_read_back(session, device):
    sequence = WakeSequence(device_action=device, tts=None)
    step = sequence.display_off(session, reason="owner_away", now=NOW)
    assert step.receipt.terminal_status == TERMINAL_VERIFIED
    assert step.receipt.execution_status == EXECUTION_EXECUTED
    assert step.receipt.observed_after["local"]["observed_state"] == "off"


# ---------------------------------------------------------------------- the greeting


def test_the_greeting_ducks_and_restores_on_the_media_path(session, device):
    alarm = _alarm(session, media={"url": MEDIA_URL})
    sequence, _ = _fire(session, device, alarm, tts=FakeTTSProvider())
    device.reset()
    sequence.speak_greeting(
        session,
        alarm,
        local_now=datetime(2026, 9, 9, 7, 30, tzinfo=UTC),
        now=FIRED_AT + timedelta(seconds=30),
        transition=alarms_service.transition,
    )
    volumes = [
        c["payload"]["level"] for c in device.calls if c["capability"] == "browser.media_volume"
    ]
    assert volumes == [0.15, 0.6]  # duck to the duck_level, then restore to the ramp end
    assert device.count("desktop.play_audio") == 1


def test_the_greeting_does_not_duck_the_tone(session, device):
    """There is no per-stream volume the cloud can address for the local tone, so the
    greeting plays over it at its own level (spec §3.5 step 4's parenthesis)."""
    alarm = _alarm(session)
    sequence, _ = _fire(session, device, alarm, tts=FakeTTSProvider())
    device.reset()
    sequence.speak_greeting(
        session,
        alarm,
        local_now=datetime(2026, 9, 9, 7, 30, tzinfo=UTC),
        now=FIRED_AT + timedelta(seconds=30),
        transition=alarms_service.transition,
    )
    assert device.count("browser.media_volume") == 0
    assert device.count("desktop.play_audio") == 1


def test_the_greeting_payload_carries_a_one_time_token_and_its_hash(session, device):
    from app.alarms.audio_store import AudioStore

    store = AudioStore()
    sequence = WakeSequence(
        device_action=device,
        tts=FakeTTSProvider(),
        audio_store=store,
        broker_audio_origin="https://core.example",
    )
    alarm = _alarm(session)
    sequence.fire(
        session, alarm, firing_id=uuid.uuid4(), now=FIRED_AT, transition=alarms_service.transition
    )
    sequence.speak_greeting(
        session,
        alarm,
        local_now=datetime(2026, 9, 9, 7, 30, tzinfo=UTC),
        now=FIRED_AT + timedelta(seconds=30),
        transition=alarms_service.transition,
    )
    payload = device.payload_for("desktop.play_audio")
    assert payload["audio"]["url"].startswith("https://core.example/v1/alarms/audio/")
    assert len(payload["audio"]["sha256"]) == 64
    assert payload["audio"]["format"] == "wav"
    assert payload["level"] == 0.75
    assert payload["max_seconds"] == 15
    # ...and the token is really redeemable, once.
    #
    # Redeemed on the FIXTURE's clock, not the wall clock. This test used to call
    # `take(token)` with no `now`, so the store compared a 2026-09-09T04:01Z entry against
    # whatever time it actually was - and the entry's five-minute TTL meant the test could
    # only pass between 04:00 and 04:06 UTC on one particular day. It passed for weeks and
    # then failed in CI at 04:31Z, looking exactly like a flake and being nothing of the
    # kind: the PRODUCT was right (the TTL works), the test was reading a different clock
    # from the one it set up. Every other time in this file is the fixture's; this is now
    # too.
    token = payload["audio"]["url"].rsplit("/", 1)[-1]
    redeemed_at = FIRED_AT + timedelta(seconds=45)
    assert store.take(token, now=redeemed_at) is not None
    assert store.take(token, now=redeemed_at) is None


def test_a_custom_greeting_text_is_used_verbatim_after_normalisation(session, device):
    alarm = _alarm(session, greeting_policy={"enabled": True, "text": "Kalk bakalım."})
    sequence, _ = _fire(session, device, alarm, tts=FakeTTSProvider())
    sequence.speak_greeting(
        session,
        alarm,
        local_now=datetime(2026, 9, 9, 7, 30, tzinfo=UTC),
        now=FIRED_AT + timedelta(seconds=30),
        transition=alarms_service.transition,
    )
    assert device.count("desktop.play_audio") == 1


# ------------------------------------------------------------------- the receipts


def test_every_device_call_in_a_full_sequence_writes_a_receipt(session, device):
    """Spec §11's "every physical action has a receipt", as a count rather than a spot check."""
    alarm = _alarm(session, media={"url": MEDIA_URL})
    sequence, _ = _fire(session, device, alarm, tts=FakeTTSProvider())
    sequence.speak_greeting(
        session,
        alarm,
        local_now=datetime(2026, 9, 9, 7, 30, tzinfo=UTC),
        now=FIRED_AT + timedelta(seconds=30),
        transition=alarms_service.transition,
    )
    sequence.stop_playback(session, alarm, reason="owner", now=FIRED_AT + timedelta(seconds=60))

    receipt_actions = {r.action for r in _receipt_rows(session)}
    expected = {RECEIPT_BY_DEVICE_CALL[c] for c in device.capabilities_called()}
    assert expected <= receipt_actions, expected - receipt_actions


def test_a_receipt_carries_the_devices_own_read_back_verbatim(session, device):
    alarm = _alarm(session)
    sequence, result = _fire(session, device, alarm)
    tone = next(s for s in result.steps if s.capability == "desktop.alarm_start")
    assert tone.receipt.observed_after["local"] == {"started": True}
    assert tone.receipt.observed_after["server"]["alarm_id"] == str(alarm.id)
    assert {"kind": "wake_alarm", "ref": str(alarm.id)} in tone.receipt.evidence_refs


def test_a_failed_step_records_execution_failed_with_its_error_class(session, device):
    device.results["desktop.alarm_start"] = failed("no_capable_device", "hiçbir cihaz yok")
    alarm = _alarm(session)
    _, result = _fire(session, device, alarm)
    tone = next(s for s in result.steps if s.capability == "desktop.alarm_start")
    assert tone.receipt.execution_status == EXECUTION_FAILED
    assert tone.receipt.terminal_status == TERMINAL_FAILED
    assert tone.receipt.error_class == "no_capable_device"


def test_stopping_while_the_alarm_is_in_the_greeting_state_still_stops_the_media(session, device):
    """Directive 2026-09-08 item F: the restore must happen on stop, not only when the
    greeting finishes on its own. ``stop_playback`` reads ``alarm.media_kind`` /
    ``media_session_id`` — never ``alarm.state`` — so it stops the SAME media whether the
    owner speaks while the alarm is PLAYING or mid-GREETING (``speak_greeting`` writes the
    GREETING state before it ducks, so that window is real in production even though this
    synchronous fake device cannot race it). This pins the state-independence down against
    a future change that made ``stop_playback`` branch on state by accident."""
    alarm = _alarm(session, media={"url": MEDIA_URL})
    sequence, _ = _fire(session, device, alarm)
    from app.alarms.models import STATE_GREETING

    alarms_service.transition(session, alarm, STATE_GREETING, now=FIRED_AT + timedelta(seconds=20))
    assert alarm.state == STATE_GREETING
    device.reset()
    sequence.stop_playback(session, alarm, reason="owner", now=FIRED_AT + timedelta(seconds=25))
    assert device.count("browser.media_stop") == 1
    assert alarm.media_session_id is None


def test_stop_playback_stops_the_medium_that_is_actually_playing(session, device):
    alarm = _alarm(session, media={"url": MEDIA_URL})
    sequence, _ = _fire(session, device, alarm)
    device.reset()
    sequence.stop_playback(session, alarm, reason="owner", now=FIRED_AT + timedelta(seconds=60))
    assert device.count("browser.media_stop") == 1
    assert alarm.media_session_id is None


def test_no_assertion_in_this_file_reads_the_wall_clock() -> None:
    """A time bomb that ticks for weeks and then looks like a flake.

    `test_the_greeting_payload_carries_a_one_time_token_and_its_hash` called
    `store.take(token)` with no `now`, so the store compared an entry stamped at the
    fixture's 2026-09-09T04:01Z against whatever time it really was. The entry's TTL is five
    minutes, so the test could only pass between 04:00 and 04:06 UTC on one particular day.
    It passed locally at 04:0x and failed in CI at 04:31Z - and a failure that depends on
    the hour reads as a flake, which is how this class survives.

    Every clock in this file is the fixture's. `AudioStore.put`/`take` both accept `now`,
    and a call here that omits it is reaching for a different clock from the one the test
    set up.
    """
    import re
    from pathlib import Path

    source = Path(__file__).read_text(encoding="utf-8")
    body = source[: source.index("def test_no_assertion_in_this_file_reads_the_wall_clock")]

    clockless = [
        call for call in re.findall(r"\bstore\.(?:take|put)\([^)]*\)", body) if "now=" not in call
    ]
    assert not clockless, (
        f"{clockless} read the wall clock while every fixture time is 2026-09-09T04:00Z - "
        f"this test can then only pass inside the TTL window of that one moment"
    )

    # And the detector is proven to bite, so it cannot pass against a file that lost the
    # discipline: a synthetic clockless call must be caught.
    assert [
        call
        for call in re.findall(r"\bstore\.(?:take|put)\([^)]*\)", "store.take(token)")
        if "now=" not in call
    ] == ["store.take(token)"]
