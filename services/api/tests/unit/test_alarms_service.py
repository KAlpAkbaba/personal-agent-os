"""Unit tests: app.alarms.service (M18.3 spec §3.1-§3.5, §8, §11).

The acceptance list of spec §11, as tests:

* an ARMED row fires after a FRESH PROCESS's first tick;
* two ticks / two "processes" / a redelivered firing produce ONE firing and ONE ring;
* a stop, a snooze and a completion release everything they held;
* a test alarm cleans itself up on EVERY terminal state.

SQLite, a fake device port, a manual clock. Nothing sleeps and nothing dials.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.alarms import service as alarms_service
from app.alarms.models import (
    ALARM_ACTIVE_STATES,
    PLAYED_KIND_LOCAL_FALLBACK,
    PLAYED_KIND_TONE_FALLBACK,
    PLAYED_KIND_YOUTUBE,
    STATE_ARMED,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_PLAYING,
    STATE_SCHEDULED,
    STATE_STOPPED,
    TEST_MAX_PLAY_SECONDS,
    WakeAlarm,
)
from app.alarms.sequence import WakeSequence
from app.alarms.state import IllegalAlarmTransition
from app.alarms.tr_time import parse_when_struct, parse_when_text
from app.ledger.models import ActivityEventRow
from app.routines import service as routines_service
from app.routines.models import ROUTINE_STATUS_ARMED, Routine, RoutineFiring
from app.uistate.contract import UiState
from app.uistate.publisher import UiStatePublisher, get_publisher, set_publisher
from tests.alarms_support import (
    FakeDeviceAction,
    build_session_factory,
    failed,
    happy_device_results,
    ok,
)

IST = ZoneInfo("Europe/Istanbul")
#: A Wednesday, 06:00 Istanbul (03:00 UTC).
NOW = datetime(2026, 9, 9, 6, 0, tzinfo=IST).astimezone(UTC)


@pytest.fixture()
def factory():
    return build_session_factory()


@pytest.fixture()
def session(factory):
    with factory() as s:
        yield s


@pytest.fixture()
def device():
    return FakeDeviceAction(results=happy_device_results())


@pytest.fixture()
def sequence(device):
    return WakeSequence(device_action=device, tts=None)


@pytest.fixture(autouse=True)
def _fresh_publisher():
    """``app.uistate.publisher`` is a process-wide singleton (the pattern
    ``test_routines_service.py`` already uses)."""
    set_publisher(UiStatePublisher())
    yield
    set_publisher(UiStatePublisher())


def _states() -> list[str]:
    return [e.state.value for e in get_publisher().tail(limit=200)]


def _aware(dt: datetime) -> datetime:
    """SQLite returns a naive datetime for a ``DateTime(timezone=True)`` column, so a test
    that computes a ``now`` FROM a stored timestamp has to re-attach UTC — the same
    restatement ``app.alarms.service._aware`` makes on the production side."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _create(session, **kwargs) -> WakeAlarm:
    when = kwargs.pop("when", None) or parse_when_text(
        "Yarın sabah 07:30'da beni uyandır.", now=NOW
    )
    return alarms_service.create_alarm(session, when=when, **kwargs)


# ------------------------------------------------------------------------ creation


def test_creating_an_alarm_creates_an_at_routine_carrying_the_wake_alarm_action(session):
    alarm = _create(session)
    assert alarm.state == STATE_SCHEDULED
    assert alarm.local_time == "07:30"
    routine = session.get(Routine, alarm.routine_id)
    assert routine is not None
    assert routine.trigger_kind == "at"
    assert routine.status == ROUTINE_STATUS_ARMED
    assert routine.actions_json == [
        {"kind": "wake_alarm", "detail": {"alarm_id": str(alarm.id)}}
    ]


def test_a_recurring_alarm_gets_a_schedule_routine_in_the_owners_timezone(session):
    when = parse_when_text("Her hafta içi 07:15'te beni uyandır.", now=NOW)
    alarm = _create(session, when=when)
    routine = session.get(Routine, alarm.routine_id)
    assert routine.trigger_kind == "schedule"
    assert routine.trigger_json["weekdays"] == [0, 1, 2, 3, 4]
    assert routine.trigger_json["time"] == "07:15"
    assert routine.trigger_json["timezone"] == "Europe/Istanbul"


def test_a_title_without_a_url_never_resolves_to_media(session):
    """This system does not choose content on the owner's behalf
    (``validate_media_playback``'s rule, applied to an alarm)."""
    alarm = _create(session, media={"title": "Hans Zimmer Time"})
    assert alarm.resolved_media_identity is None
    assert alarm.media_source["kind"] == "remembered"


def test_a_url_resolves_and_round_trips_unchanged(session):
    url = "https://www.youtube.com/watch?v=abc123"
    alarm = _create(session, media={"url": url, "title": "Time"})
    assert alarm.resolved_media_identity == {
        "kind": PLAYED_KIND_YOUTUBE,
        "url": url,
        "title": "Time",
    }


def test_a_test_alarm_gets_the_short_play_bound(session):
    alarm = _create(session, is_test=True)
    assert alarm.is_test is True
    assert alarm.max_play_seconds == TEST_MAX_PLAY_SECONDS


