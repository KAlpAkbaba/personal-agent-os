"""The ambient display scenario matrix (owner day plan 2026-09-07 §13, ADR-0079).

Every scenario the owner listed, run through the REAL policy layer: ``decide`` on inputs
that ``collect_inputs`` assembled from a real ``PresenceFusionEngine`` fed with structured
camera observations, the real status registry fed with heartbeats through ``ingest_status``,
the real holdoff registry, and the real alarm service for the alarm rows. The only fake is
the device (``tests.alarms_support.FakeDeviceAction``). Nothing sleeps: every scenario says
what time it is.

    PRESENT + input                 -> ON
    AWAY short                      -> ON
    AWAY sustained                  -> OFF eligible
    UNKNOWN                         -> ON
    camera failed                   -> ON
    likely asleep short             -> ON
    likely asleep sustained         -> OFF eligible
    display off + keyboard          -> WAKE
    display off + mouse             -> WAKE
    wake + stale AWAY               -> remains ON during holdoff
    alarm during AWAY               -> WAKE
    alarm during LIKELY_ASLEEP      -> WAKE
    alarm wake + stale policy       -> remains ON during alarm holdoff
    explicit keep-on                -> never auto-off
    automation disabled             -> never auto-off
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.alarms import service as alarms_service
from app.alarms.sequence import WakeSequence
from app.alarms.tr_time import parse_when_struct
from app.ambient import ingest as ambient_ingest
from app.ambient import service as ambient_service
from app.ambient.holdoff import (
    SOURCE_ALARM_WAKE,
    SOURCE_INPUT,
    HoldoffRegistry,
    set_holdoffs,
)
from app.ambient.policy import (
    ACTION_DISPLAY_OFF,
    ACTION_NONE,
    REASON_ALARM_CONTEXT,
    REASON_NOT_HELD_LONG_ENOUGH,
    REASON_OWNER_AWAY,
    REASON_OWNER_KEEP_ON,
    REASON_OWNER_LIKELY_ASLEEP,
    REASON_PERCEPTION_STALE,
    REASON_POLICY_DISABLED,
    REASON_UNCERTAIN,
    AmbientInputs,
    AmbientPolicy,
    decide,
)
from app.devices.status import DISPLAY_OFF, DISPLAY_ON, DeviceStatusRegistry
from app.ledger.models import ActivityEventRow
from app.presence.engine import FusionPolicy, PresenceFusionEngine
from app.presence.observations import parse_observation
from app.presence.states import PresenceState
from app.uistate.contract import UiState
from app.uistate.publisher import UiStatePublisher, get_publisher, set_publisher
from tests.alarms_support import FakeDeviceAction, build_session_factory, happy_device_results

NOW = datetime(2026, 9, 9, 2, 0, tzinfo=UTC)
DEVICE = uuid.uuid4()

#: A fusion policy with SHORT thresholds so a scenario can reach LIKELY_ASLEEP in minutes of
#: simulated time rather than the production twenty; the ambient thresholds are set per test.
FAST_FUSION = FusionPolicy(
    min_observations=2,
    min_window_s=20.0,
    min_sustain_s=30.0,
    likely_asleep_after_s=60.0,
    ttl_s={"camera": 600.0, "input": 300.0, "voice": 900.0, "task": 3600.0},
)


@pytest.fixture()
def session():
    with build_session_factory()() as s:
        yield s


@pytest.fixture()
def statuses() -> DeviceStatusRegistry:
    return DeviceStatusRegistry()


@pytest.fixture()
def holdoffs() -> HoldoffRegistry:
    return HoldoffRegistry()


@pytest.fixture()
def device():
    return FakeDeviceAction(results=happy_device_results())


@pytest.fixture()
def sequence(device):
    return WakeSequence(device_action=device, tts=None)


@pytest.fixture(autouse=True)
def _fresh_process(monkeypatch):
    set_publisher(UiStatePublisher())
    ambient_service.cancel_display_test()
    ambient_service.reset_presence_watermark()
    ambient_service.reset_holdoff_restore()
    # The eye is ON for every scenario unless a test disables it: the camera scenarios
    # below are about what the CAMERA says, not about the privacy switch.
    import app.presence.eye as eye_module

    monkeypatch.setattr(eye_module, "is_eye_enabled", lambda _session: True)
    yield
    set_publisher(UiStatePublisher())
    ambient_service.cancel_display_test()
    ambient_service.reset_holdoff_restore()


# --------------------------------------------------------------------- helpers


def _camera(
    *,
    at: datetime,
    present: bool = True,
    activity: str = "medium",
    posture: str = "upright",
    awake: str = "awake",
    confidence: float = 0.9,
) -> dict:
    return {
        "person_present": present,
        "presence_confidence": confidence,
        "activity_level": activity,
        "posture": posture,
        "awake_state": awake,
        "observed_at": at.isoformat(),
        "source": "camera",
    }


def _feed(engine: PresenceFusionEngine, payloads: list[dict], *, now: datetime) -> None:
    for payload in payloads:
        engine.add_observation(parse_observation(payload), now=now)


def _every(seconds: int, *, start: datetime, until: datetime) -> list[datetime]:
    moments = []
    t = start
    while t <= until:
        moments.append(t)
        t += timedelta(seconds=seconds)
    return moments


def _away_engine(*, start: datetime, until: datetime) -> PresenceFusionEngine:
    """Camera frames saying nobody is there, every ten seconds from ``start`` to ``until``.

    Absent frames only: inside the long camera TTL this policy uses, a "present" prelude
    would still be fresh and would make the window a CONFLICT (which the engine rightly
    reports as UNKNOWN), not an absence.
    """
    engine = PresenceFusionEngine(policy=FAST_FUSION)
    for t in _every(10, start=start, until=until):
        _feed(engine, [_camera(at=t, present=False, activity="none")], now=t)
    return engine


def _resting_engine(*, start: datetime, until: datetime) -> PresenceFusionEngine:
    """An owner in the room, still, in a resting posture: RESTING, then LIKELY_ASLEEP once
    the fusion policy's own threshold has held."""
    engine = PresenceFusionEngine(policy=FAST_FUSION)
    for t in _every(10, start=start, until=until):
        _feed(
            engine,
            [_camera(at=t, activity="none", posture="resting", awake="resting")],
            now=t,
        )
    return engine


