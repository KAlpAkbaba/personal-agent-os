"""Settle a run whose steps have all stopped but whose row still says ``running``.

`_recompute_run_progress` is the whole of the truth: a run's state is a function of its
steps, and it is recomputed every time one settles. That is enough for every run whose
steps settle while the settlement logic is deployed — and it is exactly nothing for a run
whose steps settled BEFORE it was.

Production run ``3ef7c639`` is that case. Its four steps reached ``verified``, ``skipped``,
``failed`` and ``failed_recoverable`` at 2026-09-09T01:18Z, under a build that read "not
terminal" as "in flight" and therefore called the run ``running``. The fix (M26 review
finding, ``STEP_IN_FLIGHT_STATES``) shipped afterwards and is correct — but nothing will
ever call the recompute for that run again, because no step of it will ever settle again.
The Cloud Core has restarted since without settling it. The row is stuck for good, and it
tells the owner "Çalışıyorum efendim" about work that stopped hours ago.

So the recompute needs a second caller: a cadence, not an event. This module is that
caller and nothing else — it re-derives NOTHING. It finds the runs that cannot settle
themselves and asks the released logic to settle them.

Two guards keep it from doing harm:

* **Idleness.** A run is only reconciled once it has been untouched for
  ``RECONCILE_IDLE_AFTER_S``. Between a step's ``db.commit()`` and its recompute there is
  a real instant where a live run has no in-flight step, and a reconciler that pounced on
  it would settle a run that was about to carry on. Waiting removes the race rather than
  narrowing it.
* **In-flight steps.** A run holding any step in ``STEP_IN_FLIGHT_STATES`` is left alone,
  however old. Idleness alone is not evidence of stopping - a long ``research.run`` is
  idle by design, which is why it heartbeats.

A run with no steps at all is never touched: it has not started, and "no in-flight steps"
would otherwise settle it as ``failed`` the instant it was planned.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.executive.activities import _recompute_run_progress
from app.executive.models import (
    STATE_PAUSED,
    STATE_PLANNED,
    STATE_RUNNING,
    STEP_IN_FLIGHT_STATES,
    ExecutiveRunRow,
    ExecutiveStepRow,
)
from app.logging import get_logger

logger = get_logger("app.executive.reconcile")

#: How long a run must have been untouched before a reconcile will settle it.
#:
#: Five minutes, and the number is a race margin rather than a policy: the window between
#: a step's own commit and its recompute is milliseconds, so anything above a second would
#: do, and five minutes means a reconcile can never be the thing that decided a live run's
#: fate. A genuinely stuck run waits five minutes longer to be told the truth; a live one
#: is never lied about.
RECONCILE_IDLE_AFTER_S: Final = 300.0

#: The states a reconcile may act on. ``planned`` is included because a run whose steps all
#: failed before any of them ran leaves the row exactly there. ``paused`` is NOT: a paused
#: run has no in-flight steps ON PURPOSE, and settling it would overrule the owner.
RECONCILABLE_RUN_STATES: Final = frozenset({STATE_RUNNING, STATE_PLANNED})

assert STATE_PAUSED not in RECONCILABLE_RUN_STATES, "a paused run is the owner's, not ours"


def stalled_run_ids(
    db: Session, *, now: datetime | None = None, idle_after_s: float = RECONCILE_IDLE_AFTER_S
) -> list[uuid.UUID]:
    """The runs that say they are working and have nothing that could be working.

    Read-only, and separate from the settling so a caller (a test, a diagnostic, an owner
    question) can ask "what is stuck?" without changing anything.
    """
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(seconds=idle_after_s)
    candidates = (
        db.execute(
            select(ExecutiveRunRow).where(
                ExecutiveRunRow.state.in_(tuple(RECONCILABLE_RUN_STATES)),
                ExecutiveRunRow.updated_at < cutoff,
            )
        )
        .scalars()
        .all()
    )
    stalled: list[uuid.UUID] = []
    for run in candidates:
        steps = (
            db.execute(select(ExecutiveStepRow).where(ExecutiveStepRow.run_id == run.id))
            .scalars()
            .all()
        )
        if not steps:
            continue  # planned and never started: not stalled, just not begun
        if any(step.state in STEP_IN_FLIGHT_STATES for step in steps):
            continue
        stalled.append(run.id)
    return stalled


def reconcile_stalled_runs(
    db: Session, *, now: datetime | None = None, idle_after_s: float = RECONCILE_IDLE_AFTER_S
) -> list[uuid.UUID]:
    """Settle every stalled run through the released logic. Returns what it settled.

    The settling is `_recompute_run_progress` and only that: the same function the workflow
    calls, deriving the same outcome from the same rows. Nothing here decides whether a run
    is `partial`, `failed` or `completed` — that would be a second opinion, and a second
    opinion is how the two halves of a truth drift apart.
    """
    settled: list[uuid.UUID] = []
    for run_id in stalled_run_ids(db, now=now, idle_after_s=idle_after_s):
        before = db.get(ExecutiveRunRow, run_id)
        before_state = before.state if before else None
        _recompute_run_progress(db, run_id)
        after = db.get(ExecutiveRunRow, run_id)
        if after is not None and after.state != before_state:
            settled.append(run_id)
            logger.info(
                "executive_run_reconciled",
                run_id=str(run_id),
                was=before_state,
                now=after.state,
            )
    return settled


def executive_tick(db: Session, now: datetime) -> dict[str, int]:
    """The `RoutineClock`'s shape: one tick, one small query, a count for the health line.

    Injected into the clock the way the alarm, ambient and evolution ticks are, so this
    module imports no clock and the clock imports no executive — a cadence, not a policy.
    """
    settled = reconcile_stalled_runs(db, now=now)
    return {"reconciled": len(settled)}


__all__ = [
    "RECONCILABLE_RUN_STATES",
    "RECONCILE_IDLE_AFTER_S",
    "executive_tick",
    "reconcile_stalled_runs",
    "stalled_run_ids",
]
