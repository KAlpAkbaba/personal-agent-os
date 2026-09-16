"""B47 (B13 requirement 259's local trigger): the cloud adopts a snooze the device made alone.

The owner said "ertele" to the Windows device while the Cloud Core was unreachable. The
device stopped the ring, armed the same alarm again and reported the instant it will ring.
These tests hold the cloud half: the terms the device may use come FROM the alarm row, the
report is adopted with the device's instant (not re-derived from when it arrived), and the
limit, the ledger and the heartbeat path are the ordinary ones.

SQLite, a fake device port, fixed instants. Nothing sleeps and nothing dials.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.alarms import service as alarms_service
from app.alarms.models import (
    MAX_SNOOZE_COUNT,
    STATE_CANCELLED,
    STATE_PLAYING,
    STATE_SCHEDULED,
    WakeAlarm,
)
from app.alarms.sequence import WakeSequence
from app.alarms.tr_time import parse_when_struct
from app.ambient import ingest
from app.devices.status import DeviceStatusRegistry
from app.ledger.models import ActivityEventRow
from app.uistate.publisher import UiStatePublisher, get_publisher, set_publisher
from tests.alarms_support import FakeDeviceAction, build_session_factory, happy_device_results

IST = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 9, 16, 6, 0, tzinfo=IST).astimezone(UTC)


@pytest.fixture()
def session():
    factory = build_session_factory()
    with factory() as s:
        yield s


@pytest.fixture()
def device():
    return FakeDeviceAction(results=happy_device_results())


@pytest.fixture(autouse=True)
def _fresh_publisher():
    previous = get_publisher()
    set_publisher(UiStatePublisher())
    yield
    set_publisher(previous)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _ringing(session, **kwargs) -> WakeAlarm:
    alarm = alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 5}, now=NOW), **kwargs
    )
    # The device rang its own fallback; the cloud learns it first (ingest order).
    alarms_service.reconcile_local_fired(session, [str(alarm.id)], now=NOW)
    session.refresh(alarm)
    assert alarm.state == STATE_PLAYING
    return alarm


def _local_snoozed(session) -> list[ActivityEventRow]:
    return [
        r for r in session.query(ActivityEventRow).all() if r.event_type == "alarm.local_snoozed"
    ]


def test_the_arm_and_the_ring_carry_the_alarm_rows_snooze_terms(session, device):
    sequence = WakeSequence(device_action=device, tts=None)
    alarm = alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 600}, now=NOW), snooze_minutes=7
    )
    sequence.arm(session, alarm, now=NOW)
    arm = device.payload_for("desktop.alarm_arm")
    assert arm is not None
    assert arm["snooze_minutes"] == 7
    assert arm["snoozes_left"] == MAX_SNOOZE_COUNT

    alarm.snooze_count = MAX_SNOOZE_COUNT - 1
    session.commit()
    sequence._start_tone(session, alarm, firing_id=None, now=NOW)
    tone = device.payload_for("desktop.alarm_start")
    assert tone is not None
    assert tone["snooze_minutes"] == 7
    assert tone["snoozes_left"] == 1


def test_a_local_snooze_is_adopted_at_the_devices_instant_not_the_reports(session):
    alarm = _ringing(session, snooze_minutes=9)
    until = NOW + timedelta(minutes=9)
    # The report arrives 4 min 25 s after the snooze happened - not a whole number of minutes,
    # so re-deriving the instant from the arrival (rounded to minutes) would land elsewhere.
    arrived = NOW + timedelta(minutes=4, seconds=25)

    touched = alarms_service.reconcile_local_snoozed(session, [(str(alarm.id), until)], now=arrived)

    session.refresh(alarm)
    assert [a.id for a in touched] == [alarm.id]
    assert alarm.state == STATE_SCHEDULED
    assert _aware(alarm.scheduled_for) == until
    assert alarm.snooze_count == 1
    rows = _local_snoozed(session)
    assert len(rows) == 1 and rows[0].action == "alarm_local_snoozed"
    assert rows[0].detail_json["until"] == until.isoformat()


def test_a_snooze_for_an_alarm_that_is_no_longer_ringing_is_recorded_not_applied(session, device):
    alarm = alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 600}, now=NOW)
    )
    alarms_service.cancel_alarm(
        session, alarm.id, sequence=WakeSequence(device_action=device, tts=None)
    )
    until = NOW + timedelta(minutes=9)

    assert alarms_service.reconcile_local_snoozed(session, [(str(alarm.id), until)], now=NOW) == []
    assert alarms_service.reconcile_local_snoozed(session, [("not-a-uuid", until)], now=NOW) == []
    assert (
        alarms_service.reconcile_local_snoozed(session, [(str(uuid.uuid4()), until)], now=NOW) == []
    )

    session.refresh(alarm)
    assert alarm.state == STATE_CANCELLED
    rows = _local_snoozed(session)
    assert len(rows) == 1 and rows[0].action == "alarm_local_snooze_not_adopted"


def test_the_clouds_limit_still_holds_for_a_snooze_the_device_made(session):
    alarm = _ringing(session)
    alarm.snooze_count = MAX_SNOOZE_COUNT
    session.commit()

    touched = alarms_service.reconcile_local_snoozed(
        session, [(str(alarm.id), NOW + timedelta(minutes=9))], now=NOW
    )

    session.refresh(alarm)
    assert touched == []
    assert alarm.state == STATE_PLAYING
    assert _local_snoozed(session)[0].action == "alarm_local_snooze_not_adopted"


def test_the_heartbeat_path_reconciles_the_ring_first_and_the_snooze_once(session):
    """One heartbeat can carry both facts: the device rang its fallback (the cloud never saw
    it) and then snoozed it. Fired first, or the snooze would be refused as not ringing."""
    alarm = alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 5}, now=NOW), snooze_minutes=5
    )
    until = NOW + timedelta(minutes=5)
    device_id = uuid.uuid4()
    registry = DeviceStatusRegistry()
    status = {
        "display_state": "on",
        "local_alarm_fired": [str(alarm.id)],
        "local_alarm_snoozed": [{"alarm_id": str(alarm.id), "until": until.isoformat()}],
        "voice": {
            "state": "offline",
            "indicator": "listening",
            "mode": "continuous",
            "listening": True,
            "mic_muted": False,
            "cloud_connected": False,
            "restarts": 0,
            "last_error": None,
        },
    }

    first = ingest.ingest_status(session, device_id, status, now=NOW, statuses=registry)
    second = ingest.ingest_status(
        session, device_id, status, now=NOW + timedelta(seconds=10), statuses=registry
    )

    session.refresh(alarm)
    assert first.reconciled_alarms == (str(alarm.id),)
    assert first.snoozed_alarms == (str(alarm.id),)
    assert second.snoozed_alarms == ()
    assert alarm.state == STATE_SCHEDULED
    assert _aware(alarm.scheduled_for) == until
    assert alarm.snooze_count == 1
    assert registry.get(device_id).voice["state"] == "offline"
