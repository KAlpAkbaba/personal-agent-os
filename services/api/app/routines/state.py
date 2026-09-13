"""Routine status state machine (mirrors ``app.goals.state``).

    armed     -> paused, completed, cancelled
    paused    -> armed, cancelled
    completed -> (terminal)
    cancelled -> (terminal)

A one-shot ``at`` trigger's only legal forward move is to ``completed``, reached once its
single occurrence has been evaluated (triggered or skipped — either way the moment has
passed, task brief). A ``schedule``/``presence`` routine never reaches ``completed`` on its
own; it only ever leaves ``armed`` via an explicit cancellation. Re-asserting the current
status is always legal (idempotent), same rule as ``app.goals.state.can_transition_goal``.

**``paused`` is not terminal, and it is not ``cancelled`` (B14 req 290/291).** Cancelling is
a decision about the routine; pausing is a decision about this week. A paused routine keeps
its id, its firings and its ledger history, and ``resume_routine`` puts it back exactly
where it was. It cannot go straight to ``completed``: a one-shot that was paused through its
own moment has not "completed" - the owner turned it off, and resuming it is their call.
"""

from __future__ import annotations

from app.routines.models import (
    ROUTINE_STATUS_ARMED,
    ROUTINE_STATUS_CANCELLED,
    ROUTINE_STATUS_COMPLETED,
    ROUTINE_STATUS_PAUSED,
    ROUTINE_STATUSES,
)

_ROUTINE_EDGES: dict[str, frozenset[str]] = {
    ROUTINE_STATUS_ARMED: frozenset(
        {ROUTINE_STATUS_PAUSED, ROUTINE_STATUS_COMPLETED, ROUTINE_STATUS_CANCELLED}
    ),
    ROUTINE_STATUS_PAUSED: frozenset({ROUTINE_STATUS_ARMED, ROUTINE_STATUS_CANCELLED}),
    ROUTINE_STATUS_COMPLETED: frozenset(),
    ROUTINE_STATUS_CANCELLED: frozenset(),
}

assert set(_ROUTINE_EDGES) == set(ROUTINE_STATUSES)  # every status is a node in the table


class IllegalRoutineTransition(ValueError):
    """Raised when a routine status transition is not permitted by the machine."""


def can_transition_routine(current: str, new: str) -> bool:
    if current not in _ROUTINE_EDGES:
        raise ValueError(f"unknown routine status: {current!r}")
    if new not in _ROUTINE_EDGES:
        raise ValueError(f"unknown routine status: {new!r}")
    if new == current:
        return True  # idempotent re-assert
    return new in _ROUTINE_EDGES[current]


def assert_routine_transition(current: str, new: str) -> str:
    if not can_transition_routine(current, new):
        raise IllegalRoutineTransition(f"illegal routine transition {current} -> {new}")
    return new


__all__ = ["IllegalRoutineTransition", "assert_routine_transition", "can_transition_routine"]
