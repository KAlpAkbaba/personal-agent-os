"""B10 req 539/558/559: a run says what actually happened.

Two production runs reported **"4/4 adım tamam"** — four of four steps completed — while
three of those four steps had failed. Not a display bug: ``steps_done`` counted every step
that had reached a terminal state, and ``failed``, ``cancelled``, ``compensated`` and
``skipped`` are all terminal. Two different questions ("how many are settled?" and "how many
worked?") had one answer, and the sentence that answer appeared in was asking the second one.

The same shape appears in compensation. ``_run_compensation`` ended with
``state = COMPENSATED``, unconditionally — so a step whose compensation was ``none``, or
whose evidence carried no id to act on, or whose provider raised and was swallowed by the
best-effort catch, said exactly what a step that genuinely undid its work said. "Compensated"
has to mean something was undone, or the word is worth nothing on the one occasion the owner
reads it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.executive import service as executive_service
from app.executive.models import (
    STATE_PAUSED,
    STATE_RUNNING,
    STEP_STATE_CANCELLED,
    STEP_STATE_COMPENSATED,
    STEP_STATE_FAILED,
    STEP_STATE_VERIFIED,
    STEP_TERMINAL_STATES,
    STEP_UNSUCCESSFUL_STATES,
    ExecutiveRunRow,
    ExecutiveStepRow,
)
from app.executive.spec import (
    COMPENSATION_DISCARD_DRAFT,
    COMPENSATION_NONE,
    COMPENSATION_OUTCOME_ATTEMPTED_AND_FAILED,
    COMPENSATION_OUTCOME_NOTHING_TO_UNDO,
    COMPENSATION_OUTCOME_UNDONE,
)

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------ the counts mean things


def test_the_two_counts_partition_every_settled_step() -> None:
    """`STEP_TERMINAL_STATES` answers "will it move again?"; `STEP_UNSUCCESSFUL_STATES`
    answers "did it work?". Conflating them is the whole defect, so they are held apart
    here: every terminal state is either a success or an unsuccess, exactly once."""
    assert STEP_UNSUCCESSFUL_STATES | {STEP_STATE_VERIFIED} == STEP_TERMINAL_STATES
    assert STEP_STATE_VERIFIED not in STEP_UNSUCCESSFUL_STATES


def test_a_failed_step_is_not_a_done_step() -> None:
    """The count that produced "4/4". Named individually because each of these four was
    reported as an accomplishment."""
    for state in (
        STEP_STATE_FAILED,
        STEP_STATE_CANCELLED,
        STEP_STATE_COMPENSATED,
        "skipped",
    ):
        assert state in STEP_UNSUCCESSFUL_STATES, state


def _run(**overrides) -> ExecutiveRunRow:
    base = {
        "id": uuid.uuid4(),
        "goal": "üç klasörü karşılaştır",
        "graph_json": {"steps": []},
        "source": "voice",
        "state": STATE_RUNNING,
        "steps_total": 4,
        "steps_done": 1,
        "steps_failed": 3,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return ExecutiveRunRow(**base)


def test_a_running_run_with_three_failures_cannot_say_four_of_four() -> None:
    """The exact sentence two production runs said."""
    speech = executive_service._status_speech(_run())

    assert "4/4" not in speech
    assert "1/4" in speech
    assert "3 başarısız" in speech


def test_a_run_with_nothing_failed_says_it_plainly() -> None:
    """The failure clause appears only when there is one - a status line that always
    mentions failures teaches the owner to stop reading it."""
    speech = executive_service._status_speech(_run(steps_done=2, steps_failed=0))

    assert "başarısız" not in speech
    assert "2/4" in speech


def test_a_paused_run_does_not_count_failures_as_still_to_come() -> None:
    """"Waiting" is what is neither done nor failed. Subtracting only the successes counted
    a failed step as work still ahead, which is a different lie in the same sentence."""
    paused = _run(state=STATE_PAUSED, steps_done=1, steps_failed=2)
    speech = executive_service._status_speech(paused)

    assert "1 adım tamam" in speech
    assert "2 başarısız" in speech
    assert "1 bekliyor" in speech


def test_the_panel_gets_both_halves() -> None:
    """"Not done" used to mean either "still going" or "it failed", and the caller had no
    way to tell which."""
    payload = executive_service.run_dict(_run())

    assert payload["done"] == 1
    assert payload["failed"] == 3
    assert payload["total"] == 4


# --------------------------------------------------- compensation means something


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (ExecutiveRunRow.__table__, ExecutiveStepRow.__table__):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _step(db, *, compensation: str, evidence: dict | None = None) -> ExecutiveStepRow:
    run = _run(steps_done=0, steps_failed=0)
    db.add(run)
    db.flush()
    step = ExecutiveStepRow(
        id=uuid.uuid4(),
        run_id=run.id,
        step_id="s1",
        kind="mail.draft",
        postcondition_json={},
        risk_class="low",
        timeout_s=60,
        state=STEP_STATE_VERIFIED,
        compensation=compensation,
        evidence_json=evidence or {},
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(step)
    db.commit()
    return step


def test_a_branch_that_did_nothing_does_not_claim_it_undid_anything(db) -> None:
    """req 539, the defect exactly. `compensation = none` matched no branch and the function
    still wrote COMPENSATED."""
    from app.executive.activities import _run_compensation

    step = _step(db, compensation=COMPENSATION_NONE)

    _run_compensation(db, step)

    assert step.state == STEP_STATE_CANCELLED
    assert step.evidence_json["compensation_outcome"] == COMPENSATION_OUTCOME_NOTHING_TO_UNDO


def test_a_compensation_with_no_id_to_act_on_says_so(db) -> None:
    """The branch matches the compensation kind but the evidence carries no draft to
    discard: nothing happened, and nothing happening is not an undo."""
    from app.executive.activities import _run_compensation

    step = _step(db, compensation=COMPENSATION_DISCARD_DRAFT, evidence={})

    _run_compensation(db, step)

    assert step.state == STEP_STATE_CANCELLED
    assert step.evidence_json["compensation_outcome"] == COMPENSATION_OUTCOME_NOTHING_TO_UNDO


def test_a_compensation_that_raised_is_not_reported_as_undone(db, monkeypatch) -> None:
    """The best-effort catch is right - a cancel must complete even if a provider is
    briefly unreachable - but swallowing the error and then claiming the work was undone is
    the part that was wrong."""
    from app.executive import activities

    class _Exploding:
        def discard(self, *_args, **_kwargs):
            raise RuntimeError("mail provider unreachable")

    monkeypatch.setattr(activities, "get_mail_service", lambda: _Exploding())
    step = _step(db, compensation=COMPENSATION_DISCARD_DRAFT, evidence={"draft_id": "d1"})

    activities._run_compensation(db, step)

    assert step.state == STEP_STATE_CANCELLED
    assert step.evidence_json["compensation_outcome"] == COMPENSATION_OUTCOME_ATTEMPTED_AND_FAILED
    assert step.evidence_json["compensation_error"] == "RuntimeError"


def test_a_compensation_that_worked_is_the_only_one_called_compensated(db, monkeypatch) -> None:
    from app.executive import activities

    discarded: list[str] = []

    class _Working:
        def discard(self, _db, *, draft_id):
            discarded.append(draft_id)

    monkeypatch.setattr(activities, "get_mail_service", lambda: _Working())
    step = _step(db, compensation=COMPENSATION_DISCARD_DRAFT, evidence={"draft_id": "d1"})

    activities._run_compensation(db, step)

    assert discarded == ["d1"]
    assert step.state == STEP_STATE_COMPENSATED
    assert step.evidence_json["compensation_outcome"] == COMPENSATION_OUTCOME_UNDONE


def test_a_cancelled_run_still_settles_when_a_compensation_fails(db, monkeypatch) -> None:
    """The reason the catch exists: the owner asked to stop, and that has to work. What
    changed is what the step SAYS afterwards, not whether cancelling completes."""
    from app.executive import activities

    class _Exploding:
        def discard(self, *_args, **_kwargs):
            raise RuntimeError("down")

    monkeypatch.setattr(activities, "get_mail_service", lambda: _Exploding())
    step = _step(db, compensation=COMPENSATION_DISCARD_DRAFT, evidence={"draft_id": "d1"})

    activities._run_compensation(db, step)

    assert step.state in STEP_TERMINAL_STATES, "cancellation must still complete"


# ------------------------------------------------------------- reconciliation (560)


def test_the_reconciliation_is_wired_into_the_clock() -> None:
    """req 560. The matrix recorded this as MISSING; it is not - `executive_tick` exists and
    `create_app` injects it into the routine clock. Measured rather than assumed, and the
    matrix corrected: existing measured reality wins over old documentation.

    B07 made this materially more true: the clock's five sub-ticks used to share one
    try/except, so a failure in the routine evaluation stopped this one from running at all.
    """
    import inspect

    from app.main import create_app

    source = inspect.getsource(create_app)
    assert "executive_tick=executive_reconcile.executive_tick" in source


def test_the_reconciliation_settles_through_the_released_logic() -> None:
    """It must not form a second opinion about whether a run is partial or failed - a second
    opinion is how two halves of one truth drift apart."""
    import inspect

    from app.executive import reconcile

    source = inspect.getsource(reconcile.reconcile_stalled_runs)
    assert "_recompute_run_progress" in source


def test_the_count_itself_is_computed_from_what_worked(db, monkeypatch) -> None:
    """The test that would actually have caught this in production. The sentence tests above
    assert what is SAID given the counts; this one drives `_recompute_run_progress` over
    three failures and one success and asserts the count it derives - which is where the
    "4/4" came from."""
    from app.executive import activities

    run = _run(steps_total=4, steps_done=0, steps_failed=0)
    db.add(run)
    db.flush()
    for step_id, state in (
        ("s1", STEP_STATE_VERIFIED),
        ("s2", STEP_STATE_FAILED),
        ("s3", STEP_STATE_FAILED),
        ("s4", STEP_STATE_FAILED),
    ):
        db.add(
            ExecutiveStepRow(
                id=uuid.uuid4(),
                run_id=run.id,
                step_id=step_id,
                kind="mail.draft",
                postcondition_json={},
                risk_class="low",
                timeout_s=60,
                compensation=COMPENSATION_NONE,
                state=state,
                evidence_json={},
                created_at=NOW,
                updated_at=NOW,
            )
        )
    db.commit()

    activities._recompute_run_progress(db, run.id)

    db.refresh(run)
    assert run.steps_done == 1, "three failures are not three accomplishments"
    assert run.steps_failed == 3
    assert run.state != "completed", "a run with three failed steps did not complete"
