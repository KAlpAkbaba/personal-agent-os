"""B14 req 290/291/294/299: pausing a routine, and a trigger about a STATE.

Two additions with one thing in common: both are about a routine NOT firing, which is the
half of a routine engine that is easy to get wrong quietly. A pause that still fired would
be a routine the owner believes is off. A condition trigger without an edge would fire on
every tick for as long as the owner was away from the keyboard — dozens of times an hour,
each one a real dispatch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger.models import ActivityEventRow
from app.routines import service as routines_service
from app.routines import triggers as triggers_mod
from app.routines.conditions import RoutineConditionContext
from app.routines.models import (
    ROUTINE_STATUS_ARMED,
    ROUTINE_STATUS_CANCELLED,
    ROUTINE_STATUS_PAUSED,
    TRIGGER_KIND_CONDITION,
    Routine,
    RoutineFiring,
)
from app.routines.state import IllegalRoutineTransition
from app.routines.triggers import InvalidTrigger

NOW = datetime(2026, 9, 9, 4, 0, tzinfo=UTC)
ALL_TABLES = [Routine.__table__, RoutineFiring.__table__, ActivityEventRow.__table__]


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ALL_TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


class _Counting:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, *, routine_id, action, firing_id, now=None):  # noqa: ANN001, ARG002
        from app.routines.actions import DispatchOutcome

        self.calls += 1
        return DispatchOutcome.succeeded({})


def _at_routine(session, *, when=None):
    return routines_service.create_routine(
        session,
        name="Sabah rutini",
        trigger_kind="at",
        trigger={"at": (when or NOW).isoformat()},
        actions=[{"kind": "voice_briefing", "detail": {"text": "Günaydın"}}],
        source="test",
    )


def _idle_routine(session, *, min_seconds: int = 600):
    return routines_service.create_routine(
        session,
        name="Boşta kalınca",
        trigger_kind=TRIGGER_KIND_CONDITION,
        trigger={"kind": "device_idle", "min_seconds": min_seconds},
        actions=[{"kind": "voice_briefing", "detail": {"text": "Günaydın"}}],
        source="test",
    )


def _idle(seconds: float | None) -> RoutineConditionContext:
    return RoutineConditionContext(device_idle_s=seconds)


# --------------------------------------------------------------------- 290: pause


def test_a_paused_routine_does_not_fire(session):
    """The whole claim. Everything else about pausing is bookkeeping."""
    routine = _at_routine(session)
    routines_service.pause_routine(session, routine.routine_id, reason="bu hafta")
    dispatcher = _Counting()

    routines_service.evaluate_due(session, now=NOW + timedelta(minutes=1), dispatcher=dispatcher)

    assert dispatcher.calls == 0


def test_a_paused_routine_keeps_its_id_and_its_history(session):
    """Why `paused` had to exist at all. Before it, honouring "bu hafta durdur" meant
    cancel-and-recreate, which loses the id, the firings and the ledger trail."""
    # A CONDITION routine, because an `at` one resolves itself to `completed` the moment it
    # fires - and a completed routine has nothing to pause, which is a different (correct)
    # refusal and would hide what this test is about.
    routine = _idle_routine(session)
    routines_service.evaluate_due(
        session, now=NOW, context=_idle(900), dispatcher=_Counting()
    )
    before = routines_service.list_firings(session, routine.routine_id)
    assert before, "the routine has a history to keep"

    routines_service.pause_routine(session, routine.routine_id)

    assert session.get(Routine, routine.routine_id) is not None
    assert len(routines_service.list_firings(session, routine.routine_id)) == len(before)


def test_pausing_is_idempotent(session):
    routine = _at_routine(session)
    routines_service.pause_routine(session, routine.routine_id)

    again = routines_service.pause_routine(session, routine.routine_id)

    assert again.status == ROUTINE_STATUS_PAUSED


def test_a_cancelled_routine_cannot_be_paused(session):
    """A resolved routine has nothing to pause, and saying "done" to that would let the
    owner believe they had turned off something that was never going to run again."""
    routine = _at_routine(session)
    routines_service.cancel_routine(session, routine.routine_id)

    with pytest.raises(IllegalRoutineTransition):
        routines_service.pause_routine(session, routine.routine_id)


def test_a_paused_routine_can_still_be_cancelled(session):
    """Pausing must not trap the owner: "actually, get rid of it" has to keep working."""
    routine = _at_routine(session)
    routines_service.pause_routine(session, routine.routine_id)

    cancelled = routines_service.cancel_routine(session, routine.routine_id)

    assert cancelled.status == ROUTINE_STATUS_CANCELLED


def test_the_pause_and_the_resume_are_both_in_the_ledger(session):
    """Their own event types, not a `routine.cancelled` with a flag: "turned off for a
    while" and "ended" are different facts, and a history that cannot tell them apart
    cannot answer "why did my morning routine stop running?"."""
    routine = _at_routine(session)
    routines_service.pause_routine(session, routine.routine_id, reason="tatil")
    routines_service.resume_routine(session, routine.routine_id)

    kinds = [r.event_type for r in session.query(ActivityEventRow).all()]

    assert "routine.paused" in kinds
    assert "routine.resumed" in kinds


# -------------------------------------------------------------------- 291: resume


def test_a_resumed_routine_fires_again(session):
    routine = _at_routine(session)
    routines_service.pause_routine(session, routine.routine_id)
    routines_service.resume_routine(session, routine.routine_id)
    dispatcher = _Counting()

    routines_service.evaluate_due(session, now=NOW + timedelta(minutes=1), dispatcher=dispatcher)

    assert dispatcher.calls == 1


def test_resuming_does_not_replay_what_it_slept_through(session):
    """The owner asked for those mornings not to happen. Firing four of them at once on
    resume would be the opposite of what "durdur" means."""
    routine = routines_service.create_routine(
        session,
        name="Hafta içi",
        trigger_kind="schedule",
        trigger={"weekdays": [0, 1, 2, 3, 4], "time": "07:15", "timezone": "Europe/Istanbul"},
        actions=[{"kind": "voice_briefing", "detail": {"text": "Günaydın"}}],
        source="test",
    )
    routines_service.pause_routine(session, routine.routine_id)
    routines_service.resume_routine(session, routine.routine_id)
    dispatcher = _Counting()

    # Noon: past this morning's 07:15 and nowhere near tomorrow's.
    routines_service.evaluate_due(
        session, now=NOW.replace(hour=9), dispatcher=dispatcher
    )

    assert dispatcher.calls == 0


def test_resuming_is_idempotent(session):
    routine = _at_routine(session)

    assert routines_service.resume_routine(session, routine.routine_id).status == (
        ROUTINE_STATUS_ARMED
    )


# ------------------------------------------------------- 294/299: the condition trigger


def test_a_condition_trigger_fires_when_the_machine_goes_idle(session):
    _idle_routine(session)
    dispatcher = _Counting()

    routines_service.evaluate_due(
        session, now=NOW, context=_idle(900), dispatcher=dispatcher
    )

    assert dispatcher.calls == 1


def test_it_fires_once_per_crossing_not_once_per_tick(session):
    """The defect an edge exists to prevent. The clock runs every ten seconds; a trigger
    that fired whenever the condition HELD would dispatch six times a minute for as long as
    the owner was away from the keyboard."""
    _idle_routine(session)
    dispatcher = _Counting()

    for minute in range(10):
        routines_service.evaluate_due(
            session,
            now=NOW + timedelta(minutes=minute),
            context=_idle(900 + minute * 60),
            dispatcher=dispatcher,
        )

    assert dispatcher.calls == 1


def test_coming_back_and_going_away_again_is_a_second_crossing(session):
    """And the falling edge has to be recorded even though nothing fires on it — or the
    next rise would not look like a rise."""
    _idle_routine(session)
    dispatcher = _Counting()

    routines_service.evaluate_due(session, now=NOW, context=_idle(900), dispatcher=dispatcher)
    routines_service.evaluate_due(
        session, now=NOW + timedelta(minutes=1), context=_idle(2), dispatcher=dispatcher
    )
    routines_service.evaluate_due(
        session, now=NOW + timedelta(minutes=2), context=_idle(900), dispatcher=dispatcher
    )

    assert dispatcher.calls == 2


def test_it_does_not_fire_below_the_threshold(session):
    _idle_routine(session, min_seconds=600)
    dispatcher = _Counting()

    routines_service.evaluate_due(session, now=NOW, context=_idle(599), dispatcher=dispatcher)

    assert dispatcher.calls == 0


def test_unknown_is_not_met(session):
    """Fail closed, the same rule the conditions follow. A process with no device status
    must never act as though the owner had walked away."""
    _idle_routine(session)
    dispatcher = _Counting()

    routines_service.evaluate_due(session, now=NOW, context=_idle(None), dispatcher=dispatcher)
    routines_service.evaluate_due(
        session, now=NOW + timedelta(minutes=1), context=RoutineConditionContext(),
        dispatcher=dispatcher,
    )

    assert dispatcher.calls == 0


def test_the_edge_is_remembered_on_the_row(session):
    routine = _idle_routine(session)

    routines_service.evaluate_due(session, now=NOW, context=_idle(900), dispatcher=_Counting())
    session.refresh(routine)

    assert routine.last_condition_met is True
    assert routine.last_condition_at is not None


def test_a_paused_condition_routine_does_not_even_watch(session):
    """Belt and braces: `evaluate_due` selects armed routines, so a paused one is not
    evaluated at all — its edge state stays where the owner left it."""
    routine = _idle_routine(session)
    routines_service.pause_routine(session, routine.routine_id)

    routines_service.evaluate_due(session, now=NOW, context=_idle(900), dispatcher=_Counting())
    session.refresh(routine)

    assert routine.last_condition_met is False


# ---------------------------------------------------------- the trigger's own validation


def test_an_unknown_condition_kind_is_refused():
    """A closed vocabulary, because every kind has to be answerable from the context. One
    that is not would let the owner create a routine the engine can never fire."""
    with pytest.raises(InvalidTrigger):
        triggers_mod.validate_condition_trigger({"kind": "owner_is_hungry"})


def test_a_one_second_edge_is_refused():
    """It would fire between two keystrokes."""
    with pytest.raises(InvalidTrigger):
        triggers_mod.validate_condition_trigger({"kind": "device_idle", "min_seconds": 1})


def test_the_threshold_defaults_to_ten_minutes():
    normalized = triggers_mod.validate_condition_trigger({"kind": "device_idle"})

    assert normalized == {"kind": "device_idle", "min_seconds": 600}


# ------------------------------------- the wiring, because "written and unwired" is the
# ------------------------------------- defect this repository keeps paying for


def test_the_owner_s_idle_time_is_the_lowest_across_their_machines():
    """"The owner is idle" is a claim about the OWNER, not about a machine.

    A laptop shut since Friday says nothing about somebody sitting at their desktop right
    now - taking the highest would call them idle while they typed, and a routine that
    dimmed the screen they were reading would be the result.
    """
    from app.devices.status import DeviceStatusRegistry, lowest_idle_seconds

    registry = DeviceStatusRegistry()
    registry.record(uuid.uuid4(), {"input_idle_s": 4000.0})
    registry.record(uuid.uuid4(), {"input_idle_s": 3.0})

    assert lowest_idle_seconds(registry) == 3.0


def test_no_device_reporting_is_unknown_and_not_zero():
    """Unknown must not read as "the owner is right here" either - `None` is what the
    condition trigger fails closed on."""
    from app.devices.status import DeviceStatusRegistry, lowest_idle_seconds

    assert lowest_idle_seconds(DeviceStatusRegistry()) is None


def test_the_application_feeds_the_idle_time_into_the_condition_context():
    """The guard. A condition trigger that never sees an idle time is a routine that can
    never fire, and it looks exactly like one that is simply waiting - which is how three
    retention sweeps, a speaker verdict and a notification ladder shipped unreached.

    Read from `create_app`'s OWN evaluate_due closure, through the clock it builds, so this
    fails if the wiring is removed rather than if a comment changes.
    """
    from app.config import Settings
    from app.devices.status import DeviceStatusRegistry, set_status_registry
    from app.main import create_app

    registry = DeviceStatusRegistry()
    registry.record(uuid.uuid4(), {"input_idle_s": 1234.0})
    set_status_registry(registry)
    try:
        app = create_app(Settings(_env_file=None))
        seen: list[float | None] = []

        def _capture(session, *, now=None, context=None, dispatcher=None):  # noqa: ANN001, ARG001
            seen.append(getattr(context, "device_idle_s", None))
            return None

        import app.routines.service as routines_module

        # A session that answers the one read the closure makes on its way to building the
        # context (the greeting decision reads the ledger). Not a database: the point is
        # the CONTEXT the closure assembles, and a unit test must not reach a real one.
        class _EmptyResult:
            def scalars(self):  # noqa: ANN202
                return self

            def all(self):  # noqa: ANN202
                return []

            def first(self):  # noqa: ANN202
                return None

        class _StubSession:
            def execute(self, *_args, **_kwargs):  # noqa: ANN202
                return _EmptyResult()

        original = routines_module.evaluate_due
        routines_module.evaluate_due = _capture
        try:
            app.state.routine_clock._evaluate_due(_StubSession(), NOW)
        finally:
            routines_module.evaluate_due = original
    finally:
        set_status_registry(DeviceStatusRegistry())

    assert seen == [1234.0]
