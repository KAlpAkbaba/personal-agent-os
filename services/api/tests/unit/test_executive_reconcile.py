"""A run may not say it is working when nothing of it could be.

Production run ``3ef7c639`` said ``running`` from 2026-09-09T01:18Z onward while all four
of its steps were terminal (``verified``, ``skipped``, ``failed``, ``failed_recoverable``).
The settlement logic that would have called it ``partial`` shipped AFTER those steps
settled, and the recompute only ever runs when a step settles - so no step of that run will
ever settle again, and the row is stuck for good. A Cloud Core restart did not clear it.

These tests hold the invariant itself, not the incident: **after a reconcile, no run is
``running`` with no in-flight child.** Plus the two guards that keep the reconciler from
doing harm - it waits for idleness so it cannot settle a run mid-flight, and it never
touches a run holding a step that something will act on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.executive.models import (
    STATE_PARTIAL,
    STATE_PAUSED,
    STATE_RUNNING,
    STEP_IN_FLIGHT_STATES,
    STEP_STATE_FAILED,
    STEP_STATE_FAILED_RECOVERABLE,
    STEP_STATE_READY,
    STEP_STATE_SKIPPED,
    STEP_STATE_VERIFIED,
    ExecutiveRunRow,
    ExecutiveStepRow,
)
from app.executive.reconcile import (
    RECONCILABLE_RUN_STATES,
    RECONCILE_IDLE_AFTER_S,
    reconcile_stalled_runs,
    stalled_run_ids,
)

NOW = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(seconds=RECONCILE_IDLE_AFTER_S * 2)


#: Only the two tables this reconciler touches, as `test_executive_service.py` does -
#: the suite stays independent of every other model's metadata.
TABLES = [ExecutiveRunRow.__table__, ExecutiveStepRow.__table__]


@pytest.fixture()
def factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _run(db: Session, *, state: str, updated_at: datetime, step_states: list[str]) -> uuid.UUID:
    run_id = uuid.uuid4()
    db.add(
        ExecutiveRunRow(
            id=run_id,
            goal="Bir sey arastir, rapor cikar.",
            graph_json={"steps": [{"step_id": f"s{i}"} for i in range(len(step_states))]},
            state=state,
            steps_total=len(step_states),
            steps_done=0,
            created_at=updated_at,
            updated_at=updated_at,
        )
    )
    for index, step_state in enumerate(step_states):
        db.add(
            ExecutiveStepRow(
                id=uuid.uuid4(),
                run_id=run_id,
                step_id=f"s{index}",
                kind="research.run",
                risk_class="read_only",
                postcondition_json={},
                timeout_s=60,
                state=step_state,
                created_at=updated_at,
                updated_at=updated_at,
            )
        )
    db.commit()
    return run_id


def test_the_production_shape_settles_to_partial(factory) -> None:
    """Run 3ef7c639, exactly: four steps, none in flight, and a row that says running."""
    with factory() as db:
        run_id = _run(
            db,
            state=STATE_RUNNING,
            updated_at=LONG_AGO,
            step_states=[
                STEP_STATE_VERIFIED,
                STEP_STATE_SKIPPED,
                STEP_STATE_FAILED,
                STEP_STATE_FAILED_RECOVERABLE,
            ],
        )

        assert reconcile_stalled_runs(db, now=NOW) == [run_id]

        run = db.get(ExecutiveRunRow, run_id)
        assert run is not None
        assert run.state == STATE_PARTIAL
        # `partial` is only honest if it NAMES what is missing (spec §3).
        assert set(run.partial_reasons_json or {}) == {"s1", "s2", "s3"}


def test_after_a_reconcile_no_run_claims_to_be_working_with_nothing_in_flight(factory) -> None:
    """The invariant, stated as one: this is what may never be true again."""
    with factory() as db:
        for states in (
            [STEP_STATE_VERIFIED],
            [STEP_STATE_FAILED, STEP_STATE_FAILED_RECOVERABLE],
            [STEP_STATE_SKIPPED, STEP_STATE_VERIFIED, STEP_STATE_FAILED],
            [STEP_STATE_FAILED_RECOVERABLE],
        ):
            _run(db, state=STATE_RUNNING, updated_at=LONG_AGO, step_states=states)

        reconcile_stalled_runs(db, now=NOW)

        for run in db.query(ExecutiveRunRow).all():
            if run.state != STATE_RUNNING:
                continue
            steps = db.query(ExecutiveStepRow).filter_by(run_id=run.id).all()
            assert any(s.state in STEP_IN_FLIGHT_STATES for s in steps), (
                f"run {run.id} says running with no step in flight: "
                f"{[s.state for s in steps]}"
            )


@pytest.mark.parametrize("in_flight", sorted(STEP_IN_FLIGHT_STATES))
def test_a_run_with_anything_in_flight_is_left_alone_however_old(factory, in_flight) -> None:
    """Idleness is not evidence of stopping: a long step is idle by design, and heartbeats.

    Parametrised over every in-flight state so a new one added to the set cannot quietly
    fall outside this guard.
    """
    with factory() as db:
        run_id = _run(
            db,
            state=STATE_RUNNING,
            updated_at=LONG_AGO,
            step_states=[STEP_STATE_VERIFIED, in_flight],
        )

        assert stalled_run_ids(db, now=NOW) == []
        assert reconcile_stalled_runs(db, now=NOW) == []
        assert db.get(ExecutiveRunRow, run_id).state == STATE_RUNNING


def test_a_run_that_only_just_stopped_is_not_pounced_on(factory) -> None:
    """The race the idle bound removes rather than narrows.

    Between a step's own `db.commit()` and its recompute there is a real instant where a
    live run holds no in-flight step. A reconciler without the bound would settle a run
    that was about to carry on.
    """
    with factory() as db:
        run_id = _run(
            db,
            state=STATE_RUNNING,
            updated_at=NOW - timedelta(seconds=1),
            step_states=[STEP_STATE_VERIFIED, STEP_STATE_FAILED],
        )

        assert reconcile_stalled_runs(db, now=NOW) == []
        assert db.get(ExecutiveRunRow, run_id).state == STATE_RUNNING

        # ...and it does settle once the bound has genuinely passed.
        later = NOW + timedelta(seconds=RECONCILE_IDLE_AFTER_S + 1)
        assert reconcile_stalled_runs(db, now=later) == [run_id]


def test_a_paused_run_is_the_owners_and_is_never_settled(factory) -> None:
    """A paused run has no in-flight steps ON PURPOSE. Settling it would overrule a lever
    the owner pulled - the opposite of the honesty this reconciler exists for."""
    assert STATE_PAUSED not in RECONCILABLE_RUN_STATES
    with factory() as db:
        run_id = _run(
            db,
            state=STATE_PAUSED,
            updated_at=LONG_AGO,
            step_states=[STEP_STATE_VERIFIED, STEP_STATE_READY],
        )

        assert reconcile_stalled_runs(db, now=NOW) == []
        assert db.get(ExecutiveRunRow, run_id).state == STATE_PAUSED


def test_a_run_with_no_steps_is_not_settled_as_failed(factory) -> None:
    """"No in-flight steps" is true of a run that has not started. Reading that as "it
    stopped" would fail every run the instant it was planned."""
    with factory() as db:
        run_id = _run(db, state=STATE_RUNNING, updated_at=LONG_AGO, step_states=[])

        assert reconcile_stalled_runs(db, now=NOW) == []
        assert db.get(ExecutiveRunRow, run_id).state == STATE_RUNNING