def test_creation_writes_exactly_one_scheduled_ledger_event(session):
    alarm = _create(session)
    rows = [
        r
        for r in session.query(ActivityEventRow).all()
        if r.event_type == "alarm.scheduled" and str(alarm.id) in (r.source_ref or "")
    ]
    assert len(rows) == 1


# -------------------------------------------------------------------------- arming


def test_the_tick_arms_a_scheduled_alarm_once_the_device_acknowledges(session, sequence, device):
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 600}, now=NOW))
    result = alarms_service.tick(session, sequence=sequence, now=NOW)
    session.refresh(alarm)
    assert result.armed == 1
    assert alarm.state == STATE_ARMED
    assert device.count("desktop.alarm_arm") == 1
    payload = device.payload_for("desktop.alarm_arm")
    assert payload["alarm_id"] == str(alarm.id)
    # The companion's flat arm payload (DEVICE_PROTOCOL.md §6f as shipped): the fallback's
    # ramp and duration beside the id and the instant, no nested block.
    assert payload["max_duration_s"] == alarm.max_play_seconds
    assert payload["wake_volume"]["start"] < payload["wake_volume"]["end"]
    assert payload["grace_s"] == 45 and "fallback" not in payload
    assert "alarm.armed" in _states()


def test_an_alarm_stays_scheduled_when_the_device_cannot_be_armed(session, sequence, device):
    """ARMED means the DEVICE acknowledged (spec §3.4) — never "we sent a command"."""
    device.results["desktop.alarm_arm"] = failed("no_capable_device")
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 600}, now=NOW))
    alarms_service.tick(session, sequence=sequence, now=NOW)
    session.refresh(alarm)
    assert alarm.state == STATE_SCHEDULED


def test_arming_is_not_repeated_once_the_device_has_acknowledged(session, sequence, device):
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 600}, now=NOW))
    alarms_service.tick(session, sequence=sequence, now=NOW)
    alarms_service.tick(session, sequence=sequence, now=NOW + timedelta(seconds=10))
    session.refresh(alarm)
    assert alarm.state == STATE_ARMED
    assert device.count("desktop.alarm_arm") == 1


# -------------------------------------------------------------------------- firing


def test_an_armed_row_fires_after_a_fresh_processs_first_tick(factory, device):
    """Spec §11: "a SCHEDULED/ARMED row fires after a fresh process's first tick".

    The alarm is created and armed by one session, which is then CLOSED. A second session
    — standing in for a restarted process that has never seen this alarm in memory — fires
    it from the durable row alone.
    """
    sequence = WakeSequence(device_action=device, tts=None)
    with factory() as first:
        alarm = alarms_service.create_alarm(
            first, when=parse_when_struct({"relative_seconds": 60}, now=NOW)
        )
        alarms_service.tick(first, sequence=sequence, now=NOW)
        alarm_id = alarm.id
    device.reset()

    with factory() as second:
        after = NOW + timedelta(seconds=90)
        decision = alarms_service.fire_alarm(
            second, alarm_id, sequence=sequence, firing_id=uuid.uuid4(), now=after
        )
        assert decision.fired is True
        reloaded = alarms_service.get_alarm(second, alarm_id)
        assert reloaded.state == STATE_PLAYING
    assert "desktop.alarm_start" in device.capabilities_called()


def test_two_ticks_two_processes_and_a_redelivery_produce_one_ring(factory, device):
    """Spec §11's "no duplicate firing" row, in all three of its shapes."""
    sequence = WakeSequence(device_action=device, tts=None)
    firing_id = uuid.uuid4()
    with factory() as first:
        alarm = alarms_service.create_alarm(
            first, when=parse_when_struct({"relative_seconds": 30}, now=NOW)
        )
        alarm_id = alarm.id
    after = NOW + timedelta(seconds=60)

    # 1) the first firing rings.
    with factory() as s1:
        assert alarms_service.fire_alarm(
            s1, alarm_id, sequence=sequence, firing_id=firing_id, now=after
        ).fired
    # 2) a second tick in the same process.
    with factory() as s2:
        second = alarms_service.fire_alarm(
            s2, alarm_id, sequence=sequence, firing_id=uuid.uuid4(), now=after
        )
        assert second.fired is False
        assert second.reason == "already_firing"
    # 3) a redelivered command carrying the ORIGINAL firing id, from a "reconnected" process.
    with factory() as s3:
        third = alarms_service.fire_alarm(
            s3, alarm_id, sequence=sequence, firing_id=firing_id, now=after
        )
        assert third.fired is False

    assert device.count("desktop.alarm_start") == 1


def test_an_alarm_forty_minutes_late_is_not_rung(session, sequence):
    """B13 req 284, with the incident's own number.

    On 2026-09-10 a 07:30 alarm armed the night before rang at 08:10, because the machine
    had slept through the alarm time and woke up to an overdue arm: 39 minutes and 39
    seconds late, into a room where the owner was already awake. The DEVICE was fixed that
    day and the cloud was not, so the cloud would still have done it - it allowed two hours.
    Forty minutes is the number this test uses because forty minutes is what happened.
    """
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 60}, now=NOW))
    forty_minutes_late = NOW + timedelta(minutes=40)

    decision = alarms_service.fire_alarm(
        session, alarm.id, sequence=sequence, firing_id=uuid.uuid4(), now=forty_minutes_late
    )

    assert decision.fired is False
    assert decision.reason == "expired"
    session.refresh(alarm)
    assert alarm.state == STATE_STOPPED
    assert alarm.terminal_reason == "expired_while_down"


