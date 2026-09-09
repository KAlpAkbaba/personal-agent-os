"""The thing that asks (M18.3 spec §3.3).

M18 row 12.15 said "no routine fires without something asking: there is no background
timer". That property was never about avoiding a loop — it was about avoiding a HIDDEN one:
a routine engine that woke itself up on a schedule nobody could see, name or turn off. The
rest of the routines package still has no timer, ``evaluate_due`` is still the one explicit
entry point, and this module is the one component that asks — named, owner-visible on the
health manifest (``checks.routine_clock``), configurable
(``ROUTINE_CLOCK_INTERVAL_S``/``ROUTINE_CLOCK_ENABLED``), and started only by the API's
lifespan. Unit tests never start it, and ``tests/unit/test_routines_clock.py`` asserts
structurally that this file is still the only one in the package that owns a loop.

An alarm made the difference. A wake alarm must fire while the owner is asleep, with no
browser tab open and no voice session live: "someone asks when they think of it" is not a
scheduler a person can rely on to wake them, and a person relying on it is the whole point
of M18.3.

Three properties, each with a reason:

* **Single-flight.** A tick that overruns its interval must not overlap the next one. Two
  concurrent ``evaluate_due`` passes would be caught by ``RoutineFiring``'s uniqueness, but
  they would also both try to start the same alarm's device sequence, and the second one's
  failures would be recorded as real.
* **A worker thread with its own session.** Everything below is synchronous SQLAlchemy and
  blocking device I/O; running it on the event loop would stall every HTTP request for the
  length of a wake sequence. Each tick gets a fresh session, so a poisoned transaction dies
  with its tick rather than with the process.
* **A failing tick is recorded, never fatal.** ``last_error`` on the health check is how an
  operator finds out; the loop keeps its cadence, because the next alarm is more important
  than this tick's exception.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.logging import get_logger

logger = get_logger("app.routines.clock")

DEFAULT_INTERVAL_S = 10.0
#: A tick may not be scheduled more often than this: the clock drives real device commands.
MIN_INTERVAL_S = 1.0


@dataclass
class ClockHealth:
    running: bool = False
    last_tick_at: datetime | None = None
    interval_s: float = DEFAULT_INTERVAL_S
    ticks: int = 0
    last_error: str | None = None
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if (self.running or not self.enabled) else "fail",
            "running": self.running,
            "enabled": self.enabled,
            "interval_s": self.interval_s,
            "ticks": self.ticks,
            "last_tick_at": (
                self.last_tick_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
                if self.last_tick_at
                else None
            ),
            "last_error": self.last_error,
        }


class RoutineClock:
    """The asyncio loop that asks ``evaluate_due`` -> alarm tick -> ambient tick.

    ``session_factory`` is a plain callable returning a ``Session``; the three tick
    functions are injected so this module imports neither ``app.alarms`` nor ``app.ambient``
    — the clock is a cadence, not a policy, and keeping it ignorant of what it drives is
    what lets a test run it with three counters and no database at all.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        evaluate_due: Callable[[Session, datetime], Any],
        alarm_tick: Callable[[Session, datetime], Any] | None = None,
        ambient_tick: Callable[[Session, datetime], Any] | None = None,
        evolution_tick: Callable[[Session, datetime], Any] | None = None,
        executive_tick: Callable[[Session, datetime], Any] | None = None,
        interval_s: float = DEFAULT_INTERVAL_S,
        enabled: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._evaluate_due = evaluate_due
        self._alarm_tick = alarm_tick
        self._ambient_tick = ambient_tick
        #: M18.4 (spec §3.4): the Evolution Supervisor's scan. Last, and only after the
        #: owner-facing ticks ran, so a slow scan can never delay an alarm.
        self._evolution_tick = evolution_tick
        #: M26 review finding, second caller: a run whose steps all settled under a build
        #: that could not settle the run will never settle itself, because no step of it
        #: will ever settle again. The recompute needs a cadence as well as an event.
        #: Last, with the evolution scan, for the same reason - never ahead of an alarm.
        self._executive_tick = executive_tick
        self._interval_s = max(MIN_INTERVAL_S, float(interval_s))
        self._enabled = enabled
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._in_flight = False
        self._health = ClockHealth(interval_s=self._interval_s, enabled=enabled)

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if not self._enabled:
            logger.info("routine_clock_disabled")
            return
        if self._task is not None:
            return
        self._running = True
        self._health.running = True
        self._task = asyncio.create_task(self._loop(), name="routine-clock")
        logger.info("routine_clock_started", interval_s=self._interval_s)

    async def stop(self) -> None:
        self._running = False
        self._health.running = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            logger.info("routine_clock_stopped", ticks=self._health.ticks)

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval_s)
            except asyncio.CancelledError:
                raise
            if not self._running:
                break
            await self.tick_once()

    # ------------------------------------------------------------------ one tick

    async def tick_once(self, *, now: datetime | None = None) -> bool:
        """One pass, in a worker thread. Returns False when a pass was already in flight.

        Public because the test suite drives the clock by calling this directly — the loop
        is then only the cadence, and every behaviour that matters is testable without one.
        """
        if self._in_flight:
            logger.warning("routine_clock_tick_skipped_in_flight")
            return False
        self._in_flight = True
        try:
            await asyncio.to_thread(self._run_tick, now or datetime.now(UTC))
        finally:
            self._in_flight = False
        return True

    def _run_tick(self, moment: datetime) -> None:
        session = self._session_factory()
        try:
            self._evaluate_due(session, moment)
            if self._alarm_tick is not None:
                self._alarm_tick(session, moment)
            if self._ambient_tick is not None:
                self._ambient_tick(session, moment)
            if self._evolution_tick is not None:
                self._evolution_tick(session, moment)
            if self._executive_tick is not None:
                self._executive_tick(session, moment)
            self._health.last_error = None
        except Exception as exc:  # noqa: BLE001 - a failing tick must not stop the clock
            self._health.last_error = f"{type(exc).__name__}: {exc}"[:200]
            logger.error("routine_clock_tick_failed", error=self._health.last_error)
        finally:
            self._health.ticks += 1
            self._health.last_tick_at = moment
            with contextlib.suppress(Exception):
                session.close()

    # ------------------------------------------------------------------ health

    def health_check(self) -> dict[str, Any]:
        return self._health.as_dict()


#: Process-wide registry, the same shape as ``app.routines.dispatch``'s dispatcher registry:
#: ``app.main``'s lifespan sets it once, ``/v1/system/health`` reads it, and a process that
#: never started one reports the truthful "not running" rather than inventing a clock.
_clock: RoutineClock | None = None


def register_routine_clock(clock: RoutineClock | None) -> None:
    global _clock
    _clock = clock


def get_routine_clock() -> RoutineClock | None:
    return _clock


def routine_clock_health() -> dict[str, Any]:
    clock = get_routine_clock()
    if clock is None:
        return {
            "status": "skipped",
            "running": False,
            "enabled": False,
            "interval_s": None,
            "ticks": 0,
            "last_tick_at": None,
            "last_error": None,
        }
    return clock.health_check()


__all__ = [
    "DEFAULT_INTERVAL_S",
    "MIN_INTERVAL_S",
    "ClockHealth",
    "RoutineClock",
    "get_routine_clock",
    "register_routine_clock",
    "routine_clock_health",
]
