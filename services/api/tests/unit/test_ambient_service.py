"""Unit tests: app.ambient.service and app.ambient.ingest (M18.3 spec §3.6, §3.9, §8.2).

The tick that acts on a decision, the heartbeat that turns real keyboard input into
evidence, and the owner's display test — which is a REAL display-off on the production
path, issued by the clock rather than inside a tool call.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.alarms.sequence import WakeSequence
from app.ambient import ingest as ambient_ingest
from app.ambient import service as ambient_service
from app.ambient.holdoff import SOURCE_INPUT, SOURCE_OWNER_COMMAND, HoldoffRegistry
from app.ambient.policy import ACTION_DISPLAY_OFF, REASON_OWNER_TEST
from app.devices.status import DISPLAY_OFF, DISPLAY_ON, DeviceStatusRegistry
from app.ledger.models import ActivityEventRow
from app.presence.states import PresenceState
from app.uistate.contract import UiState
from app.uistate.publisher import UiStatePublisher, get_publisher, set_publisher
from tests.alarms_support import (
    FakeDeviceAction,
    build_session_factory,
    happy_device_results,
    refused,
)

NOW = datetime(2026, 9, 9, 2, 0, tzinfo=UTC)
DEVICE = uuid.uuid4()


@pytest.fixture()
def session():
    with build_session_factory()() as s:
        yield s


@pytest.fixture()
def device():
    return FakeDeviceAction(results=happy_device_results())


@pytest.fixture()
def sequence(device):
    return WakeSequence(device_action=device, tts=None)


@pytest.fixture()
def statuses() -> DeviceStatusRegistry:
    return DeviceStatusRegistry()


@pytest.fixture()
def holdoffs() -> HoldoffRegistry:
    return HoldoffRegistry()


@pytest.fixture()
def runtimes(statuses, holdoffs):
    return ambient_service.AmbientRuntimes(statuses=statuses, holdoffs=holdoffs)


@pytest.fixture(autouse=True)
def _fresh_publisher():
    set_publisher(UiStatePublisher())
    ambient_service.cancel_display_test()
    ambient_service.reset_presence_watermark()
    yield
    set_publisher(UiStatePublisher())
    ambient_service.cancel_display_test()


def _states() -> list[str]:
    return [e.state.value for e in get_publisher().tail(limit=200)]


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


# ------------------------------------------------------------------- the policy row


def test_the_policy_row_is_created_with_the_conservative_defaults(session):
    """Spec §3.9: automatic display-off is OFF until the owner turns it on."""
    policy = ambient_service.get_policy(session)
    assert policy.auto_off_enabled is False
    assert policy.off_when_away is True
    assert policy.off_when_asleep is True
    assert policy.away_after_s == 900
    assert policy.asleep_after_s == 600
    assert policy.asleep_min_confidence == 0.7


def test_setting_the_policy_records_only_what_actually_changed(session, holdoffs):
    policy, applied = ambient_service.set_policy(
        session, {"auto_off_enabled": True}, holdoffs=holdoffs, now=NOW
    )
    assert policy.auto_off_enabled is True
    assert applied == {"auto_off_enabled": True}

    # Saying it twice does not claim to have turned it on twice.
    _, applied_again = ambient_service.set_policy(
        session, {"auto_off_enabled": True}, holdoffs=holdoffs, now=NOW
    )
    assert applied_again == {}


def test_a_policy_change_writes_a_ledger_row_and_starts_the_command_holdoff(
    session, holdoffs
):
    ambient_service.set_policy(session, {"off_when_asleep": False}, holdoffs=holdoffs, now=NOW)
    events = {r.event_type for r in session.query(ActivityEventRow).all()}
    assert "ambient.policy_changed" in events
    assert holdoffs.is_active(SOURCE_OWNER_COMMAND, now=NOW)


# ------------------------------------------------------------------------- the tick


def test_the_tick_does_nothing_while_the_policy_is_off(session, sequence, device, runtimes):
    runtimes.statuses.record(DEVICE, _status(), now=NOW)
    result = ambient_service.tick(session, sequence=sequence, runtimes=runtimes, now=NOW)
    assert result.acted is False
    assert result.decision.reason == "policy_disabled"
    assert device.count("desktop.display_off") == 0


def test_the_tick_issues_a_receipted_display_off_when_the_decision_says_so(
    session, sequence, device, runtimes, monkeypatch
):
    ambient_service.set_policy(
        session, {"auto_off_enabled": True}, holdoffs=runtimes.holdoffs, now=NOW
    )
    runtimes.holdoffs.clear()  # the owner's own command holdoff would otherwise block it
    runtimes.statuses.record(DEVICE, _status(), now=NOW)
    _pretend_owner_is_away(monkeypatch)

    result = ambient_service.tick(session, sequence=sequence, runtimes=runtimes, now=NOW)
    assert result.acted is True
    assert result.decision.action == ACTION_DISPLAY_OFF
    assert result.receipt_capability == "display.off"
    assert device.count("desktop.display_off") == 1
    payload = device.payload_for("desktop.display_off")
    assert payload["reason"] == "owner_away"

    receipts = [
        r for r in session.query(ActivityEventRow).all() if r.event_type == "action.receipt"
    ]
    assert [r.action for r in receipts] == ["display.off"]


def test_a_successful_automatic_off_starts_no_holdoff(
    session, sequence, device, runtimes, monkeypatch
):
    """Spec §3.9: a display-off decision "starts NO holdoff" — acting is not a reason to
    stop watching."""
    ambient_service.set_policy(
        session, {"auto_off_enabled": True}, holdoffs=runtimes.holdoffs, now=NOW
    )
    runtimes.holdoffs.clear()
    runtimes.statuses.record(DEVICE, _status(), now=NOW)
    _pretend_owner_is_away(monkeypatch)

    ambient_service.tick(session, sequence=sequence, runtimes=runtimes, now=NOW)
    assert runtimes.holdoffs.active(now=NOW) == ()


def test_a_recent_input_refusal_starts_the_input_holdoff(
    session, sequence, device, runtimes, monkeypatch
):
    """Spec §3.9's last line, and spec §11's own row: "a device ``recent_input`` refusal is
    a refused receipt and starts the input holdoff"."""
    device.results["desktop.display_off"] = refused("recent_input", input_idle_s=2)
    ambient_service.set_policy(
        session, {"auto_off_enabled": True}, holdoffs=runtimes.holdoffs, now=NOW
    )
    runtimes.holdoffs.clear()
    runtimes.statuses.record(DEVICE, _status(), now=NOW)
    _pretend_owner_is_away(monkeypatch)

    result = ambient_service.tick(session, sequence=sequence, runtimes=runtimes, now=NOW)
    assert result.acted is True
    assert result.terminal_status == "failed"
    assert runtimes.holdoffs.is_active(SOURCE_INPUT, now=NOW)

    receipt = [
        r for r in session.query(ActivityEventRow).all() if r.event_type == "action.receipt"
    ][0]
    assert receipt.detail_json["execution_status"] == "refused"
    assert receipt.detail_json["error_class"] == "recent_input"


