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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.logging import get_logger

logger = get_logger("app.routines.clock")

DEFAULT_INTERVAL_S = 10.0
#: A tick may not be scheduled more often than this: the clock drives real device commands.
MIN_INTERVAL_S = 1.0


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class SubTickHealth:
    """One sub-tick's own record (B07 req 18/19).

    Each sub-tick is a separate piece of work on a shared clock, and until 2026-09-13 they
    shared a single try/except and a single ``last_error`` too. So a failure in the routine
    evaluation meant the alarm tick did not run at all that pass - and the health surface
    reported one clock, ticking, with an error string that named only whichever sub-tick
    happened to raise first. An alarm could be silently dropped by something unrelated to
    alarms, and nothing said which part was broken.
    """

    name: str
    ticks: int = 0
    failures: int = 0
    #: The run of failures ending now. A sub-tick that failed once an hour ago and has
    #: worked since is not the same thing as one that has failed every pass for an hour.
    consecutive_failures: int = 0
    last_error: str | None = None
    last_ok_at: datetime | None = None

    @property
    def status(self) -> str:
        return "fail" if self.consecutive_failures else "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ticks": self.ticks,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_ok_at": _iso(self.last_ok_at),
        }


@dataclass
class ClockHealth:
    running: bool = False
    last_tick_at: datetime | None = None
    interval_s: float = DEFAULT_INTERVAL_S
    ticks: int = 0
    last_error: str | None = None
    enabled: bool = True
    sub_ticks: dict[str, SubTickHealth] = field(default_factory=dict)

    def sub(self, name: str) -> SubTickHealth:
        record = self.sub_ticks.get(name)
        if record is None:
            record = SubTickHealth(name=name)
            self.sub_ticks[name] = record
        return record

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if (self.running or not self.enabled) else "fail",
            "running": self.running,
            "enabled": self.enabled,
            "interval_s": self.interval_s,
            "ticks": self.ticks,
            "last_tick_at": _iso(self.last_tick_at),
            "last_error": self.last_error,
            # req 18: each loop answers for itself. One aggregated "the clock is ticking"
            # hid four sub-ticks that were not.
            "sub_ticks": {name: record.as_dict() for name, record in self.sub_ticks.items()},
            "failing_sub_ticks": sorted(
                name for name, record in self.sub_ticks.items() if record.status == "fail"
            ),
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
        experience_tick: Callable[[Session, datetime], Any] | None = None,
        mail_tick: Callable[[Session, datetime], Any] | None = None,
        calendar_tick: Callable[[Session, datetime], Any] | None = None,
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
        #: B18 req 71: the Experience Engine's pass. LAST, behind the evolution scan and for
        #: the same reason - it reads the whole ledger and writes memories, which is the
        #: slowest thing on this clock and the least urgent. It throttles itself to its own
        #: interval, so most ticks it does nothing at all.
        self._experience_tick = experience_tick
        #: B45 (req 360): the inbox poll. Behind the owner-facing ticks, ahead of the
        #: experience pass (which may want what it indexed); it throttles itself.
        self._mail_tick = mail_tick
        #: B46 (req 358, 361): the calendar mirror and its reminders; throttles itself.
        self._calendar_tick = calendar_tick
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

    def _sub_ticks(self) -> list[tuple[str, Callable[[Session, datetime], Any]]]:
        """The sub-ticks this clock drives, in order, named. Order is preserved from the
        original single block: routines are evaluated first because the alarm tick acts on
        what they produced."""
        candidates: list[tuple[str, Callable[[Session, datetime], Any] | None]] = [
            ("routines", self._evaluate_due),
            ("alarms", self._alarm_tick),
            ("ambient", self._ambient_tick),
            ("evolution", self._evolution_tick),
            ("executive", self._executive_tick),
            ("mail", self._mail_tick),
            ("calendar", self._calendar_tick),
            ("experience", self._experience_tick),
        ]
        return [(name, fn) for name, fn in candidates if fn is not None]

    def _run_tick(self, moment: datetime) -> None:
        """Every sub-tick runs, whatever the ones before it did (B07 req 19).

        One try/except used to wrap all five, so a failure in the first meant the other four
        did not run at all - an alarm silently dropped by something that had nothing to do
        with alarms. Each one is isolated now, and the session is rolled back after a failure
        so the next sub-tick does not inherit a broken transaction: isolation that leaves the
        next caller a poisoned session is not isolation.
        """
        session = self._session_factory()
        failed: list[str] = []
        try:
            for name, fn in self._sub_ticks():
                record = self._health.sub(name)
                record.ticks += 1
                try:
                    fn(session, moment)
                except Exception as exc:  # noqa: BLE001 - one sub-tick, not the clock
                    record.failures += 1
                    record.consecutive_failures += 1
                    record.last_error = f"{type(exc).__name__}: {exc}"[:200]
                    failed.append(name)
                    logger.error(
                        "routine_clock_sub_tick_failed",
                        sub_tick=name,
                        error=record.last_error,
                        consecutive=record.consecutive_failures,
                    )
                    with contextlib.suppress(Exception):
                        session.rollback()
                else:
                    record.consecutive_failures = 0
                    record.last_ok_at = moment
            self._health.last_error = (
                None if not failed else f"sub-ticks failed: {', '.join(failed)}"
            )
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