def test_an_alarm_two_minutes_late_is_still_rung(session, sequence):
    """The horizon is not zero, and this is why. A slow resume, a busy boot, the cloud
    losing a race - a couple of minutes late still serves the request to be woken AT a
    time, and refusing would turn every scheduling wobble into a missed alarm."""
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 60}, now=NOW))

    decision = alarms_service.fire_alarm(
        session, alarm.id, sequence=sequence, firing_id=uuid.uuid4(),
        now=NOW + timedelta(minutes=2),
    )

    assert decision.fired is True


def test_the_late_horizon_is_the_one_in_the_shared_contract(session, sequence):  # noqa: ARG001
    """Not a number remembered here. The cloud said two hours and the device said five
    minutes for three days, with both suites green - one decision, two memories of the
    answer, which is what `packages/protocol/` exists to stop."""
    import json
    from pathlib import Path

    contract = json.loads(
        (
            Path(__file__).resolve().parents[4] / "packages" / "protocol" / "alarm-timing.json"
        ).read_text(encoding="utf-8")
    )

    assert alarms_service.MAX_LATE_FIRE_S == contract["late_fire"]["max_late_seconds"]


def test_the_grace_stays_well_under_the_late_horizon() -> None:
    """Two different questions, and the ordering between them is load-bearing: a grace as
    long as the horizon would leave the device politely waiting for the cloud at the exact
    moment ringing stopped being worth doing."""
    from app.alarms import timing

    assert timing.cloud_grace_s() < timing.max_late_fire_s()
    assert timing.device_default_grace_s() < timing.max_late_fire_s()


def test_a_cancelled_alarm_never_fires(session, sequence):
    alarm = _create(session)
    alarms_service.cancel_alarm(session, alarm.id, sequence=sequence)
    decision = alarms_service.fire_alarm(
        session, alarm.id, sequence=sequence, firing_id=uuid.uuid4(), now=NOW
    )
    assert decision.fired is False
    assert decision.reason == "alarm_cancelled"


# --------------------------------------------------------------------- the greeting


def _fire_now(session, sequence, alarm, moment):
    return alarms_service.fire_alarm(
        session, alarm.id, sequence=sequence, firing_id=uuid.uuid4(), now=moment
    )


def test_the_greeting_happens_on_a_later_tick_not_inside_the_fire(session, device):
    """Spec §3.5 step 4: the greeting waits for the ramp to finish."""
    from app.voice.providers import FakeTTSProvider

    sequence = WakeSequence(device_action=device, tts=FakeTTSProvider(synthetic_speech=False))
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 5}, now=NOW))
    fired_at = NOW + timedelta(seconds=10)
    _fire_now(session, sequence, alarm, fired_at)
    session.refresh(alarm)
    assert alarm.greeting_due_at is not None
    assert device.count("desktop.play_audio") == 0

    # A tick BEFORE the greeting is due does nothing.
    alarms_service.tick(session, sequence=sequence, now=fired_at + timedelta(seconds=1))
    assert device.count("desktop.play_audio") == 0

    # A tick after it does.
    result = alarms_service.tick(
        session, sequence=sequence, now=_aware(alarm.greeting_due_at) + timedelta(seconds=1)
    )
    assert result.greeted == 1
    assert device.count("desktop.play_audio") == 1
    session.refresh(alarm)
    assert alarm.greeted_at is not None
    assert alarm.state == STATE_PLAYING  # back to playing after the greeting
    assert "alarm.greeting" in _states()


def test_the_greeting_plays_over_the_tone_fallback_too(session, device):
    """Spec §11: "the greeting plays with the tone fallback too"."""
    from app.voice.providers import FakeTTSProvider

    device.results["browser.media_play"] = failed("capability_missing")
    sequence = WakeSequence(device_action=device, tts=FakeTTSProvider(synthetic_speech=False))
    alarm = _create(
        session,
        when=parse_when_struct({"relative_seconds": 5}, now=NOW),
        media={"url": "https://youtube.com/watch?v=x"},
    )
    fired_at = NOW + timedelta(seconds=10)
    _fire_now(session, sequence, alarm, fired_at)
    session.refresh(alarm)
    assert alarm.media_kind == PLAYED_KIND_TONE_FALLBACK

    alarms_service.tick(
        session, sequence=sequence, now=_aware(alarm.greeting_due_at) + timedelta(seconds=1)
    )
    assert device.count("desktop.play_audio") == 1
    # No duck/restore on the tone path: there is no per-stream volume to duck.
    assert device.count("browser.media_volume") == 0


def test_a_greeting_that_cannot_be_synthesised_does_not_stop_the_alarm(session, device):
    sequence = WakeSequence(device_action=device, tts=None)  # no provider at all
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 5}, now=NOW))
    _fire_now(session, sequence, alarm, NOW + timedelta(seconds=10))
    session.refresh(alarm)
    alarms_service.tick(
        session, sequence=sequence, now=_aware(alarm.greeting_due_at) + timedelta(seconds=1)
    )
    session.refresh(alarm)
    assert alarm.state == STATE_PLAYING
    assert alarm.detail_json.get("greeting_failure") == "no_tts_provider"
    assert device.count("desktop.play_audio") == 0