def test_the_holdoff_the_refusal_started_blocks_the_next_tick(
    session, sequence, device, runtimes, monkeypatch
):
    device.results["desktop.display_off"] = refused("recent_input")
    ambient_service.set_policy(
        session, {"auto_off_enabled": True}, holdoffs=runtimes.holdoffs, now=NOW
    )
    runtimes.holdoffs.clear()
    runtimes.statuses.record(DEVICE, _status(), now=NOW)
    _pretend_owner_is_away(monkeypatch)

    ambient_service.tick(session, sequence=sequence, runtimes=runtimes, now=NOW)
    device.reset()
    later = ambient_service.tick(
        session, sequence=sequence, runtimes=runtimes, now=NOW + timedelta(seconds=30)
    )
    assert later.acted is False
    assert later.decision.reason == f"holdoff:{SOURCE_INPUT}"
    assert device.count("desktop.display_off") == 0


def _pretend_owner_is_away(monkeypatch) -> None:
    """A sustained, confident, camera-backed AWAY — assembled here rather than by feeding
    the fusion engine, so this file tests the AMBIENT service and not the presence one."""
    from app.ambient import service as service_mod
    from app.ambient.policy import AmbientInputs

    original = service_mod.collect_inputs

    def _collect(session, *, runtimes=None, now=None):
        real = original(session, runtimes=runtimes, now=now)
        return AmbientInputs(
            display_on=real.display_on,
            presence_state=PresenceState.AWAY,
            presence_confidence=0.9,
            presence_held_s=5000.0,
            presence_stale=False,
            eye_enabled=True,
            alarm_active=real.alarm_active,
            next_alarm_at=real.next_alarm_at,
            holdoffs=real.holdoffs,
            # ADR-0079 §3: "camera-backed" now means the camera DELIVERED recently, too.
            perception_age_s=5.0,
        )

    monkeypatch.setattr(service_mod, "collect_inputs", _collect)