def _runtimes(statuses, holdoffs, engine=None):
    return ambient_service.AmbientRuntimes(
        statuses=statuses, holdoffs=holdoffs, presence_engine=engine
    )


def _status(**overrides) -> dict:
    base = {
        "input_idle_s": 5.0,
        "display": {"state": DISPLAY_ON, "observed_at": NOW.isoformat()},
        "alarm_ringing": False,
        "armed_alarms": [],
        "local_alarm_fired": [],
        "observed_at": NOW.isoformat(),
    }
    base.update(overrides)
    return base


def _screens_on(statuses: DeviceStatusRegistry, *, now: datetime) -> None:
    statuses.record(DEVICE, _status(observed_at=now.isoformat()), now=now)


ENABLED = AmbientPolicy(auto_off_enabled=True, away_after_s=120, asleep_after_s=30)


# ------------------------------------------------------------------ the matrix


def test_present_with_input_stays_on(session, statuses, holdoffs) -> None:
    """PRESENT + input -> ON. Keyboard activity is the strongest evidence there is."""
    engine = PresenceFusionEngine(policy=FAST_FUSION)
    for t in _every(10, start=NOW - timedelta(seconds=120), until=NOW):
        _feed(engine, [_camera(at=t)], now=t)
    # A real input reset on the heartbeat: long idle, then the owner touched the machine.
    statuses.record(DEVICE, _status(input_idle_s=900.0), now=NOW - timedelta(seconds=30))
    ambient_ingest.ingest_status(
        session, DEVICE, _status(input_idle_s=1.0), statuses=statuses, holdoffs=holdoffs, now=NOW
    )
    inputs = ambient_service.collect_inputs(
        session, runtimes=_runtimes(statuses, holdoffs, engine), now=NOW
    )
    assert inputs.presence_state is PresenceState.PRESENT
    decision = decide(inputs, ENABLED, NOW)
    assert decision.action == ACTION_NONE
    assert decision.reason == f"holdoff:{SOURCE_INPUT}"


