"""Unit tests: app.routines.triggers.

Covers validation of all three trigger kinds, the "is this due?" check for each, the
recurring-weekday-alarm case, and the DST-boundary property the task brief calls out by
name: a schedule trigger must resolve the SAME local wall-clock time correctly on both
sides of a daylight-saving transition, because it converts `now` through zoneinfo every
time rather than caching a UTC offset.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.routines.triggers import (
    InvalidTrigger,
    check_at_due,
    check_presence_due,
    check_schedule_due,
    validate_at_trigger,
    validate_presence_trigger,
    validate_schedule_trigger,
    validate_trigger,
)

# --------------------------------------------------------------------------- validation


def test_validate_at_trigger_requires_tz_aware_iso() -> None:
    with pytest.raises(InvalidTrigger):
        validate_at_trigger({"at": "2026-09-05T07:30:00"})  # naive — refused


def test_validate_at_trigger_accepts_z_suffix() -> None:
    normalized = validate_at_trigger({"at": "2026-09-05T07:30:00Z"})
    assert normalized["at"].startswith("2026-09-05T07:30:00")


def test_validate_schedule_trigger_rejects_unknown_timezone() -> None:
    with pytest.raises(InvalidTrigger):
        validate_schedule_trigger(
            {"weekdays": [0], "time": "07:30", "timezone": "Mars/Olympus_Mons"}
        )


def test_validate_schedule_trigger_rejects_bad_weekday() -> None:
    with pytest.raises(InvalidTrigger):
        validate_schedule_trigger(
            {"weekdays": [7], "time": "07:30", "timezone": "Europe/Istanbul"}
        )


def test_validate_schedule_trigger_rejects_bad_time_format() -> None:
    with pytest.raises(InvalidTrigger):
        validate_schedule_trigger(
            {"weekdays": [0], "time": "7:30", "timezone": "Europe/Istanbul"}
        )


def test_validate_schedule_trigger_normalizes_and_defaults_grace() -> None:
    normalized = validate_schedule_trigger(
        {"weekdays": [2, 0, 0], "time": "07:30", "timezone": "Europe/Istanbul"}
    )
    assert normalized["weekdays"] == [0, 2]  # deduped and sorted
    assert normalized["grace_minutes"] == 5
    assert normalized["timezone"] == "Europe/Istanbul"


def test_validate_presence_trigger_rejects_unknown_event() -> None:
    with pytest.raises(InvalidTrigger):
        validate_presence_trigger({"event": "owner.teleported"})


def test_validate_presence_trigger_accepts_known_event() -> None:
    normalized = validate_presence_trigger({"event": "owner.returned"})
    assert normalized == {"event": "owner.returned"}


def test_validate_trigger_dispatches_on_kind() -> None:
    with pytest.raises(InvalidTrigger):
        validate_trigger("not_a_kind", {})


# --------------------------------------------------------------------------- "at" due-check


def test_at_trigger_not_due_before_target() -> None:
    trigger = validate_at_trigger({"at": "2026-09-05T07:30:00Z"})
    now = datetime(2026, 9, 5, 7, 29, tzinfo=UTC)
    due, key = check_at_due(trigger, now)
    assert due is False
    assert key is None


def test_at_trigger_due_at_and_after_target() -> None:
    trigger = validate_at_trigger({"at": "2026-09-05T07:30:00Z"})
    for now in (
        datetime(2026, 9, 5, 7, 30, tzinfo=UTC),
        datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
    ):
        due, key = check_at_due(trigger, now)
        assert due is True
        assert key == "once"


# ---------------------------------------------------------------- "schedule" due-check


def test_schedule_trigger_recurring_weekday_alarm() -> None:
    """Mon/Wed/Fri 07:30 Europe/Istanbul: fires inside the grace window on a listed
    weekday, not on an unlisted one, and not outside the window."""
    trigger = validate_schedule_trigger(
        {"weekdays": [0, 2, 4], "time": "07:30", "timezone": "Europe/Istanbul"}
    )
    istanbul = ZoneInfo("Europe/Istanbul")

    # Wednesday 2026-09-09, 07:32 local — inside the 5-minute grace window.
    wednesday_in_window = datetime(2026, 9, 9, 7, 32, tzinfo=istanbul).astimezone(UTC)
    due, key = check_schedule_due(trigger, wednesday_in_window)
    assert due is True
    assert key == "2026-09-09"

    # Tuesday 2026-09-08, same time-of-day — not a listed weekday.
    tuesday_same_time = datetime(2026, 9, 8, 7, 32, tzinfo=istanbul).astimezone(UTC)
    due, key = check_schedule_due(trigger, tuesday_same_time)
    assert due is False
    assert key is None

    # Wednesday, but 20 minutes late — outside the grace window.
    wednesday_late = datetime(2026, 9, 9, 7, 50, tzinfo=istanbul).astimezone(UTC)
    due, key = check_schedule_due(trigger, wednesday_late)
    assert due is False
    assert key is None

    # Wednesday, one minute before — not due yet.
    wednesday_early = datetime(2026, 9, 9, 7, 29, tzinfo=istanbul).astimezone(UTC)
    due, key = check_schedule_due(trigger, wednesday_early)
    assert due is False
    assert key is None


def test_schedule_trigger_dst_boundary_same_local_time_both_sides() -> None:
    """The property the task brief demands by name: a DST transition must not silently
    shift an alarm. Europe/Berlin observes DST (Europe/Istanbul currently does not, but the
    mechanism must be correct for any zone an owner-relevant integration might use); local
    09:02 must be recognised as due on both sides of the spring-forward transition even
    though the two instants are more than 24h * N apart in UTC. A fixed-UTC-offset
    implementation would fire an hour early or late on one side without ever raising."""
    trigger = validate_schedule_trigger(
        {"weekdays": list(range(7)), "time": "09:00", "timezone": "Europe/Berlin"}
    )
    berlin = ZoneInfo("Europe/Berlin")

    before_transition = datetime(2026, 3, 28, 9, 2, tzinfo=berlin)  # CET, UTC+1
    after_transition = datetime(2026, 3, 30, 9, 2, tzinfo=berlin)  # CEST, UTC+2
    # Sanity: the fixture really does straddle a UTC-offset change, or this test would not
    # be exercising DST at all.
    assert before_transition.utcoffset() != after_transition.utcoffset()

    due_before, _ = check_schedule_due(trigger, before_transition.astimezone(UTC))
    due_after, _ = check_schedule_due(trigger, after_transition.astimezone(UTC))
    assert due_before is True
    assert due_after is True


def test_schedule_trigger_handles_nonexistent_local_time_without_crashing() -> None:
    """02:30 Berlin does not exist on the spring-forward date (clocks jump 02:00->03:00).
    The check must not raise — it may report due or not, but never crash the caller."""
    trigger = validate_schedule_trigger(
        {"weekdays": list(range(7)), "time": "02:30", "timezone": "Europe/Berlin"}
    )
    # A UTC instant that lands inside the imaginary local window on the transition day.
    now = datetime(2026, 3, 29, 1, 15, tzinfo=UTC)
    check_schedule_due(trigger, now)  # must not raise


# ---------------------------------------------------------------- "presence" due-check


def _event(event_id: str, state: str, sequence: int) -> SimpleNamespace:
    return SimpleNamespace(event_id=event_id, state=state, sequence=sequence)


def test_presence_trigger_fires_on_matching_event() -> None:
    trigger = validate_presence_trigger({"event": "owner.returned"})
    tail = [_event("evt-1", "owner.awake", 5), _event("evt-2", "owner.returned", 6)]
    due, key, watermark = check_presence_due(trigger, tail, after_sequence=0)
    assert due is True
    assert key == "evt-2"
    assert watermark == 6


def test_presence_trigger_does_not_fire_on_unrelated_event() -> None:
    trigger = validate_presence_trigger({"event": "owner.returned"})
    tail = [_event("evt-1", "owner.awake", 3)]
    due, key, watermark = check_presence_due(trigger, tail, after_sequence=0)
    assert due is False
    assert key is None
    assert watermark == 3  # watermark still advances so evt-1 is never rescanned


def test_presence_trigger_collapses_a_burst_to_one_firing_per_call() -> None:
    trigger = validate_presence_trigger({"event": "owner.returned"})
    tail = [_event("evt-1", "owner.returned", 1), _event("evt-2", "owner.returned", 2)]
    due, key, watermark = check_presence_due(trigger, tail, after_sequence=0)
    assert due is True
    assert key == "evt-1"  # the first match in the batch, not both
    assert watermark == 2  # but the watermark still advances past everything scanned
