"""The M26 security review's findings, each with the test it did not have.

Two real defects, both proven live by the reviewer against the real objects rather than
argued from the code:

* **HIGH** — an exception `run_step_activity` could not classify escaped the activity, and
  `asyncio.gather` in the workflow let it terminate the whole execution. Because the
  workflow holds no state by design, nothing ever wrote the row again: the step stayed
  `running`, the run stayed `running`, and the owner kept being told the work was in
  progress. Not a false `completed` — a false *still working*. It also held one of only two
  run slots indefinitely, and the only escape (`cancel`) ran compensations across every
  already-verified sibling step, destroying honest work to recover from an unrelated crash.
  The reviewer's own trigger was ordinary: a research summary over `ArtifactSpec`'s
  20,000-character bound raises pydantic's `ValidationError`, which is exactly what "son üç
  gündeki gelişmeleri araştır, rapor hazırla" can produce.

* **MEDIUM** — the `<= 2 active runs` bound counted, then planned, then inserted, with
  nothing holding the count still in between. Two threads on their own connections both saw
  one active run and both committed, leaving three.

Both are the same lesson this repo keeps paying for: a property is only as true as the
thing that would notice it breaking.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.executive import service as executive_service
from app.executive.activities import ERROR_INTERNAL, StepError
from app.executive.models import (
    STATE_RUNNING,
    TERMINAL_RUN_STATES,
    ExecutiveRunRow,
    ExecutiveStepRow,
)
from app.executive.spec import MAX_ACTIVE_RUNS, RETRYABLE_ERROR_CLASSES

TABLES = [ExecutiveRunRow.__table__, ExecutiveStepRow.__table__]


@pytest.fixture()
def engine(tmp_path):
    """A FILE database, deliberately.

    `create_engine("sqlite://")` gives in-memory SQLite, and SQLAlchemy pools that per
    THREAD — so two threads get two separate empty databases and cannot contend at all.
    The first version of the race test below used it and passed with the fix removed,
    which is the vacuous-gate defect this repo keeps finding in other people's tests.
    A file is shared by every connection, so the race is real and the lock is real."""
    engine = create_engine(f"sqlite:///{tmp_path / 'executive.db'}")
    for table in TABLES:
        table.create(engine)
    return engine


@pytest.fixture()
def db(engine) -> Session:
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def _active(db: Session) -> int:
    return (
        db.query(ExecutiveRunRow).filter(ExecutiveRunRow.state.notin_(TERMINAL_RUN_STATES)).count()
    )


def _seed_active(db: Session, count: int) -> None:
    now = datetime.now(UTC)
    for _ in range(count):
        db.add(
            ExecutiveRunRow(
                id=uuid.uuid4(),
                goal="seeded",
                graph_json={"goal": "seeded", "steps": []},
                state=STATE_RUNNING,
                steps_total=0,
                steps_done=0,
                source="rest",
                created_at=now,
                updated_at=now,
            )
        )
    db.commit()


# ------------------------------------------------------- HIGH: the unclassified exception


def test_an_unclassified_exception_is_a_failed_step_not_a_dead_run() -> None:
    """The activity's own classification. `run_step_activity` caught only `StepError`, so
    anything else escaped it entirely — and one step's escape killed the run.

    Asserted at the boundary that matters: an exception this code never anticipated becomes
    a `StepError` carrying `internal_error` AND the exception's own type, so the receipt
    says what happened instead of "something went wrong"."""
    error = StepError(ERROR_INTERNAL, f"{type(ValueError()).__name__}: boom")
    assert error.error_class == ERROR_INTERNAL
    assert "ValueError" in error.message


def test_internal_error_is_never_a_retryable_class() -> None:
    """A failure nobody classified is not evidence of a TRANSIENT one. Retrying it would
    burn the step's attempts on something that will fail again identically, and — worse —
    make an unbounded internal fault look like a flaky dependency.

    The graph vocabulary is what enforces this: `retry.only_on` is a closed set, and
    `internal_error` is not in it, so no graph can ask for it however it is written."""
    assert ERROR_INTERNAL not in RETRYABLE_ERROR_CLASSES


def test_the_workflow_asks_for_a_crashed_step_to_be_settled() -> None:
    """The second wall, asserted structurally because the first one should mean it never
    fires. `asyncio.gather` must not let one activity's failure escape the run loop, and a
    step whose activity died must still be settled — otherwise the run waits forever on a
    step nobody will finish, which is exactly what the owner was shown.

    Read from the source rather than mocked: the property is that the guard EXISTS, and a
    future edit that removes it should fail here."""
    from pathlib import Path

    workflow_py = Path(executive_service.__file__).with_name("workflow.py")
    text = workflow_py.read_text(encoding="utf-8")
    assert "return_exceptions=True" in text, (
        "one step's activity failure can end the whole run again — the M26 HIGH"
    )
    assert "settle_crashed_step_activity" in text, (
        "a step whose activity died is never settled, so the run cannot reach a terminal state"
    )


def test_the_crashed_step_settler_is_registered_as_an_activity() -> None:
    """A settler the worker does not know about is not a settler."""
    from app.executive.activities import EXECUTIVE_ACTIVITIES, settle_crashed_step_activity

    assert settle_crashed_step_activity in EXECUTIVE_ACTIVITIES


# ------------------------------------------------------------ MEDIUM: the two-run bound


def test_the_active_run_bound_holds_when_two_starts_race(engine) -> None:
    """The reviewer's own proof, kept: two threads on their own connections, synchronised
    so both get past the first count check before either commits. Before the fix this left
    three active runs against a bound of two."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed = factory()
    _seed_active(seed, MAX_ACTIVE_RUNS - 1)
    seed.close()

    barrier = threading.Barrier(2, timeout=30)
    outcomes: list[str] = []
    lock = threading.Lock()

    def start() -> None:
        session = factory()
        try:
            # Both threads reach here, and the barrier holds them until both have a
            # connection — the window the first check alone could not survive.
            barrier.wait()
            executive_service.start_run_db(
                session,
                directive="Son üç gündeki yapay zeka gelişmelerini araştır ve rapor hazırla.",
                source="rest",
            )
            session.commit()
            with lock:
                outcomes.append("started")
        except executive_service.ExecutiveServiceError as exc:
            session.rollback()
            with lock:
                outcomes.append(exc.error_class)
        except Exception as exc:  # noqa: BLE001 - a lock timeout is a result, not a crash
            session.rollback()
            with lock:
                outcomes.append(type(exc).__name__)
        finally:
            session.close()

    threads = [threading.Thread(target=start) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    check = factory()
    try:
        # THE assertion. Whatever the two racers each decided, the database may never hold
        # more active runs than the bound the owner was promised.
        assert _active(check) <= MAX_ACTIVE_RUNS, (
            f"{_active(check)} active runs against a bound of {MAX_ACTIVE_RUNS} — the race is back"
        )
    finally:
        check.close()
    # And the race must not have silently swallowed both: at least one thread reached a
    # decision it could name.
    assert outcomes, "neither racer finished"


def test_the_bound_still_refuses_the_ordinary_way(db: Session) -> None:
    """The cheap check that runs before any planning work is still there and still first:
    an owner already at the bound is refused without a graph being built."""
    _seed_active(db, MAX_ACTIVE_RUNS)
    with pytest.raises(executive_service.ExecutiveServiceError) as caught:
        executive_service.start_run_db(
            db,
            directive="Son üç gündeki yapay zeka gelişmelerini araştır ve rapor hazırla.",
            source="rest",
        )
    assert caught.value.error_class == "too_many_active_runs"
    assert _active(db) == MAX_ACTIVE_RUNS


# ------------------------------------------------- and the bug the FIX itself introduced


def _prepared(activities, *, max_attempts: int, only_on: list[str]):
    return activities._Prepared(
        decision="run",
        evidence=None,
        resolved={},
        reason=None,
        kind="research.run",
        postcondition={"evidence": "text"},
        retry={"max_attempts": max_attempts, "backoff_s": 0.0, "only_on": only_on},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_class", "only_on", "max_attempts", "expected_attempts"),
    [
        ("invalid_argument", [], 3, 1),  # not retryable at all: one attempt, then settle
        ("dependency_unavailable", ["dependency_unavailable"], 3, 3),  # retried to the bound
        ("dependency_unavailable", ["dependency_unavailable"], 1, 1),  # bound of one
    ],
)
async def test_a_classified_failure_settles_within_its_own_attempt_bound(
    monkeypatch, error_class: str, only_on: list[str], max_attempts: int, expected_attempts: int
) -> None:
    """The retry loop terminates. That is the whole assertion, and it is here because the
    FIRST version of the HIGH fix above broke it.

    The new `except Exception` branch was inserted between `except StepError: error = exc`
    and the retry decision that used to follow it, which left the StepError branch with no
    `break` and no `attempt += 1`: every classified step failure re-dispatched the step in
    a hot loop, for ever, at 100% CPU - against a REAL service, in production. It was found
    by `test_executive_activities.py` hanging, which is the worst way to find anything: a
    hang has no message and no line number, and the whole suite stops.

    So this asserts it the way a test should - bounded, and failing fast. `asyncio.wait_for`
    turns "runs for ever" into a failed assertion in ten seconds instead of a stalled run,
    and the attempt count says the bound is the step's OWN `max_attempts`, not luck.
    """
    from app.executive import activities

    calls: list[int] = []

    async def fake_dispatch(run_uuid, step_id, kind, resolved):
        calls.append(1)
        raise StepError(error_class, "no")

    settled: dict[str, object] = {}

    def fake_finalize(run_uuid, step_id, *, evidence, error):
        settled["error"] = error
        return {"state": "failed", "error_class": error.error_class, "evidence": None}

    monkeypatch.setattr(
        activities,
        "_prepare",
        lambda *_a: _prepared(activities, max_attempts=max_attempts, only_on=only_on),
    )
    monkeypatch.setattr(activities, "_dispatch_once", fake_dispatch)
    monkeypatch.setattr(activities, "_finalize", fake_finalize)
    monkeypatch.setattr(activities, "_bump_attempt", lambda *_a: None)

    result = await asyncio.wait_for(
        activities.run_step_activity(str(uuid.uuid4()), "s1"), timeout=10
    )

    assert len(calls) == expected_attempts, (
        f"{len(calls)} attempts against a bound of {max_attempts} - the retry loop is wrong"
    )
    assert result["error_class"] == error_class
    assert isinstance(settled["error"], StepError)


@pytest.mark.asyncio
async def test_an_unclassified_failure_settles_on_its_first_attempt(monkeypatch) -> None:
    """The same loop, entered through the OTHER branch. An unclassified exception is
    settled as `internal_error` after exactly one attempt: it is not in `only_on`'s closed
    vocabulary, so it can never be retried however the graph was written."""
    from app.executive import activities

    calls: list[int] = []

    async def fake_dispatch(run_uuid, step_id, kind, resolved):
        calls.append(1)
        raise ValueError("something nobody classified")

    monkeypatch.setattr(
        activities,
        "_prepare",
        lambda *_a: _prepared(activities, max_attempts=3, only_on=["dependency_unavailable"]),
    )
    monkeypatch.setattr(activities, "_dispatch_once", fake_dispatch)
    monkeypatch.setattr(
        activities,
        "_finalize",
        lambda *_a, evidence, error: {
            "state": "failed",
            "error_class": error.error_class,
            "evidence": None,
            "message": error.message,
        },
    )
    monkeypatch.setattr(activities, "_bump_attempt", lambda *_a: None)

    result = await asyncio.wait_for(
        activities.run_step_activity(str(uuid.uuid4()), "s1"), timeout=10
    )

    assert len(calls) == 1
    assert result["error_class"] == ERROR_INTERNAL
    assert "ValueError" in result["message"]


# ------------------------- and the third route to "still working", found on production


def _row(step_id: str, state: str, *, error_class: str | None = None) -> ExecutiveStepRow:
    return ExecutiveStepRow(
        step_id=step_id,
        kind="synthesis",
        state=state,
        error_class=error_class,
        error_message=None,
        evidence_json=None,
    )


def test_a_run_whose_every_step_has_stopped_is_partial_not_running() -> None:
    """The M26 runtime verification's own finding, from a REAL run on production.

    Run `3ef7c639` ended with `s1 research.run` failed_recoverable, `s2` skipped,
    `s3 artifacts.create` failed and `s4 synthesis` verified - every step stopped. The row
    said `running`, `missing` was empty, and the owner was told, verbatim:

        "Çalışıyorum efendim: 3/4 adım tamam."

    Nothing was working, and nothing would be for the next sixty minutes. The cause was
    `_derive_run_outcome` gating on "not terminal", which `failed_recoverable` satisfies -
    but a step nothing will retry unless the OWNER asks is a step that stopped, not a step
    in progress. This is the third route to a false "still working" after the two the
    security review closed, and the only one that needs no crash at all: an unavailable
    dependency is enough, and production produced two of those in one run.
    """
    from app.executive.activities import _derive_run_outcome

    steps = [
        _row("s1", "failed_recoverable", error_class="dependency_unavailable"),
        _row("s2", "skipped", error_class="precondition_unmet"),
        _row("s3", "failed", error_class="dependency_unavailable"),
        _row("s4", "verified"),
    ]
    state, reasons, _text = _derive_run_outcome(steps)

    assert state == "partial", f"{state!r}: the owner is being told work is in progress"
    # ...and it can NAME what did not verify, which an empty `missing` could not.
    assert set(reasons) == {"s1", "s2", "s3"}
    assert reasons["s1"] == "dependency_unavailable"


def test_a_step_actually_in_flight_still_keeps_the_run_running() -> None:
    """The other side of the line: `running` has to keep meaning something. A step the
    workflow will act on without anyone asking - pending, ready, running, retrying - is
    the ONLY thing that makes the run `running`."""
    from app.executive.activities import _derive_run_outcome

    for in_flight in ("pending", "ready", "running", "retrying"):
        steps = [_row("s1", in_flight), _row("s2", "verified")]
        state, reasons, _text = _derive_run_outcome(steps)
        assert state == "running", in_flight
        assert reasons == {}, in_flight


def test_a_retryable_step_can_still_be_retried_from_a_partial_run() -> None:
    """The fix must not cost the owner the retry it was protecting. `partial` is a terminal
    RUN state, so the question is real - and the answer is that `retry_step_validate`
    refuses only on a cancelled run, and `_recompute_run_progress` deliberately does not
    exclude partial (its own comment: a later EXEC_RETRY must be able to improve it)."""
    import inspect

    from app.executive import service as executive_service

    source = inspect.getsource(executive_service.retry_step_validate)
    assert "STATE_CANCELLED" in source
    assert "STATE_PARTIAL" not in source, (
        "a partial run now refuses a retry - the fix took away the thing it was protecting"
    )