# ------------------------------------------------------------------ owner commands


def test_stop_is_idempotent_and_releases_the_device(session, sequence, device):
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 5}, now=NOW))
    _fire_now(session, sequence, alarm, NOW + timedelta(seconds=10))
    alarms_service.stop_alarm(session, alarm.id, sequence=sequence)
    session.refresh(alarm)
    assert alarm.state == STATE_STOPPED
    assert device.count("desktop.alarm_stop") >= 1
    assert device.count("desktop.alarm_disarm") >= 1

    # ...and a second stop is a calm no-op, not an error.
    again = alarms_service.stop_alarm(session, alarm.id, sequence=sequence)
    assert again.state == STATE_STOPPED


def test_snooze_stops_the_playback_and_arms_a_fresh_one_shot_routine(session, sequence, device):
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 5}, now=NOW))
    fired_at = NOW + timedelta(seconds=10)
    _fire_now(session, sequence, alarm, fired_at)
    first_routine = alarm.routine_id

    alarms_service.snooze_alarm(session, alarm.id, minutes=5, sequence=sequence, now=fired_at)
    session.refresh(alarm)
    assert alarm.state == STATE_ARMED
    assert alarm.snooze_count == 1
    assert alarm.routine_id != first_routine
    assert _aware(alarm.scheduled_for) == fired_at + timedelta(minutes=5)
    assert alarm.last_firing_id is None  # the next firing is a NEW occurrence
    assert device.count("desktop.alarm_stop") >= 1
    assert "alarm.snoozed" in _states()


def test_snooze_is_refused_when_nothing_is_ringing(session, sequence):
    alarm = _create(session)
    with pytest.raises(alarms_service.InvalidAlarmRequest):
        alarms_service.snooze_alarm(session, alarm.id, sequence=sequence)


def test_a_snoozed_alarm_can_fire_again(session, sequence, device):
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 5}, now=NOW))
    fired_at = NOW + timedelta(seconds=10)
    _fire_now(session, sequence, alarm, fired_at)
    alarms_service.snooze_alarm(session, alarm.id, minutes=5, sequence=sequence, now=fired_at)
    device.reset()

    second = alarms_service.fire_alarm(
        session,
        alarm.id,
        sequence=sequence,
        firing_id=uuid.uuid4(),
        now=fired_at + timedelta(minutes=5),
    )
    assert second.fired is True
    assert device.count("desktop.alarm_start") == 1


def test_cancel_refuses_to_resurrect_a_completed_alarm(session, sequence):
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 5}, now=NOW))
    _fire_now(session, sequence, alarm, NOW + timedelta(seconds=10))
    alarms_service.complete_alarm(session, alarm, sequence=sequence)
    with pytest.raises(IllegalAlarmTransition):
        alarms_service.cancel_alarm(session, alarm.id, sequence=sequence)


# --------------------------------------------------------------------- completion


def test_an_alarm_completes_once_it_has_played_for_its_bound(session, sequence, device):
    alarm = _create(
        session, when=parse_when_struct({"relative_seconds": 5}, now=NOW), is_test=True
    )
    fired_at = NOW + timedelta(seconds=10)
    _fire_now(session, sequence, alarm, fired_at)
    session.refresh(alarm)

    # The first tick past the bound speaks the greeting that came due while it played;
    # the NEXT one completes. The order is deliberate: an alarm does not skip its greeting
    # because the play bound happened to arrive in the same tick.
    expired_at = fired_at + timedelta(seconds=TEST_MAX_PLAY_SECONDS + 1)
    alarms_service.tick(session, sequence=sequence, now=expired_at)
    result = alarms_service.tick(
        session, sequence=sequence, now=expired_at + timedelta(seconds=1)
    )
    session.refresh(alarm)
    assert result.completed == 1
    assert alarm.state == STATE_COMPLETED
    assert "alarm.completed" in _states()


@pytest.mark.parametrize("terminal", ["stop", "cancel", "complete"])
def test_a_test_alarm_cleans_itself_up_on_every_terminal_state(
    session, sequence, device, terminal: str
):
    """Spec §8.1 / §11: media session closed, device disarmed, routine resolved,
    ``alarm.cleaned_up`` written — on EVERY terminal state, not just the happy one."""
    alarm = _create(
        session,
        when=parse_when_struct({"relative_seconds": 5}, now=NOW),
        is_test=True,
        media={"url": "https://youtube.com/watch?v=x"},
    )
    routine_id = alarm.routine_id
    if terminal != "cancel":
        _fire_now(session, sequence, alarm, NOW + timedelta(seconds=10))
    device.reset()

    if terminal == "stop":
        alarms_service.stop_alarm(session, alarm.id, sequence=sequence)
    elif terminal == "cancel":
        alarms_service.cancel_alarm(session, alarm.id, sequence=sequence)
    else:
        alarms_service.complete_alarm(session, alarm, sequence=sequence)

    session.refresh(alarm)
    assert alarm.state in (STATE_STOPPED, STATE_CANCELLED, STATE_COMPLETED)
    assert alarm.media_session_id is None
    assert device.count("desktop.alarm_disarm") == 1
    routine = session.get(Routine, routine_id)
    assert routine.status != ROUTINE_STATUS_ARMED
    cleaned = [
        r for r in session.query(ActivityEventRow).all() if r.event_type == "alarm.cleaned_up"
    ]
    assert len(cleaned) == 1


