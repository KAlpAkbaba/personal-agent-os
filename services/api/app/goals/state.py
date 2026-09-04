"""Goal status state machine (documented legal-transition table).

Same discipline as ``app.artifacts.state.assert_artifact_transition``: a
transition not present in the table is illegal and raises rather than being
silently accepted, so a bug in the Cognitive Core cannot walk a goal into a
status that has no sensible meaning.

    draft         -> active, abandoned
    active        -> blocked, waiting_owner, achieved, abandoned, superseded
    blocked       -> active, abandoned, superseded
    waiting_owner -> active, abandoned, superseded
    achieved      -> (terminal)
    abandoned     -> (terminal)
    superseded    -> (terminal)

``waiting_owner`` is not itself the owner-approval gate — leaving it is always
a *legal* transition per this table. The gate (a goal with
``requires_owner_approval`` cannot leave ``waiting_owner`` without an explicit
owner action) is enforced by ``app.goals.service.transition_status``, one
layer up, because it depends on the goal's ``requires_owner_approval`` flag
and on *who* is asking — not on notions this pure state machine has.
"""

from __future__ import annotations

from app.goals.models import (
    GOAL_STATUS_ABANDONED,
    GOAL_STATUS_ACHIEVED,
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_BLOCKED,
    GOAL_STATUS_DRAFT,
    GOAL_STATUS_SUPERSEDED,
    GOAL_STATUS_WAITING_OWNER,
    GOAL_STATUSES,
)

_GOAL_EDGES: dict[str, frozenset[str]] = {
    GOAL_STATUS_DRAFT: frozenset({GOAL_STATUS_ACTIVE, GOAL_STATUS_ABANDONED}),
    GOAL_STATUS_ACTIVE: frozenset(
        {
            GOAL_STATUS_BLOCKED,
            GOAL_STATUS_WAITING_OWNER,
            GOAL_STATUS_ACHIEVED,
            GOAL_STATUS_ABANDONED,
            GOAL_STATUS_SUPERSEDED,
        }
    ),
    GOAL_STATUS_BLOCKED: frozenset(
        {GOAL_STATUS_ACTIVE, GOAL_STATUS_ABANDONED, GOAL_STATUS_SUPERSEDED}
    ),
    GOAL_STATUS_WAITING_OWNER: frozenset(
        {GOAL_STATUS_ACTIVE, GOAL_STATUS_ABANDONED, GOAL_STATUS_SUPERSEDED}
    ),
    GOAL_STATUS_ACHIEVED: frozenset(),
    GOAL_STATUS_ABANDONED: frozenset(),
    GOAL_STATUS_SUPERSEDED: frozenset(),
}

assert set(_GOAL_EDGES) == set(GOAL_STATUSES)  # every status is a node in the table


class IllegalGoalTransition(ValueError):
    """Raised when a goal status transition is not permitted by the machine."""


def can_transition_goal(current: str, new: str) -> bool:
    if current not in _GOAL_EDGES:
        raise ValueError(f"unknown goal status: {current!r}")
    if new not in _GOAL_EDGES:
        raise ValueError(f"unknown goal status: {new!r}")
    if new == current:
        return True  # idempotent re-assert
    return new in _GOAL_EDGES[current]


def assert_goal_transition(current: str, new: str) -> str:
    if not can_transition_goal(current, new):
        raise IllegalGoalTransition(f"illegal goal transition {current} -> {new}")
    return new


__all__ = ["IllegalGoalTransition", "assert_goal_transition", "can_transition_goal"]