def test_a_short_absence_stays_on(session, statuses, holdoffs) -> None:
    """AWAY short -> ON."""
    engine = _away_engine(start=NOW - timedelta(seconds=40), until=NOW)
    _screens_on(statuses, now=NOW)
    inputs = ambient_service.collect_inputs(
        session, runtimes=_runtimes(statuses, holdoffs, engine), now=NOW
    )
    assert inputs.presence_state is PresenceState.AWAY
    assert inputs.presence_held_s < ENABLED.away_after_s
    decision = decide(inputs, ENABLED, NOW)
    assert decision.action == ACTION_NONE
    assert decision.reason == REASON_NOT_HELD_LONG_ENOUGH


def test_a_sustained_absence_is_off_eligible(session, statuses, holdoffs) -> None:
    """AWAY sustained -> OFF eligible (and the camera delivered inside the grace)."""
    engine = _away_engine(start=NOW - timedelta(seconds=400), until=NOW)
    _screens_on(statuses, now=NOW)
    inputs = ambient_service.collect_inputs(
        session, runtimes=_runtimes(statuses, holdoffs, engine), now=NOW
    )
    assert inputs.presence_state is PresenceState.AWAY
    assert inputs.presence_held_s >= ENABLED.away_after_s
    assert inputs.perception_age_s is not None and inputs.perception_age_s <= 10
    decision = decide(inputs, ENABLED, NOW)
    assert decision.action == ACTION_DISPLAY_OFF
    assert decision.reason == REASON_OWNER_AWAY


def test_unknown_stays_on(session, statuses, holdoffs) -> None:
    """UNKNOWN -> ON: an engine with nothing in it asserts nothing."""
    engine = PresenceFusionEngine(policy=FAST_FUSION)
    _screens_on(statuses, now=NOW)
    inputs = ambient_service.collect_inputs(
        session, runtimes=_runtimes(statuses, holdoffs, engine), now=NOW
    )
    assert inputs.presence_state is PresenceState.UNKNOWN
    decision = decide(inputs, ENABLED, NOW)
    assert decision.action == ACTION_NONE
    assert decision.reason == REASON_UNCERTAIN


def test_a_camera_that_stopped_delivering_stays_on(session, statuses, holdoffs) -> None:
    """camera failed -> ON. The engine still holds an AWAY it fused from old frames (its
    own TTL is long here on purpose); the ambient grace says the camera has not DELIVERED
    for 200 s, and a silent camera is a degraded perception, never an absent owner."""
    engine = _away_engine(start=NOW - timedelta(seconds=600), until=NOW - timedelta(seconds=200))
    _screens_on(statuses, now=NOW)
    inputs = ambient_service.collect_inputs(
        session, runtimes=_runtimes(statuses, holdoffs, engine), now=NOW
    )
    assert inputs.presence_state is PresenceState.AWAY  # what the engine still believes
    assert inputs.perception_age_s is not None and inputs.perception_age_s >= 200
    decision = decide(inputs, ENABLED, NOW)
    assert decision.action == ACTION_NONE
    assert decision.reason == REASON_PERCEPTION_STALE

    # And a camera that is invalidated outright (the eye closed) is UNKNOWN, not asleep.
    engine.invalidate_source("camera", now=NOW, reason="eye_disabled")
    inputs = ambient_service.collect_inputs(
        session, runtimes=_runtimes(statuses, holdoffs, engine), now=NOW
    )
    assert inputs.presence_state is PresenceState.UNKNOWN
    assert decide(inputs, ENABLED, NOW).action == ACTION_NONE


def test_a_short_likely_asleep_stays_on(session, statuses, holdoffs) -> None:
    """likely asleep short -> ON. RESTING escalates to LIKELY_ASLEEP only after the fusion
    threshold, and the ambient threshold then has to hold on top of that."""
    engine = _resting_engine(start=NOW - timedelta(seconds=110), until=NOW)
    _screens_on(statuses, now=NOW)
    inputs = ambient_service.collect_inputs(
        session, runtimes=_runtimes(statuses, holdoffs, engine), now=NOW
    )
    assert inputs.presence_state is PresenceState.LIKELY_ASLEEP
    strict = AmbientPolicy(auto_off_enabled=True, asleep_after_s=600)
    decision = decide(inputs, strict, NOW)
    assert decision.action == ACTION_NONE
    assert decision.reason == REASON_NOT_HELD_LONG_ENOUGH
    assert decision.evidence["needed_s"] == 600


