"""B07 req 18: every background loop answers for itself.

Nine loops start with the application. Five of them could be seen on the health surface -
the broker sweeper, the artifact runtime, the embedded worker, the routine clock and the
retention sweeper - and four could not: the push announcer, the briefing announcer, the
research tool-call announcer and the self-model refresher. Those four are exactly the ones
that carry a notification to the owner, so the failure they can have is the one nobody would
notice: the loop dies, or wakes and does nothing, and the system looks healthy while going
quiet.

"The process is up" is not "the loops are running", and one aggregate answer for nine
independent pieces of work is the same mistake as one try/except around five sub-ticks.

Two things are recorded that a task object cannot tell you on its own:

* **passes** - a loop whose task is alive but which has not completed a pass since it
  started is not working, and ``task.done()`` says nothing about that;
* **last_error** - a loop that catches its own exceptions (all of these do, deliberately,
  because dying is worse) otherwise fails invisibly for ever.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime
from typing import Any

#: A loop that has not completed a pass in this many of its own intervals is reported as
#: stalled. Three rather than one: a single slow pass is normal, three missed in a row is
#: the loop not doing its job.
STALL_INTERVALS = 3


@dataclasses.dataclass
class LoopHeartbeat:
    """What one background loop knows about itself."""

    name: str
    interval_s: float
    passes: int = 0
    failures: int = 0
    last_pass_at: datetime | None = None
    last_error: str | None = None
    started_at: datetime | None = None
    _task: asyncio.Task[Any] | None = dataclasses.field(default=None, repr=False)

    def bind(self, task: asyncio.Task[Any] | None, *, now: datetime | None = None) -> None:
        self._task = task
        self.started_at = (now or datetime.now(UTC)) if task is not None else None

    def record_pass(self, *, now: datetime | None = None) -> None:
        self.passes += 1
        self.last_pass_at = now or datetime.now(UTC)
        self.last_error = None

    def record_failure(self, exc: BaseException, *, now: datetime | None = None) -> None:
        self.failures += 1
        self.last_error = f"{type(exc).__name__}: {exc}"[:200]
        self.last_pass_at = now or datetime.now(UTC)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def stalled(self, *, now: datetime | None = None) -> bool:
        """Alive but not working: no completed pass for several of its own intervals.

        A loop that has started and never completed a pass is stalled once that window has
        gone by - "it has not finished its first pass yet" stops being an explanation.
        """
        if not self.running or self.interval_s <= 0:
            return False
        reference = self.last_pass_at or self.started_at
        if reference is None:
            return False
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        moment = now or datetime.now(UTC)
        return (moment - reference).total_seconds() > self.interval_s * STALL_INTERVALS

    def health_check(self, *, now: datetime | None = None) -> dict[str, Any]:
        """``status`` is "fail" only for a loop that should be running and is not, or is
        running and stalled. A loop that was never started is "skipped": every test process
        is in that state and it is not a fault."""
        if self._task is None:
            status = "skipped"
        elif not self.running or self.stalled(now=now):
            status = "fail"
        else:
            status = "ok"
        return {
            "status": status,
            "running": self.running,
            "interval_s": self.interval_s,
            "passes": self.passes,
            "failures": self.failures,
            "last_pass_at": _iso(self.last_pass_at),
            "last_error": self.last_error,
            # Advisory: a background loop being behind is worth seeing, and is not a reason
            # to fail a release gate that asks whether the API is serving.
            "required": False,
        }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["STALL_INTERVALS", "LoopHeartbeat"]