def test_a_youtube_alarm_completing_its_bound_actually_stops_the_media_session(
    session, sequence, device
):
    """Directive 2026-09-08 item F: the restore/stop must happen on the bounded
    ``max_play_seconds`` completion too, proven against a YOUTUBE alarm specifically — the
    existing completion test only ever used the tone, so a media session left open past
    completion (an alarm quietly playing music forever after the app called it COMPLETED)
    would have gone unnoticed."""
    alarm = _create(
        session,
        when=parse_when_struct({"relative_seconds": 5}, now=NOW),
        is_test=True,
        media={"url": "https://youtube.com/watch?v=x"},
    )
    fired_at = NOW + timedelta(seconds=10)
    _fire_now(session, sequence, alarm, fired_at)
    session.refresh(alarm)
    assert alarm.media_kind == PLAYED_KIND_YOUTUBE
    device.reset()

    expired_at = fired_at + timedelta(seconds=TEST_MAX_PLAY_SECONDS + 1)
    alarms_service.tick(session, sequence=sequence, now=expired_at)
    alarms_service.tick(session, sequence=sequence, now=expired_at + timedelta(seconds=1))
    session.refresh(alarm)
    assert alarm.state == STATE_COMPLETED
    assert device.count("browser.media_stop") == 1
    assert alarm.media_session_id is None


def test_snoozing_a_youtube_alarm_stops_the_media_session_not_just_the_row(
    session, sequence, device
):
    """The same fact (item F), through ``alarm.snooze`` rather than the timer: the owner's
    music must not keep playing under a snooze that has already re-armed the alarm for
    later."""
    alarm = _create(
        session,
        when=parse_when_struct({"relative_seconds": 5}, now=NOW),
        media={"url": "https://youtube.com/watch?v=x"},
    )
    fired_at = NOW + timedelta(seconds=10)
    _fire_now(session, sequence, alarm, fired_at)
    session.refresh(alarm)
    assert alarm.media_kind == PLAYED_KIND_YOUTUBE
    device.reset()

    alarms_service.snooze_alarm(session, alarm.id, minutes=5, sequence=sequence, now=fired_at)
    session.refresh(alarm)
    assert alarm.state == STATE_ARMED
    assert device.count("browser.media_stop") == 1
    assert alarm.media_session_id is None


def test_a_snooze_clears_a_stale_media_failure_reason_from_the_previous_ring(
    session, sequence, device
):
    """``GET /v1/alarms/{id}`` must never explain a ring that has not happened yet: a
    media failure recorded on the ring just snoozed away must not still be there once the
    alarm is armed again for later."""
    device.results["browser.media_play"] = failed("challenge")
    alarm = _create(
        session,
        when=parse_when_struct({"relative_seconds": 5}, now=NOW),
        media={"url": "https://youtube.com/watch?v=x"},
    )
    fired_at = NOW + timedelta(seconds=10)
    _fire_now(session, sequence, alarm, fired_at)
    session.refresh(alarm)
    assert alarms_service.alarm_dict(alarm)["media_failure_reason"]

    alarms_service.snooze_alarm(session, alarm.id, minutes=5, sequence=sequence, now=fired_at)
    session.refresh(alarm)
    assert alarms_service.alarm_dict(alarm)["media_failure_reason"] is None


def test_a_recurring_alarm_is_rescheduled_rather_than_left_terminal(session, sequence):
    when = parse_when_text("Her hafta içi 07:15'te beni uyandır.", now=NOW)
    alarm = _create(session, when=when)
    first_moment = alarm.scheduled_for
    _fire_now(session, sequence, alarm, first_moment)
    alarms_service.complete_alarm(session, alarm, sequence=sequence, now=first_moment)
    session.refresh(alarm)
    assert alarm.state == STATE_SCHEDULED
    assert _aware(alarm.scheduled_for) > _aware(first_moment)
    # ...and the schedule routine is still armed: tomorrow still happens.
    routine = session.get(Routine, alarm.routine_id)
    assert routine.status == ROUTINE_STATUS_ARMED


# ------------------------------------------------------------------ reconciliation


def test_a_locally_fired_alarm_is_reconciled_not_rung_again(session, device):
    """Spec §3.6d: the device's own fallback did its job; the cloud records it."""
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 5}, now=NOW))
    touched = alarms_service.reconcile_local_fired(session, [str(alarm.id)], now=NOW)
    session.refresh(alarm)
    assert [a.id for a in touched] == [alarm.id]
    assert alarm.state == STATE_PLAYING
    assert alarm.media_kind == PLAYED_KIND_LOCAL_FALLBACK
    assert device.calls == []  # nothing was asked of the device
    events = {r.event_type for r in session.query(ActivityEventRow).all()}
    assert "alarm.local_fallback_rang" in events


def test_reconciling_an_unknown_or_terminal_alarm_changes_no_state(session, sequence):
    alarm = _create(session)
    alarms_service.cancel_alarm(session, alarm.id, sequence=sequence)
    assert alarms_service.reconcile_local_fired(session, [str(alarm.id)], now=NOW) == []
    assert alarms_service.reconcile_local_fired(session, ["not-a-uuid"], now=NOW) == []
    session.refresh(alarm)
    assert alarm.state == STATE_CANCELLED