def test_reconciling_twice_changes_nothing_the_second_time(factory) -> None:
    """The tick runs every interval for ever; it must be idempotent or it is a log flood."""
    with factory() as db:
        _run(
            db,
            state=STATE_RUNNING,
            updated_at=LONG_AGO,
            step_states=[STEP_STATE_VERIFIED, STEP_STATE_FAILED],
        )

        assert len(reconcile_stalled_runs(db, now=NOW)) == 1
        assert reconcile_stalled_runs(db, now=NOW) == []


def test_the_reconciler_derives_nothing_of_its_own(factory) -> None:
    """It must call the released settlement logic, not a second opinion.

    A structural check rather than a behavioural one: two functions that each decide what
    `partial` means are two truths that will drift. `app/executive/reconcile.py` may name
    the recompute and must not re-implement the outcome rule.
    """
    import pathlib

    here = pathlib.Path(__file__).resolve()
    root = next(p for p in here.parents if (p / "app" / "executive").is_dir())
    source = (root / "app" / "executive" / "reconcile.py").read_text(encoding="utf-8")

    assert "_recompute_run_progress" in source
    for forbidden in ("STATE_PARTIAL", "STATE_COMPLETED", "STATE_FAILED", "_derive_run_outcome"):
        assert forbidden not in source, (
            f"reconcile.py names {forbidden}: it is deciding an outcome for itself instead "
            "of asking the released logic"
        )


@pytest.mark.asyncio
async def test_the_clock_actually_runs_the_tick_and_runs_it_last(factory) -> None:
    """The wiring, driven rather than read. A reconciler nothing calls is not a fix.

    The first version of this test asserted the call site was PRESENT in the source, and a
    mutation that wrapped it in `if False:` sailed straight through - the string was still
    there. Vacuous checks are a recurring failure in this repository, so this one drives a
    real `RoutineClock` and counts, and asserts the ORDER from what actually ran: a
    reconcile must never delay the alarm the owner is waiting for.
    """
    from app.routines.clock import RoutineClock

    order: list[str] = []
    clock = RoutineClock(
        session_factory=factory,
        evaluate_due=lambda session, now: order.append("routines"),
        alarm_tick=lambda session, now: order.append("alarm"),
        ambient_tick=lambda session, now: order.append("ambient"),
        evolution_tick=lambda session, now: order.append("evolution"),
        executive_tick=lambda session, now: order.append("executive"),
    )

    assert await clock.tick_once(now=NOW) is True

    assert "executive" in order, "the clock did not run the executive tick at all"
    assert order.index("alarm") < order.index("executive")
    assert order.index("ambient") < order.index("executive")
    assert clock.health_check()["last_error"] is None


@pytest.mark.asyncio
async def test_the_tick_settles_a_stalled_run_through_the_real_clock(factory) -> None:
    """End to end at the seam: the production shape, settled by a clock pass alone."""
    from app.executive.reconcile import executive_tick as reconcile_tick
    from app.routines.clock import RoutineClock

    with factory() as db:
        run_id = _run(
            db,
            state=STATE_RUNNING,
            updated_at=LONG_AGO,
            step_states=[STEP_STATE_VERIFIED, STEP_STATE_FAILED],
        )

    clock = RoutineClock(
        session_factory=factory,
        evaluate_due=lambda session, now: None,
        executive_tick=reconcile_tick,
    )
    assert await clock.tick_once(now=NOW) is True
    assert clock.health_check()["last_error"] is None

    with factory() as db:
        assert db.get(ExecutiveRunRow, run_id).state == STATE_PARTIAL
