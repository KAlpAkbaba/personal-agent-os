"""M18 Routine Engine: TRIGGER -> CONDITIONS -> ACTIONS, durable (M18_HOLOGRAPHIC_CORE spec
sections 3/4/7, as carried in the task brief — see docs/DECISIONS.md for the ADR that
records this package's design since no ``docs/M18_HOLOGRAPHIC_CORE_SPEC.md`` exists yet in
this checkout).

A routine is a durable row, not a running thing: it is armed once, and something later
calls the explicit "due now" evaluation entry point (``app.routines.service.evaluate_due``)
to decide whether it fires. Nothing in this package starts a background timer or wakes
itself up — a cognitive system that schedules its own wakeups is a separate decision with
its own safety questions (ADR-0053 decision 8), which this package deliberately defers.

This package only decides WHAT should happen and records it. It never plays audio, never
opens a browser tab, never touches a display: ``app.routines.actions.RoutineDispatcher`` is
the seam a real executor implements later; ``NoopDispatcher`` is the deterministic
do-nothing implementation used here and in tests.
"""

from __future__ import annotations

__all__: list[str] = []