def test_a_device_that_rang_after_the_cloud_gave_up_is_recorded_not_lost(session):
    """Owner day plan 2026-09-07 §11: the network is down at the instant.

    The cloud reaches nothing - both audio paths fail, the alarm is FAILED and released -
    while the device's own armed fallback rings on time. When the heartbeat returns and
    names the alarm, the state stays terminal (nothing rings twice, nothing is
    resurrected) but the ledger says the wake-up happened, once.
    """
    unreachable = FakeDeviceAction(
        results={cap: failed("dependency_unavailable") for cap in happy_device_results()}
    )
    sequence = WakeSequence(device_action=unreachable, tts=None)
    alarm = _create(
        session,
        when=parse_when_struct({"relative_seconds": 5}, now=NOW),
        media={"url": "https://youtube.com/watch?v=x"},
    )
    decision = _fire_now(session, sequence, alarm, NOW + timedelta(seconds=10))
    assert decision.fired is False and decision.reason == "audio_failed"
    session.refresh(alarm)
    assert alarm.state == STATE_FAILED

    # The heartbeat comes back and names the alarm the device rang itself - twice, because
    # the device repeats it until the cloud has seen it.
    for _ in range(2):
        touched = alarms_service.reconcile_local_fired(
            session, [str(alarm.id)], now=NOW + timedelta(seconds=90)
        )
        assert touched == []
    session.refresh(alarm)
    assert alarm.state == STATE_FAILED
    rang = [
        r
        for r in session.query(ActivityEventRow).all()
        if r.event_type == "alarm.local_fallback_rang"
    ]
    assert len(rang) == 1
    assert rang[0].detail_json["state_at_reconcile"] == STATE_FAILED
    assert rang[0].detail_json["media_kind"] == PLAYED_KIND_LOCAL_FALLBACK
    assert "yedek alarm" in rang[0].factual_summary
    assert unreachable.count("desktop.alarm_start") == 1  # the cloud tried the tone once


# ------------------------------------------------------------------------ queries


def test_next_alarm_prefers_a_ringing_one_then_the_soonest_pending(session, sequence):
    soon = alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 300}, now=NOW)
    )
    alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 3000}, now=NOW)
    )
    assert alarms_service.next_alarm(session, now=NOW).id == soon.id

    later_id = [a.id for a in alarms_service.list_alarms(session) if a.id != soon.id][0]
    later = alarms_service.get_alarm(session, later_id)
    alarms_service.fire_alarm(
        session,
        later.id,
        sequence=sequence,
        firing_id=uuid.uuid4(),
        now=NOW + timedelta(seconds=3010),
    )
    assert alarms_service.next_alarm(session, now=NOW).id == later.id


def test_status_speech_answers_from_the_record(session):
    assert alarms_service.status_speech(session, now=NOW)[0] == "Kurulu alarm yok efendim."
    _create(session)
    said, alarm = alarms_service.status_speech(session, now=NOW)
    assert alarm is not None
    assert said == "Sabah alarmınız yedi otuzda efendim."


# ------------------------------------------------------- the routine engine seam


def test_the_routine_engine_fires_the_alarm_through_the_wake_alarm_port(factory, device):
    """The whole trigger path end to end, with the routine engine deciding when.

    ``evaluate_due`` -> ``ActionDispatcher`` -> ``WakeAlarmRunner`` -> the sequence.
    """
    from app.alarms.routine_port import WakeAlarmRunner
    from app.routines.actions import DispatchOutcome
    from app.routines.dispatch import ActionDispatcher, BriefingDelivery

    class _NoBriefing:
        def narrate(self, *, text, routine_id, firing_id):
            return BriefingDelivery(False, "not_used")

    sequence = WakeSequence(device_action=device, tts=None)
    dispatcher = ActionDispatcher(
        briefing=_NoBriefing(),
        device_action=device,
        wake_alarm=WakeAlarmRunner(session_factory=factory, sequence=sequence),
    )
    with factory() as session:
        alarm = alarms_service.create_alarm(
            session, when=parse_when_struct({"relative_seconds": 30}, now=NOW)
        )
        alarm_id = alarm.id
        result = routines_service.evaluate_due(
            session, now=NOW + timedelta(seconds=60), dispatcher=dispatcher
        )
        assert len(result.outcomes) == 1
        firing = session.query(RoutineFiring).one()
        assert firing.dispatch_status == "succeeded"
        assert firing.dispatch_results[0]["kind"] == "wake_alarm"
        # The runner ran in ITS OWN session (its module docstring: the sequence's
        # transitions must be durable BEFORE the physical steps that follow them), so this
        # session has to re-read rather than trust its identity map.
        session.expire_all()
        assert alarms_service.get_alarm(session, alarm_id).state == STATE_PLAYING
    assert device.count("desktop.alarm_start") == 1
    assert isinstance(DispatchOutcome.succeeded({}), DispatchOutcome)