# ---------------------------------------------------------------- the display test


def test_the_display_test_is_issued_by_the_clock_not_by_the_request(
    session, sequence, device, runtimes
):
    """Spec §8.2: the tool arms a moment; the CLOCK issues the real, receipted
    ``display.off`` — which is what actually gives the owner the ten seconds."""
    at = ambient_service.schedule_display_test(delay_seconds=10, now=NOW)
    assert at == NOW + timedelta(seconds=10)
    assert device.count("desktop.display_off") == 0

    early = ambient_service.tick(
        session, sequence=sequence, runtimes=runtimes, now=NOW + timedelta(seconds=5)
    )
    assert early.acted is False
    assert device.count("desktop.display_off") == 0

    due = ambient_service.tick(
        session, sequence=sequence, runtimes=runtimes, now=NOW + timedelta(seconds=11)
    )
    assert due.acted is True
    assert due.decision.reason == REASON_OWNER_TEST
    assert device.payload_for("desktop.display_off")["reason"] == REASON_OWNER_TEST


def test_the_display_test_runs_once_and_disarms_itself(session, sequence, device, runtimes):
    ambient_service.schedule_display_test(delay_seconds=1, now=NOW)
    ambient_service.tick(
        session, sequence=sequence, runtimes=runtimes, now=NOW + timedelta(seconds=2)
    )
    ambient_service.tick(
        session, sequence=sequence, runtimes=runtimes, now=NOW + timedelta(seconds=3)
    )
    assert device.count("desktop.display_off") == 1
    assert ambient_service.pending_display_test() is None


def test_the_display_test_runs_with_the_policy_off_and_the_eye_disabled(
    session, sequence, device, runtimes
):
    """Spec §8.2: the harness disables the eye first, to prove camera failure cannot lock
    the owner out. The test must therefore not depend on presence at all."""
    ambient_service.schedule_display_test(delay_seconds=1, now=NOW)
    result = ambient_service.tick(
        session, sequence=sequence, runtimes=runtimes, now=NOW + timedelta(seconds=2)
    )
    assert result.acted is True
    assert ambient_service.get_policy(session).auto_off_enabled is False


# ---------------------------------------------------------------- heartbeat ingest


def test_a_display_transition_publishes_the_observed_state(session, statuses, holdoffs):
    """Spec §7: ``display.on``/``display.off`` come from the device's OBSERVED power state,
    never from having asked for one."""
    ambient_ingest.ingest_status(
        session, DEVICE, _status(), statuses=statuses, holdoffs=holdoffs, now=NOW
    )
    result = ambient_ingest.ingest_status(
        session,
        DEVICE,
        _status(display={"state": DISPLAY_OFF}),
        statuses=statuses,
        holdoffs=holdoffs,
        now=NOW + timedelta(seconds=10),
    )
    assert result.display_published == DISPLAY_OFF
    assert UiState.DISPLAY_OFF.value in _states()


