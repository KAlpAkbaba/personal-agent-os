"""Unit tests: app.devices.status (M18.3 spec §3.6, §5.3).

The registry stores what a device said and reports what CHANGED. Two properties matter
more than the rest:

* **Lenient validation.** The authoritative schema is Track D's; a status shape this Cloud
  Core has never seen must not disconnect a device, because a disconnected device cannot
  ring an alarm.
* **Long idle produces NOTHING.** Absence of input is not evidence of absence (spec §3.6a).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.devices.status import (
    DISPLAY_OFF,
    DISPLAY_ON,
    DISPLAY_UNKNOWN,
    INPUT_OBSERVATION_THROTTLE_S,
    INPUT_RESET_AFTER_IDLE_S,
    DeviceStatusRegistry,
    parse_status,
)

NOW = datetime(2026, 9, 9, 2, 0, tzinfo=UTC)
DEVICE = uuid.uuid4()


def _status(**overrides) -> dict:
    base = {
        "input_idle_s": 5.0,
        "last_input_at": NOW.isoformat(),
        "display": {"state": DISPLAY_ON, "observed_at": NOW.isoformat()},
        "alarm_ringing": False,
        "ringing_alarm_id": None,
        "armed_alarms": [],
        "local_alarm_fired": [],
        "holdoff_until": None,
        "observed_at": NOW.isoformat(),
    }
    base.update(overrides)
    return base


@pytest.fixture()
def registry() -> DeviceStatusRegistry:
    return DeviceStatusRegistry()


# ------------------------------------------------------------------------- parsing


def test_a_full_status_parses_into_the_normalised_shape() -> None:
    parsed = parse_status(DEVICE, _status(monitors=2), now=NOW)
    assert parsed is not None
    assert parsed.input_idle_s == 5.0
    assert parsed.display_state == DISPLAY_ON
    assert parsed.monitors == 2
    assert parsed.input_active is True


@pytest.mark.parametrize(
    "raw", [None, {}, [], "status", 42, {"input_idle_s": "soon"}], ids=repr
)
def test_a_malformed_status_never_raises(raw) -> None:
    """A device sending a strange heartbeat must stay CONNECTED (module docstring)."""
    parsed = parse_status(DEVICE, raw, now=NOW)
    assert parsed is None or parsed.input_idle_s is None


def test_unknown_keys_are_ignored_rather_than_refused() -> None:
    """Track D owns the schema; this side must tolerate a field it has never heard of."""
    parsed = parse_status(DEVICE, _status(some_future_field={"a": 1}), now=NOW)
    assert parsed is not None
    assert parsed.display_state == DISPLAY_ON
    assert "some_future_field" in parsed.raw_keys


def test_an_unreadable_display_state_reads_as_unknown_not_as_off() -> None:
    """"Unknown" is the only safe reading for a policy that can darken screens."""
    odd = parse_status(DEVICE, _status(display={"state": "flickering"}), now=NOW)
    assert odd.display_state == DISPLAY_UNKNOWN
    assert parse_status(DEVICE, _status(display=None), now=NOW).display_state == DISPLAY_UNKNOWN


def test_a_negative_idle_counter_is_discarded(registry) -> None:
    parsed = parse_status(DEVICE, _status(input_idle_s=-1), now=NOW)
    assert parsed.input_idle_s is None
    assert parsed.input_active is False


# ---------------------------------------------------------------------- the changes


def test_the_first_status_reports_the_display_as_changed(registry) -> None:
    change = registry.record(DEVICE, _status(), now=NOW)
    assert change is not None
    assert change.display_changed is True
    assert change.display_state == DISPLAY_ON


def test_an_unchanged_display_is_not_reported_again(registry) -> None:
    """A heartbeat arrives every ~10 s and almost always says the same thing."""
    registry.record(DEVICE, _status(), now=NOW)
    change = registry.record(DEVICE, _status(), now=NOW + timedelta(seconds=10))
    assert change.display_changed is False


def test_a_display_transition_is_reported(registry) -> None:
    registry.record(DEVICE, _status(), now=NOW)
    change = registry.record(
        DEVICE, _status(display={"state": DISPLAY_OFF}), now=NOW + timedelta(seconds=10)
    )
    assert change.display_changed is True
    assert change.display_state == DISPLAY_OFF


def test_a_transition_to_unknown_is_not_a_transition(registry) -> None:
    registry.record(DEVICE, _status(), now=NOW)
    change = registry.record(
        DEVICE, _status(display={"state": "who knows"}), now=NOW + timedelta(seconds=10)
    )
    assert change.display_changed is False


# --------------------------------------------------------------------- input activity


def test_an_input_observation_is_throttled_to_one_per_window(registry) -> None:
    first = registry.record(DEVICE, _status(input_idle_s=2), now=NOW)
    assert first.should_observe_input is True
    soon = registry.record(DEVICE, _status(input_idle_s=2), now=NOW + timedelta(seconds=10))
    assert soon.should_observe_input is False
    later = registry.record(
        DEVICE,
        _status(input_idle_s=2),
        now=NOW + timedelta(seconds=INPUT_OBSERVATION_THROTTLE_S + 1),
    )
    assert later.should_observe_input is True


def test_a_long_idle_produces_no_observation_at_all(registry) -> None:
    """Spec §3.6a: absence of input is not evidence of absence."""
    change = registry.record(DEVICE, _status(input_idle_s=3600), now=NOW)
    assert change.should_observe_input is False
    assert change.status.input_active is False


def test_an_idle_reset_after_a_long_idle_is_an_input_reset(registry) -> None:
    registry.record(DEVICE, _status(input_idle_s=INPUT_RESET_AFTER_IDLE_S + 60), now=NOW)
    change = registry.record(
        DEVICE, _status(input_idle_s=1), now=NOW + timedelta(seconds=10)
    )
    assert change.input_reset is True


def test_a_short_pause_between_keystrokes_is_not_an_input_reset(registry) -> None:
    registry.record(DEVICE, _status(input_idle_s=20), now=NOW)
    change = registry.record(DEVICE, _status(input_idle_s=1), now=NOW + timedelta(seconds=10))
    assert change.input_reset is False


def test_any_input_while_the_display_is_off_is_an_input_reset(registry) -> None:
    """The owner pressing a key on a dark screen is exactly the wake we must never fight,
    and its idle counter may be well under the long-idle threshold by the time we see it
    (spec §3.6b)."""
    registry.record(DEVICE, _status(input_idle_s=30, display={"state": DISPLAY_OFF}), now=NOW)
    change = registry.record(
        DEVICE,
        _status(input_idle_s=1, display={"state": DISPLAY_ON}),
        now=NOW + timedelta(seconds=10),
    )
    assert change.input_reset is True


def test_a_first_report_on_a_lit_screen_is_not_a_reset(registry) -> None:
    """Nothing changed — we simply had not been watching."""
    change = registry.record(DEVICE, _status(input_idle_s=1), now=NOW)
    assert change.input_reset is False


# ------------------------------------------------------------------- local alarms


def test_only_newly_fired_alarms_are_reported(registry) -> None:
    alarm_id = str(uuid.uuid4())
    first = registry.record(DEVICE, _status(local_alarm_fired=[alarm_id]), now=NOW)
    assert first.newly_fired_alarms == (alarm_id,)
    again = registry.record(
        DEVICE, _status(local_alarm_fired=[alarm_id]), now=NOW + timedelta(seconds=10)
    )
    assert again.newly_fired_alarms == ()


# ------------------------------------------------------------------- the aggregates


def test_displays_on_is_none_when_nobody_has_reported(registry) -> None:
    """``None`` is the answer the ambient policy refuses to act on — uncertain means ON."""
    assert registry.displays_on() is None
    registry.record(DEVICE, _status(display={"state": DISPLAY_UNKNOWN}), now=NOW)
    assert registry.displays_on() is None


def test_displays_on_reflects_any_device_reporting_a_lit_screen(registry) -> None:
    other = uuid.uuid4()
    registry.record(DEVICE, _status(display={"state": DISPLAY_OFF}), now=NOW)
    assert registry.displays_on() is False
    registry.record(other, _status(display={"state": DISPLAY_ON}), now=NOW)
    assert registry.displays_on() is True


def test_any_alarm_ringing_is_true_when_a_device_says_so(registry) -> None:
    assert registry.any_alarm_ringing() is False
    registry.record(DEVICE, _status(alarm_ringing=True), now=NOW)
    assert registry.any_alarm_ringing() is True


def test_the_registry_is_bounded(registry) -> None:
    """One owner, a handful of machines: a device that never stops enrolling must not grow
    this without bound."""
    small = DeviceStatusRegistry(max_devices=2)
    for _ in range(5):
        small.record(uuid.uuid4(), _status(), now=NOW)
    assert len(small.all()) <= 2


def test_as_dict_exposes_the_fields_the_route_returns(registry) -> None:
    registry.record(DEVICE, _status(monitors=1), now=NOW)
    payload = registry.get(DEVICE).as_dict()
    assert payload["device_id"] == str(DEVICE)
    assert payload["display"]["state"] == DISPLAY_ON
    assert payload["input_active"] is True
    assert payload["monitors"] == 1