def test_a_process_with_no_wake_alarm_port_fails_honestly(factory, device):
    """A dispatcher without the port must not record "the routine fired" for an alarm that
    never rang."""
    from app.routines.dispatch import ActionDispatcher, BriefingDelivery

    class _NoBriefing:
        def narrate(self, *, text, routine_id, firing_id):
            return BriefingDelivery(False, "not_used")

    dispatcher = ActionDispatcher(briefing=_NoBriefing(), device_action=device)
    outcome = dispatcher.dispatch(
        routine_id=uuid.uuid4(),
        firing_id=uuid.uuid4(),
        action={"kind": "wake_alarm", "detail": {"alarm_id": str(uuid.uuid4())}},
    )
    assert outcome.ok is False
    assert outcome.reason == "wake_alarm_port_unavailable"


def test_a_failed_wake_publishes_a_critical_error_state(session, device):
    """Both audio paths failing is the one outcome the owner must hear about as an error
    (spec §3.5 step 3's last line)."""
    device.results["browser.media_play"] = failed("challenge")
    device.results["desktop.alarm_start"] = failed("no_capable_device")
    sequence = WakeSequence(device_action=device, tts=None)
    alarm = _create(
        session,
        when=parse_when_struct({"relative_seconds": 5}, now=NOW),
        media={"url": "https://youtube.com/watch?v=x"},
    )
    decision = alarms_service.fire_alarm(
        session,
        alarm.id,
        sequence=sequence,
        firing_id=uuid.uuid4(),
        now=NOW + timedelta(seconds=10),
    )
    session.refresh(alarm)
    assert decision.fired is False
    assert alarm.state == STATE_FAILED
    published = _states()
    assert "alarm.failed" in published
    assert UiState.ERROR.value in published


def test_the_alarm_never_depends_on_a_realtime_session_or_presence(session, device):
    """Spec §11: the sequence has no presence/eye dependency and needs no voice session.

    There is no realtime session table in this suite's schema and no presence engine
    observation anywhere in it — a sequence that touched either would fail here rather
    than in production at 07:30.
    """
    from app.voice.providers import FakeTTSProvider

    sequence = WakeSequence(device_action=device, tts=FakeTTSProvider(synthetic_speech=False))
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 5}, now=NOW))
    fired_at = NOW + timedelta(seconds=10)
    assert _fire_now(session, sequence, alarm, fired_at).fired
    session.refresh(alarm)
    alarms_service.tick(
        session, sequence=sequence, now=_aware(alarm.greeting_due_at) + timedelta(seconds=1)
    )
    session.refresh(alarm)
    assert alarm.greeted_at is not None


def test_display_wake_failure_does_not_stop_the_audio(session, device):
    """Spec §1.5 / §11: a dark screen is not a reason for a silent alarm."""
    device.results["desktop.display_wake"] = failed("capability_missing")
    sequence = WakeSequence(device_action=device, tts=None)
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 5}, now=NOW))
    decision = alarms_service.fire_alarm(
        session,
        alarm.id,
        sequence=sequence,
        firing_id=uuid.uuid4(),
        now=NOW + timedelta(seconds=10),
    )
    session.refresh(alarm)
    assert decision.fired is True
    assert alarm.state == STATE_PLAYING
    assert device.count("desktop.alarm_start") == 1


def test_the_device_is_disarmed_before_anything_else_happens(session, device):
    """Spec §3.5 step 1: disarm FIRST, so a cloud ring and a local fallback ring can never
    both happen."""
    sequence = WakeSequence(device_action=device, tts=None)
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 5}, now=NOW))
    alarms_service.fire_alarm(
        session,
        alarm.id,
        sequence=sequence,
        firing_id=uuid.uuid4(),
        now=NOW + timedelta(seconds=10),
    )
    called = device.capabilities_called()
    assert called[0] == "desktop.alarm_disarm"
    assert called.index("desktop.alarm_disarm") < called.index("desktop.alarm_start")


def test_a_failed_disarm_does_not_stop_the_sequence(session, device):
    """An unreachable device that still rings its own fallback is the behaviour this
    milestone wants, not a fault to abort on (spec §3.5 step 1)."""
    device.results["desktop.alarm_disarm"] = failed("device_offline")
    sequence = WakeSequence(device_action=device, tts=None)
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 5}, now=NOW))
    assert alarms_service.fire_alarm(
        session,
        alarm.id,
        sequence=sequence,
        firing_id=uuid.uuid4(),
        now=NOW + timedelta(seconds=10),
    ).fired


def test_every_transition_writes_exactly_one_ledger_event(session, device):
    sequence = WakeSequence(device_action=device, tts=None)
    alarm = _create(session, when=parse_when_struct({"relative_seconds": 5}, now=NOW))
    moment = NOW + timedelta(seconds=10)
    _fire_now(session, sequence, alarm, moment)
    # Fire again (a duplicate tick) — no second set of rows.
    alarms_service.fire_alarm(
        session, alarm.id, sequence=sequence, firing_id=uuid.uuid4(), now=moment
    )
    rows = [
        r
        for r in session.query(ActivityEventRow).all()
        if r.event_type.startswith("alarm.") and f"alarms:{alarm.id}" in (r.source_ref or "")
    ]
    refs = [r.source_ref for r in rows]
    assert len(refs) == len(set(refs)), refs


def test_transition_refuses_an_illegal_move(session):
    alarm = _create(session)
    with pytest.raises(IllegalAlarmTransition):
        alarms_service.transition(session, alarm, STATE_COMPLETED, now=NOW)