def test_an_input_reset_writes_owner_input_active_and_starts_the_holdoff(
    session, statuses, holdoffs
):
    """Spec §1.3, §3.6b: physical owner input outranks passive inference."""
    ambient_ingest.ingest_status(
        session,
        DEVICE,
        _status(input_idle_s=900, display={"state": DISPLAY_OFF}),
        statuses=statuses,
        holdoffs=holdoffs,
        now=NOW,
    )
    result = ambient_ingest.ingest_status(
        session,
        DEVICE,
        _status(input_idle_s=1, display={"state": DISPLAY_ON}),
        statuses=statuses,
        holdoffs=holdoffs,
        now=NOW + timedelta(seconds=10),
    )
    assert result.input_active_recorded is True
    assert result.holdoff_started is True
    assert holdoffs.is_active(SOURCE_INPUT, now=NOW + timedelta(seconds=10))
    events = {r.event_type for r in session.query(ActivityEventRow).all()}
    assert "owner.input_active" in events


def test_a_long_idle_writes_nothing_at_all(session, statuses, holdoffs):
    result = ambient_ingest.ingest_status(
        session,
        DEVICE,
        _status(input_idle_s=7200),
        statuses=statuses,
        holdoffs=holdoffs,
        now=NOW,
    )
    assert result.observed is False
    assert result.input_active_recorded is False
    assert result.holdoff_started is False


def test_the_input_observation_carries_only_the_seven_allowed_fields(statuses) -> None:
    """The perception boundary of spec §2 applies to an input observation too: it is
    derived from a tick count and carries no more than that."""
    from app.presence.observations import OBSERVATION_FIELDS, parse_observation

    change = statuses.record(DEVICE, _status(input_idle_s=3), now=NOW)
    payload = ambient_ingest.input_observation(change, now=NOW)
    assert set(payload) == OBSERVATION_FIELDS
    observation = parse_observation(payload)
    assert observation.source == "input"
    assert observation.person_present is True
    assert observation.awake_state == "awake"


def test_an_unusable_status_is_a_clean_no_op(session, statuses, holdoffs):
    result = ambient_ingest.ingest_status(
        session, DEVICE, None, statuses=statuses, holdoffs=holdoffs, now=NOW
    )
    assert result.as_dict() == {
        "observed": False,
        "input_active_recorded": False,
        "holdoff_started": False,
        "display_published": None,
        "reconciled_alarms": [],
        # B47: a local snooze the cloud adopted from this report.
        "snoozed_alarms": [],
    }


def test_a_locally_fired_alarm_is_reconciled_from_the_heartbeat(session, statuses, holdoffs):
    """Spec §3.6d, through the real path: the device names an alarm it rang itself."""
    from app.alarms import service as alarms_service
    from app.alarms.tr_time import parse_when_struct

    alarm = alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 60}, now=NOW)
    )
    result = ambient_ingest.ingest_status(
        session,
        DEVICE,
        _status(local_alarm_fired=[str(alarm.id)]),
        statuses=statuses,
        holdoffs=holdoffs,
        now=NOW,
    )
    assert result.reconciled_alarms == (str(alarm.id),)


def test_world_model_facts_name_the_display_and_the_idle_counter(statuses) -> None:
    """Spec §3.6e."""
    statuses.record(DEVICE, _status(), now=NOW)
    keys = {f["key"] for f in ambient_ingest.world_model_facts(statuses=statuses)}
    assert f"device.display_state.{DEVICE}" in keys
    assert f"device.input_idle_s.{DEVICE}" in keys


# ------------------------------------------------------------------ wake on return