def test_a_sustained_confident_likely_asleep_is_off_eligible(session, statuses, holdoffs) -> None:
    """likely asleep sustained -> OFF eligible."""
    engine = _resting_engine(start=NOW - timedelta(seconds=400), until=NOW)
    _screens_on(statuses, now=NOW)
    inputs = ambient_service.collect_inputs(
        session, runtimes=_runtimes(statuses, holdoffs, engine), now=NOW
    )
    assert inputs.presence_state is PresenceState.LIKELY_ASLEEP
    assert inputs.presence_confidence >= ENABLED.asleep_min_confidence
    assert inputs.presence_held_s >= ENABLED.asleep_after_s
    decision = decide(inputs, ENABLED, NOW)
    assert decision.action == ACTION_DISPLAY_OFF
    assert decision.reason == REASON_OWNER_LIKELY_ASLEEP


@pytest.mark.parametrize(
    ("before_idle", "after_idle", "label"),
    [(600.0, 2.0, "keyboard"), (600.0, 0.5, "mouse")],
)
def test_input_on_a_dark_screen_is_a_wake(
    session, statuses, holdoffs, before_idle: float, after_idle: float, label: str
) -> None:
    """display off + keyboard / mouse -> WAKE. The device wakes its own display (the OS does
    that, before any cloud); what the cloud must do is record the input, start the holdoff,
    and publish the observed ``display.on`` when the next heartbeat says so."""
    dark = {"state": DISPLAY_OFF, "observed_at": NOW.isoformat()}
    ambient_ingest.ingest_status(
        session,
        DEVICE,
        _status(input_idle_s=before_idle, display=dark),
        statuses=statuses,
        holdoffs=holdoffs,
        now=NOW - timedelta(seconds=20),
    )
    result = ambient_ingest.ingest_status(
        session,
        DEVICE,
        _status(input_idle_s=after_idle, display=dark),
        statuses=statuses,
        holdoffs=holdoffs,
        now=NOW,
    )
    assert result.input_active_recorded is True, label
    assert result.holdoff_started is True
    assert holdoffs.is_active(SOURCE_INPUT, now=NOW)
    rows = [
        r for r in session.query(ActivityEventRow).all() if r.event_type == "owner.input_active"
    ]
    assert len(rows) == 1

    lit = {"state": DISPLAY_ON, "observed_at": (NOW + timedelta(seconds=5)).isoformat()}
    after = ambient_ingest.ingest_status(
        session,
        DEVICE,
        _status(input_idle_s=after_idle + 5, display=lit),
        statuses=statuses,
        holdoffs=holdoffs,
        now=NOW + timedelta(seconds=5),
    )
    assert after.display_published == DISPLAY_ON
    assert UiState.DISPLAY_ON.value in [e.state.value for e in get_publisher().tail(limit=50)]


def test_a_wake_outlives_a_stale_away_for_the_whole_holdoff(holdoffs) -> None:
    """wake + stale AWAY -> remains ON during holdoff; after it, the policy resumes."""
    holdoffs.start(SOURCE_INPUT, seconds=ENABLED.input_holdoff_s, now=NOW)
    stale_away = AmbientInputs(
        display_on=True,
        presence_state=PresenceState.AWAY,
        presence_confidence=0.9,
        presence_held_s=5000.0,
        presence_stale=False,
        eye_enabled=True,
        perception_age_s=5.0,
    )
    for offset in (0, 60, ENABLED.input_holdoff_s - 1):
        moment = NOW + timedelta(seconds=offset)
        inputs = replace(stale_away, holdoffs=holdoffs.active(now=moment))
        decision = decide(inputs, ENABLED, moment)
        assert decision.action == ACTION_NONE, offset
        assert decision.reason == f"holdoff:{SOURCE_INPUT}"
    moment = NOW + timedelta(seconds=ENABLED.input_holdoff_s + 1)
    inputs = replace(stale_away, holdoffs=holdoffs.active(now=moment))
    assert decide(inputs, ENABLED, moment).action == ACTION_DISPLAY_OFF