def test_ok_helper_is_a_verified_read_back(device):
    """A guard on the fixture itself: an unscripted capability must not look verified."""
    assert ok(armed=True).result == {"armed": True}
    plain = device.run(
        capability="desktop.unknown_thing", payload={}, idempotency_key="k", timeout_s=1.0
    )
    assert plain.ok is True
    assert plain.result == {}


def test_the_engine_and_the_alarm_judge_the_same_moment(factory, device) -> None:
    """One decision, one clock.

    `evaluate_due(now=...)` decides an alarm is DUE at some moment; `fire_alarm` then
    decides whether it is TOO LATE (`MAX_LATE_FIRE_S`). Those have to be the same moment.
    `WakeAlarmRunner.fire` took no `now` and passed none, so the second decision was made
    against the wall clock while the first used the engine's - two clocks for one decision.

    In production they agree to within milliseconds, which is exactly why it hid. The case
    where they do NOT is the case the lateness bound exists for: a catch-up tick replaying a
    firing that came due while the Cloud Core was down. Here that case is made explicit -
    the engine's clock is hours behind the wall clock, and the alarm must still fire,
    because by the ENGINE's reckoning it is only thirty seconds late.

    This is also the shape that broke the suite: this file's fixtures are pinned to
    2026-09-09T03:00Z, so the old code passed only while real time stayed within
    MAX_LATE_FIRE_S of that and began failing once it drifted past - a wall-clock time bomb
    that reads as a flake.
    """
    from app.alarms.routine_port import WakeAlarmRunner
    from app.routines.dispatch import ActionDispatcher, BriefingDelivery

    class _NoBriefing:
        def narrate(self, *, text, routine_id, firing_id):
            return BriefingDelivery(False, "not_used")

    sequence = WakeSequence(device_action=device, tts=None)
    dispatcher = ActionDispatcher(
        briefing=_NoBriefing(),
        device_action=device,
        wake_alarm=WakeAlarmRunner(session_factory=factory, sequence=sequence),
    )
    with factory() as session:
        alarm = alarms_service.create_alarm(
            session, when=parse_when_struct({"relative_seconds": 30}, now=NOW)
        )
        result = routines_service.evaluate_due(
            session, now=NOW + timedelta(seconds=60), dispatcher=dispatcher
        )
        firing = session.query(RoutineFiring).one()

    assert len(result.outcomes) == 1
    assert firing.dispatch_status == "succeeded", (
        "the alarm judged its own lateness on a different clock from the engine that "
        "decided it was due - so a catch-up firing expires instead of ringing"
    )
    assert firing.dispatch_results[0]["kind"] == "wake_alarm"
    del alarm


# ------------------------------------------------ B13 req 259: snooze has an end


def test_a_wake_up_cannot_be_deferred_for_ever(session, sequence):
    """There was no cap. An alarm could be snoozed indefinitely and never reached a terminal
    state, so a wake-up nobody ever got up for stayed live in the row, in the world model and
    on the device's arm for as long as somebody kept pressing."""
    alarm = _create(session)
    # The clock moves with the snoozes, because a snooze schedules the alarm five minutes
    # from the moment it was pressed: firing at a stale `now` would be refused as expired
    # (B13 req 284) and the loop would prove nothing about the cap.
    moment = NOW
    alarms_service.fire_alarm(
        session, alarm.id, sequence=sequence, firing_id=uuid.uuid4(), now=moment
    )

    for _ in range(alarms_service.MAX_SNOOZE_COUNT):
        alarms_service.snooze_alarm(session, alarm.id, minutes=5, sequence=sequence, now=moment)
        moment += timedelta(minutes=5)
        alarms_service.fire_alarm(
            session, alarm.id, sequence=sequence, firing_id=uuid.uuid4(), now=moment
        )

    with pytest.raises(alarms_service.InvalidAlarmRequest) as refused:
        alarms_service.snooze_alarm(session, alarm.id, minutes=5, sequence=sequence, now=moment)

    assert str(alarms_service.MAX_SNOOZE_COUNT) in str(refused.value)


def test_the_refusal_leaves_the_alarm_ringing_rather_than_going_quiet(session, sequence):
    """A cap that silenced the alarm would be worse than no cap: the owner would have
    snoozed once more and simply not been woken."""
    alarm = _create(session)
    alarms_service.fire_alarm(session, alarm.id, sequence=sequence, firing_id=uuid.uuid4(), now=NOW)
    alarm.snooze_count = alarms_service.MAX_SNOOZE_COUNT
    session.commit()

    with pytest.raises(alarms_service.InvalidAlarmRequest):
        alarms_service.snooze_alarm(session, alarm.id, minutes=5, sequence=sequence, now=NOW)

    session.refresh(alarm)
    assert alarm.state in ALARM_ACTIVE_STATES, "the alarm is still physically happening"


def test_a_fresh_alarm_is_not_born_at_the_limit(session, sequence):
    alarm = _create(session)
    alarms_service.fire_alarm(session, alarm.id, sequence=sequence, firing_id=uuid.uuid4(), now=NOW)

    snoozed = alarms_service.snooze_alarm(session, alarm.id, minutes=5, sequence=sequence, now=NOW)

    assert snoozed.snooze_count == 1