def test_wake_on_return_only_acts_when_the_display_is_actually_off(
    session, sequence, device, runtimes
):
    runtimes.statuses.record(DEVICE, _status(display={"state": DISPLAY_ON}), now=NOW)
    assert ambient_service.wake_on_return(
        session, sequence=sequence, runtimes=runtimes, now=NOW
    ) is None
    assert device.count("desktop.display_wake") == 0

    runtimes.statuses.record(
        DEVICE, _status(display={"state": DISPLAY_OFF}), now=NOW + timedelta(seconds=10)
    )
    step = ambient_service.wake_on_return(
        session, sequence=sequence, runtimes=runtimes, now=NOW + timedelta(seconds=11)
    )
    assert step is not None
    assert device.count("desktop.display_wake") == 1
    assert device.payload_for("desktop.display_wake")["reason"] == "owner_returned"


def test_the_tick_wakes_a_dark_display_when_the_owner_returns(
    session, sequence, device, runtimes
):
    """Spec §3.9's ``wake_on_return``, through the TICK rather than by calling the helper.

    Read from the presence BUS rather than from the current assertion, because
    ``owner.returned`` is a transition and the assertion that follows it is ``present`` —
    by the time a tick reads the state, the moment of return is gone.
    """
    from app.uistate.publisher import publish

    ambient_service.reset_presence_watermark()
    runtimes.statuses.record(DEVICE, _status(display={"state": DISPLAY_OFF}), now=NOW)
    ambient_service.tick(session, sequence=sequence, runtimes=runtimes, now=NOW)
    assert device.count("desktop.display_wake") == 0  # nothing returned yet

    publish(UiState.OWNER_RETURNED, subsystem="presence")
    ambient_service.tick(
        session, sequence=sequence, runtimes=runtimes, now=NOW + timedelta(seconds=10)
    )
    assert device.count("desktop.display_wake") == 1

    # ...and the same event is not acted on twice: the watermark advanced past it.
    ambient_service.tick(
        session, sequence=sequence, runtimes=runtimes, now=NOW + timedelta(seconds=20)
    )
    assert device.count("desktop.display_wake") == 1
    ambient_service.reset_presence_watermark()


def test_wake_on_return_is_skipped_when_the_owner_turned_it_off(
    session, sequence, device, runtimes
):
    ambient_service.set_policy(
        session, {"wake_on_return": False}, holdoffs=runtimes.holdoffs, now=NOW
    )
    runtimes.statuses.record(DEVICE, _status(display={"state": DISPLAY_OFF}), now=NOW)
    assert ambient_service.wake_on_return(
        session, sequence=sequence, runtimes=runtimes, now=NOW
    ) is None


def test_an_alarm_that_fired_holds_off_every_automatic_display_off(session, holdoffs):
    from app.ambient.holdoff import SOURCE_ALARM_WAKE

    ambient_service.note_alarm_wake(session, holdoffs=holdoffs, now=NOW)
    assert holdoffs.is_active(SOURCE_ALARM_WAKE, now=NOW)


def test_display_state_summary_reports_unknown_when_nobody_has_said(statuses):
    assert ambient_service.display_state_summary(statuses=statuses) == "unknown"
    statuses.record(DEVICE, _status(), now=NOW)
    assert ambient_service.display_state_summary(statuses=statuses) == "on"


# ---------------------------------------------------------------------- holdoffs


def test_a_holdoff_extends_but_never_shortens(holdoffs):
    holdoffs.start(SOURCE_INPUT, seconds=600, now=NOW)
    holdoffs.start(SOURCE_INPUT, seconds=60, now=NOW)
    active = holdoffs.active(now=NOW)
    assert len(active) == 1
    assert active[0].until == NOW + timedelta(seconds=600)


def test_an_expired_holdoff_stops_being_active(holdoffs):
    holdoffs.start(SOURCE_INPUT, seconds=60, now=NOW)
    assert holdoffs.is_active(SOURCE_INPUT, now=NOW + timedelta(seconds=30))
    assert not holdoffs.is_active(SOURCE_INPUT, now=NOW + timedelta(seconds=61))


def test_an_unknown_holdoff_source_is_refused(holdoffs):
    with pytest.raises(ValueError, match="unknown holdoff source"):
        holdoffs.start("whatever", now=NOW)
