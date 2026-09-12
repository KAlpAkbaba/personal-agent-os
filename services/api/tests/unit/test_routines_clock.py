"""Unit tests: app.routines.clock (M18.3 spec §3.3).

This file also carries the AMENDED form of the M18 "no background timer" guard
(QUALIFICATION.md row 12.15). That row's property was never "no loop exists" — it was "no
routine fires without something asking", i.e. no HIDDEN loop. M18.3 needs a wake alarm to
fire while the owner is asleep with no browser tab open and no voice session live, and
"someone asks when they think of it" is not a scheduler a person can rely on to wake them.

So the guard is restated, structurally, in two halves:

* the routines PACKAGE still owns no timer — ``evaluate_due`` remains the one explicit
  entry point, and no module in ``app/routines`` except ``clock.py`` starts a loop, a
  thread or a sleep;
* ``clock.py`` is the one component that asks, and it is NAMED, owner-visible on the health
  manifest, and configurable.

The suite never starts the loop: every behaviour is driven through ``tick_once``.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.routines.clock import (
    DEFAULT_INTERVAL_S,
    MIN_INTERVAL_S,
    RoutineClock,
    get_routine_clock,
    register_routine_clock,
    routine_clock_health,
)

ROUTINES = Path(__file__).resolve().parents[2] / "app" / "routines"
NOW = datetime(2026, 9, 9, 2, 0, tzinfo=UTC)


class _Session:
    """A session stand-in: the clock only ever creates one, hands it to the ticks and
    closes it, so nothing more is needed to test the cadence."""

    def __init__(self) -> None:
        self.closed = False
        self.rollbacks = 0

    def rollback(self) -> None:
        # B07 req 19: the clock rolls back after a failed sub-tick so the next one does not
        # inherit a broken transaction. Isolation that hands the next caller a poisoned
        # session is not isolation.
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _clock(**kwargs) -> tuple[RoutineClock, dict[str, list]]:
    calls: dict[str, list] = {"evaluate": [], "alarm": [], "ambient": [], "sessions": []}

    def _factory() -> _Session:
        session = _Session()
        calls["sessions"].append(session)
        return session

    defaults = {
        "session_factory": _factory,
        "evaluate_due": lambda s, now: calls["evaluate"].append(now),
        "alarm_tick": lambda s, now: calls["alarm"].append(now),
        "ambient_tick": lambda s, now: calls["ambient"].append(now),
    }
    defaults.update(kwargs)
    return RoutineClock(**defaults), calls


@pytest.fixture(autouse=True)
def _no_registered_clock():
    register_routine_clock(None)
    yield
    register_routine_clock(None)


# ------------------------------------------------------------------------- the tick


def test_one_tick_runs_the_three_ticks_in_order_in_one_session() -> None:
    clock, calls = _clock()
    assert asyncio.run(clock.tick_once(now=NOW)) is True
    assert calls["evaluate"] == [NOW]
    assert calls["alarm"] == [NOW]
    assert calls["ambient"] == [NOW]
    assert len(calls["sessions"]) == 1
    assert calls["sessions"][0].closed is True


def test_each_tick_gets_a_fresh_session() -> None:
    """A poisoned transaction dies with its tick rather than with the process."""
    clock, calls = _clock()
    asyncio.run(clock.tick_once(now=NOW))
    asyncio.run(clock.tick_once(now=NOW))
    assert len(calls["sessions"]) == 2
    assert all(s.closed for s in calls["sessions"])


def test_a_failing_tick_is_recorded_and_never_fatal() -> None:
    """``last_error`` is how an operator finds out; the loop keeps its cadence, because the
    next alarm matters more than this tick's exception.

    B07 req 19 (2026-09-13): the second half of this test used to read "the later ticks were
    skipped by the exception", which was the defect written down as an expectation. One
    try/except wrapped all five sub-ticks, so a failure in the routine evaluation stopped the
    ALARM tick from running that pass - an alarm silently dropped by something that had
    nothing to do with alarms. They are isolated now.
    """

    def _boom(session, now):
        raise RuntimeError("the database went away")

    clock, calls = _clock(evaluate_due=_boom)
    assert asyncio.run(clock.tick_once(now=NOW)) is True
    health = clock.health_check()
    assert health["ticks"] == 1
    assert health["failing_sub_ticks"] == ["routines"]
    assert "RuntimeError" in health["sub_ticks"]["routines"]["last_error"]
    assert health["last_error"] == "sub-ticks failed: routines"
    # The alarm tick ran anyway - that is the whole point.
    assert len(calls["alarm"]) == 1
    assert calls["sessions"][0].rollbacks == 1, "the next sub-tick got a clean session"
    assert calls["sessions"][0].closed is True

    # ...and a subsequent good tick clears the error rather than leaving it standing.
    clock._evaluate_due = lambda s, now: None  # noqa: SLF001 - simulating a recovered DB
    asyncio.run(clock.tick_once(now=NOW))
    recovered = clock.health_check()
    assert recovered["last_error"] is None
    assert recovered["failing_sub_ticks"] == []
    assert recovered["sub_ticks"]["routines"]["consecutive_failures"] == 0
    assert recovered["sub_ticks"]["routines"]["failures"] == 1, "the history is kept"


def test_a_tick_that_is_already_in_flight_is_skipped_not_queued() -> None:
    """Single-flight (module docstring): two concurrent passes would both try to start the
    same alarm's device sequence, and the second one's failures would be recorded as real."""
    clock, calls = _clock()
    started = asyncio.Event()
    release = asyncio.Event()

    def _slow(session, now):
        calls["evaluate"].append(now)
        loop.call_soon_threadsafe(started.set)
        asyncio.run_coroutine_threadsafe(_wait(), loop).result()

    async def _wait() -> None:
        await release.wait()

    async def _run() -> tuple[bool, bool]:
        first = asyncio.create_task(clock.tick_once(now=NOW))
        await started.wait()
        second = await clock.tick_once(now=NOW)
        release.set()
        return await first, second

    clock._evaluate_due = _slow  # noqa: SLF001 - the seam this test needs
    loop = asyncio.new_event_loop()
    try:
        first_ok, second_ok = loop.run_until_complete(_run())
    finally:
        loop.close()
    assert first_ok is True
    assert second_ok is False  # skipped, not queued
    assert len(calls["evaluate"]) == 1


def test_optional_ticks_may_be_absent() -> None:
    clock, calls = _clock(alarm_tick=None, ambient_tick=None)
    asyncio.run(clock.tick_once(now=NOW))
    assert calls["evaluate"] == [NOW]
    assert calls["alarm"] == []


# ----------------------------------------------------------------------- lifecycle


def test_a_disabled_clock_never_starts() -> None:
    """``ROUTINE_CLOCK_ENABLED=false`` means no alarm fires on its own — which is exactly
    why it is a visible setting and not a hidden constant."""
    clock, calls = _clock(enabled=False)
    asyncio.run(clock.start())
    assert clock.health_check()["running"] is False
    assert clock.health_check()["enabled"] is False
    # ...and it still reports "ok", because not running is what the owner asked for.
    assert clock.health_check()["status"] == "ok"
    assert calls["evaluate"] == []


def test_start_and_stop_are_idempotent() -> None:
    clock, _ = _clock(interval_s=3600)

    async def _run() -> None:
        await clock.start()
        await clock.start()
        assert clock.health_check()["running"] is True
        await clock.stop()
        await clock.stop()

    asyncio.run(_run())
    assert clock.health_check()["running"] is False


def test_the_interval_has_a_floor() -> None:
    """The clock drives real device commands; a caller cannot configure it to hammer one."""
    clock, _ = _clock(interval_s=0.01)
    assert clock.health_check()["interval_s"] == MIN_INTERVAL_S


# -------------------------------------------------------------------------- health


def test_health_is_skipped_when_no_clock_is_registered() -> None:
    """Every unit-test process. "skipped" is truthful — there is no clock, rather than a
    broken one."""
    assert get_routine_clock() is None
    health = routine_clock_health()
    assert health["status"] == "skipped"
    assert health["running"] is False
    assert health["ticks"] == 0


def test_health_reports_the_registered_clocks_real_state() -> None:
    clock, _ = _clock()
    register_routine_clock(clock)
    asyncio.run(clock.tick_once(now=NOW))
    health = routine_clock_health()
    assert health["ticks"] == 1
    assert health["last_tick_at"] == "2026-09-09T02:00:00Z"
    assert health["interval_s"] == DEFAULT_INTERVAL_S
    assert health["last_error"] is None


def test_a_configured_clock_that_is_not_running_reports_fail() -> None:
    """The state in which no alarm would ever fire must not read as healthy."""
    clock, _ = _clock()
    register_routine_clock(clock)
    assert routine_clock_health()["status"] == "fail"


# ------------------------------------------- the amended "no background timer" guard

#: Names that would mean a module schedules its own work.
_TIMER_NAMES = (
    "create_task",
    "call_later",
    "call_at",
    "ensure_future",
    "Timer",
    "Thread",
    "sleep",
)


def _routine_modules() -> list[Path]:
    return sorted(p for p in ROUTINES.glob("*.py") if p.name != "clock.py")


def test_there_are_routine_modules_to_check() -> None:
    """A guard on the guard: a rename that emptied this glob would make the next test pass
    vacuously."""
    names = {p.name for p in _routine_modules()}
    assert {"service.py", "triggers.py", "dispatch.py", "actions.py"} <= names


@pytest.mark.parametrize("path", _routine_modules(), ids=lambda p: p.name)
def test_no_routine_module_except_the_clock_owns_a_timer(path: Path) -> None:
    """QUALIFICATION row 12.15, amended (module docstring).

    The routines PACKAGE still has no timer: ``evaluate_due`` is the one explicit entry
    point and nothing in it decides when to run. ``clock.py`` is excluded because it IS the
    named, owner-visible component that asks — which is the whole point of the amendment.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                called.add(func.attr)
            elif isinstance(func, ast.Name):
                called.add(func.id)
    offenders = called & set(_TIMER_NAMES)
    assert not offenders, f"{path.name} schedules its own work: {sorted(offenders)}"


def test_evaluate_due_is_still_the_one_explicit_entry_point() -> None:
    """The clock CALLS it; it does not replace it, and nothing else in the package decides
    when a routine fires."""
    import inspect

    from app.routines import service as routines_service

    source = inspect.getsource(routines_service.evaluate_due)
    assert "now = now or utcnow()" in source
    assert "asyncio" not in source


def test_the_clock_is_the_named_component_that_asks() -> None:
    """The other half of the amendment: the thing that asks has a name, a health check and
    a switch."""
    from app.config import Settings

    settings = Settings()
    assert hasattr(settings, "routine_clock_enabled")
    assert hasattr(settings, "routine_clock_interval_s")
    assert settings.routine_clock_interval_s == DEFAULT_INTERVAL_S
    assert set(routine_clock_health()) >= {
        "status",
        "running",
        "interval_s",
        "ticks",
        "last_tick_at",
        "last_error",
    }