@pytest.mark.parametrize("state", [PresenceState.AWAY, PresenceState.LIKELY_ASLEEP])
def test_an_alarm_wakes_the_display_whatever_presence_says(
    session, sequence, device, holdoffs, state: PresenceState
) -> None:
    """alarm during AWAY / LIKELY_ASLEEP -> WAKE. The wake sequence asks for the display
    before any audio, and the ambient decision reads the alarm as context."""
    set_holdoffs(holdoffs)
    alarm = alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 5}, now=NOW)
    )
    decision = alarms_service.fire_alarm(
        session,
        alarm.id,
        sequence=sequence,
        firing_id=uuid.uuid4(),
        now=NOW + timedelta(seconds=10),
    )
    assert decision.fired is True
    called = device.capabilities_called()
    assert "desktop.display_wake" in called
    assert called.index("desktop.display_wake") < called.index("desktop.alarm_start")

    inputs = AmbientInputs(
        display_on=True,
        presence_state=state,
        presence_confidence=0.95,
        presence_held_s=9000.0,
        presence_stale=False,
        eye_enabled=True,
        alarm_active=True,
        perception_age_s=5.0,
    )
    ambient = decide(inputs, ENABLED, NOW + timedelta(seconds=10))
    assert ambient.action == ACTION_NONE
    assert ambient.reason == REASON_ALARM_CONTEXT


def test_the_alarm_holdoff_keeps_the_screens_on_after_the_alarm_stops(
    session, sequence, device, holdoffs
) -> None:
    """alarm wake + stale policy -> remains ON during alarm holdoff. The holdoff is started
    by ``fire_alarm`` itself (ADR-0079 §6 wired it; it had no caller before)."""
    set_holdoffs(holdoffs)
    alarm = alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 5}, now=NOW)
    )
    fired_at = NOW + timedelta(seconds=10)
    alarms_service.fire_alarm(
        session, alarm.id, sequence=sequence, firing_id=uuid.uuid4(), now=fired_at
    )
    assert holdoffs.is_active(SOURCE_ALARM_WAKE, now=fired_at)
    alarms_service.stop_alarm(
        session, alarm.id, sequence=sequence, now=fired_at + timedelta(seconds=60)
    )

    asleep = AmbientInputs(
        display_on=True,
        presence_state=PresenceState.LIKELY_ASLEEP,
        presence_confidence=0.95,
        presence_held_s=9000.0,
        presence_stale=False,
        eye_enabled=True,
        alarm_active=False,
        perception_age_s=5.0,
    )
    later = fired_at + timedelta(seconds=120)
    inputs = replace(asleep, holdoffs=holdoffs.active(now=later))
    decision = decide(inputs, ENABLED, later)
    assert decision.action == ACTION_NONE
    assert decision.reason == f"holdoff:{SOURCE_ALARM_WAKE}"


def test_an_explicit_keep_on_never_auto_offs() -> None:
    """explicit keep-on -> never auto-off, whatever the presence and the thresholds say."""
    keep = AmbientPolicy(auto_off_enabled=True, away_after_s=1, asleep_after_s=1, keep_on=True)
    for state in (PresenceState.AWAY, PresenceState.LIKELY_ASLEEP):
        inputs = AmbientInputs(
            display_on=True,
            presence_state=state,
            presence_confidence=1.0,
            presence_held_s=99999.0,
            presence_stale=False,
            eye_enabled=True,
            perception_age_s=1.0,
        )
        decision = decide(inputs, keep, NOW)
        assert decision.action == ACTION_NONE
        assert decision.reason == REASON_OWNER_KEEP_ON


def test_disabled_automation_never_auto_offs() -> None:
    """automation disabled -> never auto-off."""
    disabled = AmbientPolicy(auto_off_enabled=False, away_after_s=1)
    inputs = AmbientInputs(
        display_on=True,
        presence_state=PresenceState.AWAY,
        presence_confidence=1.0,
        presence_held_s=99999.0,
        presence_stale=False,
        eye_enabled=True,
        perception_age_s=1.0,
    )
    decision = decide(inputs, disabled, NOW)
    assert decision.action == ACTION_NONE
    assert decision.reason == REASON_POLICY_DISABLED
