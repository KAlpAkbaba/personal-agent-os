"""B14 req 261/262/293: a recurring alarm, driven round the whole loop.

The matrix recorded 261 as MISSING with the note "none — all 7 production routines are
one-shot". That is a statement about the ROWS in production, not about the code, and the
two are different claims. The code has had the whole path since M18.3: `create_alarm` reads
a weekday set out of the owner's sentence, `_create_trigger_routine` makes a `schedule`
routine instead of an `at` one, and `_release` moves the alarm forward while that routine
stays armed.

Two existing tests pin the ends of that path — the routine is created with the right
weekdays, and the alarm re-schedules rather than going terminal. Neither drives the middle:
that the routine ENGINE fires the alarm on a listed weekday, does NOT on an unlisted one,
and comes back the following week. Without that, "recurring alarms work" was an inference
across three test files.

So this file measures it, through `evaluate_due` and the real `WakeAlarmRunner` seam, with
a clock walked across nine days. The one thing deliberately faked is the device.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.alarms import service as alarms_service
from app.alarms.models import STATE_SCHEDULED, WakeAlarm
from app.alarms.routine_port import WakeAlarmRunner
from app.alarms.sequence import WakeSequence
from app.alarms.service import _aware
from app.alarms.tr_time import parse_when_text
from app.routines import service as routines_service
from app.routines.actions import ACTION_KIND_WAKE_ALARM, DispatchOutcome
from app.routines.models import ROUTINE_STATUS_ARMED, Routine
from tests.alarms_support import FakeDeviceAction, build_session_factory, happy_device_results

ISTANBUL = ZoneInfo("Europe/Istanbul")

#: Monday 2026-09-07, 04:00 UTC = 07:00 Istanbul. Named, because every instant below is
#: derived from it and a reader has to be able to check the weekday arithmetic by hand.
MONDAY = datetime(2026, 9, 7, 4, 0, tzinfo=UTC)


class _WakeAlarmDispatcher:
    """The routine engine's dispatcher, wired to the REAL alarm seam.

    Only `wake_alarm` is handled, because that is the only action a recurring alarm creates
    and a dispatcher that quietly succeeded on anything else would let a broken action list
    pass as a fired alarm.
    """

    def __init__(self, runner: WakeAlarmRunner) -> None:
        self._runner = runner
        self.fired: list[datetime] = []

    def dispatch(
        self,
        action: dict[str, Any],
        *,
        routine_id: Any,
        firing_id: Any,
        now: datetime | None = None,
    ) -> DispatchOutcome:
        assert action["kind"] == ACTION_KIND_WAKE_ALARM, action
        import uuid as _uuid

        outcome = self._runner.fire(
            alarm_id=_uuid.UUID(action["detail"]["alarm_id"]),
            routine_id=routine_id,
            firing_id=firing_id,
            now=now,
        )
        if outcome.ok and now is not None:
            self.fired.append(now)
        return outcome


@pytest.fixture()
def factory():
    return build_session_factory()


@pytest.fixture()
def session(factory):
    with factory() as s:
        yield s


@pytest.fixture()
def dispatcher(factory):
    device = FakeDeviceAction(results=happy_device_results())
    return _WakeAlarmDispatcher(
        WakeAlarmRunner(
            session_factory=factory, sequence=WakeSequence(device_action=device, tts=None)
        )
    )


def _weekday_alarm(session) -> WakeAlarm:
    """"Her hafta içi 07:15'te beni uyandır." — the owner's own sentence, parsed."""
    return alarms_service.create_alarm(
        session, when=parse_when_text("Her hafta içi 07:15'te beni uyandır.", now=MONDAY)
    )


def _at(day_offset: int, local_hhmm: str) -> datetime:
    """A UTC instant at `local_hhmm` Istanbul, `day_offset` days after MONDAY."""
    hour, minute = (int(p) for p in local_hhmm.split(":"))
    day = (MONDAY.astimezone(ISTANBUL) + timedelta(days=day_offset)).date()
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ISTANBUL).astimezone(UTC)


def _tick(session, dispatcher, moment: datetime) -> None:
    """One routine-engine pass, then forget what this session thought it knew.

    `WakeAlarmRunner` fires in its OWN session on purpose (its module docstring: the wake
    sequence must commit as it goes). So after a tick, this session's identity map still
    holds the alarm as it was before the ring, and reading it without expiring would test
    a stale object rather than the database.
    """
    routines_service.evaluate_due(session, now=moment, dispatcher=dispatcher)
    session.expire_all()


# ------------------------------------------------------------------ the loop itself


def test_a_weekday_alarm_rings_on_a_weekday(session, dispatcher):
    alarm = _weekday_alarm(session)

    _tick(session, dispatcher, _at(1, "07:15"))  # Tuesday

    assert len(dispatcher.fired) == 1
    session.refresh(alarm)
    assert alarm.state != STATE_SCHEDULED, "the alarm is physically happening"


def test_a_weekday_alarm_does_not_ring_at_the_weekend(session, dispatcher):
    """The half that makes "recurring" mean something. A trigger that fired every day
    would satisfy "it rang on Tuesday" and wake the owner on Sunday."""
    _weekday_alarm(session)

    _tick(session, dispatcher, _at(5, "07:15"))  # Saturday
    _tick(session, dispatcher, _at(6, "07:15"))  # Sunday

    assert dispatcher.fired == []


