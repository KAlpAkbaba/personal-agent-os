"""B13 req 285: what actually happened to the owner's alarms, read back.

The one claim worth testing is the one the alarm ROW cannot make. A recurring alarm reuses
its row: ``_release`` rewinds ``terminal_state``, ``terminal_at`` and ``terminal_reason`` to
``None`` when it re-schedules for tomorrow, so the row can only ever describe the NEXT
occurrence. "Did my 07:30 ring on Tuesday?" is unanswerable from it by construction.

Every one of these tests therefore drives the REAL transitions and reads the REAL ledger
rows back. A history test that built its own rows would prove that a list comprehension
works.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.alarms import history as alarm_history
from app.alarms import service as alarms_service
from app.alarms.models import STATE_FIRING, STATE_PLAYING, STATE_STOPPED
from app.alarms.sequence import WakeSequence
from app.alarms.tr_time import parse_when_struct
from tests.alarms_support import FakeDeviceAction, build_session_factory, happy_device_results

NOW = datetime(2026, 9, 9, 4, 0, tzinfo=UTC)


@pytest.fixture()
def session():
    with build_session_factory()() as s:
        yield s


@pytest.fixture()
def sequence():
    return WakeSequence(device_action=FakeDeviceAction(results=happy_device_results()), tts=None)


def _create(session, **kwargs):
    when = kwargs.pop("when", None) or parse_when_struct({"relative_seconds": 60}, now=NOW)
    return alarms_service.create_alarm(session, when=when, **kwargs)


def _ring(session, alarm, *, media_kind: str | None = None, now=NOW) -> None:
    """Drive the real transitions the machine allows: SCHEDULED -> FIRING -> PLAYING.
    Jumping straight to PLAYING is illegal, and a test that bypassed the guard would be
    recording a history the state machine can never produce."""
    alarms_service.transition(session, alarm, STATE_FIRING, now=now)
    alarms_service.transition(session, alarm, STATE_PLAYING, now=now, media_kind=media_kind)


def _kinds(entries) -> list[str]:
    return [e["event_type"] for e in entries]


# ------------------------------------------------------- the row cannot answer this


def test_a_recurring_alarm_keeps_yesterday_even_though_its_row_does_not(session, sequence):
    """The defect this requirement names. `_release` rewinds the terminal fields so the row
    describes tomorrow; the ledger keeps each occurrence, because its idempotency key
    contains the occurrence."""
    alarm = _create(
        session, when=parse_when_struct({"time": "07:30", "weekdays": [1, 2, 3]}, now=NOW)
    )
    alarms_service.transition(session, alarm, STATE_STOPPED, now=NOW, reason="owner_stopped")
    alarms_service._release(session, alarm, sequence=sequence, reason="completed", now=NOW)
    session.refresh(alarm)

    # The row has forgotten: it is scheduled again, with nothing terminal on it.
    assert alarm.terminal_state is None
    assert alarm.terminal_reason is None

    # The history has not.
    entries = alarm_history.alarm_history(session, alarm_id=alarm.id)
    assert "alarm.stopped" in _kinds(entries)


def test_the_history_is_about_one_alarm_when_it_is_asked_to_be(session):
    mine = _create(session)
    other = _create(session)
    alarms_service.transition(session, mine, STATE_STOPPED, now=NOW, reason="owner_stopped")
    alarms_service.transition(session, other, STATE_STOPPED, now=NOW, reason="owner_stopped")

    entries = alarm_history.alarm_history(session, alarm_id=mine.id)

    assert entries, "the alarm has a story"
    assert {e["alarm_id"] for e in entries} == {str(mine.id)}


def test_an_unknown_alarm_has_an_empty_history_rather_than_everyone_else_s(session):
    _create(session)

    assert alarm_history.alarm_history(session, alarm_id=uuid.uuid4()) == []


# --------------------------------------------------------------- what an entry says


def test_an_entry_names_the_state_it_recorded(session):
    alarm = _create(session)
    alarms_service.transition(session, alarm, STATE_STOPPED, now=NOW, reason="owner_stopped")

    stopped = next(e for e in alarm_history.alarm_history(session, alarm_id=alarm.id)
                   if e["event_type"] == "alarm.stopped")

    assert stopped["state"] == "STOPPED"
    assert stopped["reason"] == "owner_stopped"
    assert stopped["occurred_at"] is not None


def test_how_the_owner_was_woken_is_in_the_entry(session):
    """"How were you woken?" is the question this history is read for."""
    alarm = _create(session)
    _ring(session, alarm, media_kind="tone_fallback")

    playing = next(e for e in alarm_history.alarm_history(session, alarm_id=alarm.id)
                   if e["event_type"] == "alarm.playing")

    assert playing["media_kind"] == "tone_fallback"


def test_the_entries_come_back_newest_first(session):
    """Asserted as an ORDERING of what came back, not as "row X is at index 0".

    The ledger stamps `occurred_at` with its own `utcnow()`, not with the clock the caller
    passed - which is right for a history (it records when the row was written) and means a
    test cannot place three transitions at three chosen instants. Pinning an index would be
    pinning the resolution of the machine's clock; the claim is the order.
    """
    alarm = _create(session)
    _ring(session, alarm)
    alarms_service.transition(
        session, alarm, STATE_STOPPED, now=NOW + timedelta(minutes=5), reason="owner_stopped"
    )

    entries = alarm_history.alarm_history(session, alarm_id=alarm.id)

    assert len(entries) >= 3
    stamps = [e["occurred_at"] for e in entries]
    assert stamps == sorted(stamps, reverse=True)
    assert "alarm.stopped" in _kinds(entries)


def test_an_event_with_no_state_says_so_rather_than_inventing_one(session, sequence):
    """`local_fallback_rang` and `cleaned_up` are events, not states. Giving them a state
    would put a transition in the history that the machine never made."""
    alarm = _create(session)
    alarms_service.reconcile_local_fired(session, [str(alarm.id)], now=NOW)

    rang = next(e for e in alarm_history.alarm_history(session, alarm_id=alarm.id)
                if e["event_type"] == "alarm.local_fallback_rang")

    assert rang["state"] is None
    assert rang["media_kind"] == "local_fallback"


# -------------------------------------------------------------------- test alarms


def test_a_test_alarm_can_be_left_out(session):
    """req 285 + req 286 together: a history full of test rings is a history nobody reads."""
    real = _create(session)
    test = _create(session, is_test=True)
    alarms_service.transition(session, real, STATE_STOPPED, now=NOW, reason="owner_stopped")
    alarms_service.transition(session, test, STATE_STOPPED, now=NOW, reason="owner_stopped")

    without = alarm_history.alarm_history(session, include_tests=False)

    assert {e["alarm_id"] for e in without} == {str(real.id)}
    assert any(e["is_test"] for e in alarm_history.alarm_history(session))


# ------------------------------------------------------------------ the event list


def test_every_alarm_state_is_a_history_event(session):  # noqa: ARG001
    """Derived from the state map rather than listed, so a state added to the machine
    cannot be missing from the history. Read here so the derivation is not just a comment."""
    from app.ledger.vocabulary import ALARM_EVENT_TYPE_BY_STATE

    assert set(ALARM_EVENT_TYPE_BY_STATE.values()) <= set(alarm_history.ALARM_EVENT_TYPES)


def test_the_history_is_bounded(session):
    alarm = _create(session)
    _ring(session, alarm)
    alarms_service.transition(session, alarm, STATE_STOPPED, now=NOW, reason="owner_stopped")

    assert len(alarm_history.alarm_history(session, limit=1)) == 1
    assert len(alarm_history.alarm_history(session, limit=10_000)) <= alarm_history.MAX_LIMIT