def test_it_comes_back_the_next_week(session, dispatcher):
    """The claim the matrix's "7/7 one-shot" note is really about: not that it fires, but
    that it keeps firing. Eight days apart, so the second is the SAME weekday."""
    alarm = _weekday_alarm(session)

    _tick(session, dispatcher, _at(1, "07:15"))
    alarms_service.complete_alarm(session, alarm, sequence=None, now=_at(1, "07:20"))
    _tick(session, dispatcher, _at(8, "07:15"))

    assert len(dispatcher.fired) == 2


def test_the_routine_stays_armed_across_firings(session, dispatcher):
    """A one-shot routine resolves itself to `completed` after firing. A schedule routine
    must not: that is the difference between an alarm and an alarm that happened once."""
    alarm = _weekday_alarm(session)

    _tick(session, dispatcher, _at(1, "07:15"))
    alarms_service.complete_alarm(session, alarm, sequence=None, now=_at(1, "07:20"))

    assert session.get(Routine, alarm.routine_id).status == ROUTINE_STATUS_ARMED


def test_one_ring_per_morning_even_when_the_engine_ticks_repeatedly(session, dispatcher):
    """The engine runs every ten seconds. Within the schedule trigger's grace window that
    is dozens of ticks for one 07:15, and each one must find the morning already spoken
    for — by the firing row, not by luck."""
    _weekday_alarm(session)

    for minute in range(0, 5):
        _tick(session, dispatcher, _at(1, "07:15") + timedelta(minutes=minute))

    assert len(dispatcher.fired) == 1


def test_the_alarm_moves_itself_to_the_next_weekday(session, dispatcher):
    """Friday's alarm points at Monday, not at Saturday — `next_occurrence_after` honours
    the weekday set rather than adding a day."""
    alarm = _weekday_alarm(session)
    friday = _at(4, "07:15")

    _tick(session, dispatcher, friday)
    alarms_service.complete_alarm(session, alarm, sequence=None, now=friday + timedelta(minutes=5))
    session.refresh(alarm)

    assert alarm.state == STATE_SCHEDULED
    assert alarm.scheduled_for.astimezone(ISTANBUL).weekday() == 0  # Monday


# ---------------------------------------------------------- 262: an arbitrary weekday set


def test_a_custom_weekday_set_is_honoured_day_by_day(session, dispatcher):
    """req 262. "Pazartesi ve perşembe" is not a preset — the days come from the owner's
    sentence, and every other day of the week must stay silent."""
    alarms_service.create_alarm(
        session,
        when=parse_when_text("Her pazartesi ve perşembe 06:45'te beni uyandır.", now=MONDAY),
    )

    rang_on = []
    for offset in range(7):
        before = len(dispatcher.fired)
        _tick(session, dispatcher, _at(offset, "06:45"))
        if len(dispatcher.fired) > before:
            rang_on.append(offset)

    assert rang_on == [0, 3], (
        "Monday and Thursday, and nothing else. The schedule trigger answers about the "
        f"wall clock, not about when the alarm was created; got {rang_on}"
    )


# ------------------------------------- 261: the defect a missed morning used to cause


def test_a_recurring_alarm_that_missed_a_morning_still_rings_the_next_one(session, dispatcher):
    """The defect behind "MISSING: 7/7 one-shot", found by driving this loop.

    `scheduled_for` on a recurring alarm means "the next occurrence", and only `_release`
    kept that true — `_release` runs after a ring. So a morning that went by unrung (the
    machine was off, the process was down) left the row pointing at a day in the past for
    ever. The schedule routine kept firing on time and every one of those firings was
    refused as expired, because the ROW said the occurrence was yesterday.

    A recurring alarm that missed one morning was dead, silently, and the owner's only
    symptom was not being woken again.
    """
    _weekday_alarm(session)

    # Monday goes by with nothing running at all: no tick, no ring.
    _tick(session, dispatcher, _at(1, "07:15"))  # Tuesday

    assert len(dispatcher.fired) == 1


def test_the_row_catches_up_on_a_tick_even_before_the_next_ring(session, dispatcher):  # noqa: ARG001
    """And the row is honest in between, not only at fire time. "Sabah alarmım kaçta?" and
    /v1/state/now both read `scheduled_for`, and a row pointing at last Monday answers a
    question about the past."""
    alarm = _weekday_alarm(session)
    tuesday_noon = _at(1, "12:00")

    alarms_service.tick(session, sequence=None, now=tuesday_noon)
    session.refresh(alarm)

    # `_aware` because SQLite hands back naive datetimes where PostgreSQL hands back aware
    # ones, and `.astimezone()` on a naive value silently reads it as machine-local time.
    assert _aware(alarm.scheduled_for) > _at(1, "07:15")


def test_a_one_shot_alarm_is_never_moved(session, dispatcher):  # noqa: ARG001
    """The half that keeps the fix from becoming the bug. A one-shot alarm whose moment
    went by IS finished; moving it would invent a wake-up the owner never asked for."""
    alarm = alarms_service.create_alarm(
        session, when=parse_when_text("Yarın sabah 07:30'da beni uyandır.", now=MONDAY)
    )
    was = _aware(alarm.scheduled_for)

    alarms_service.tick(session, sequence=None, now=_at(6, "12:00"))
    session.refresh(alarm)

    assert _aware(alarm.scheduled_for) == was
