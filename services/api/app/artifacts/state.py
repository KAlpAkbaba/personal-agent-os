"""Task + artifact state machines (ARCHITECTURE.md §4/§5).

Transitions are validated so the research workflow (and any future task type)
cannot record an illegal jump. The happy path realized in M3 is:

    Task:     CREATED -> PLANNED -> RUNNING -> RENDERING -> READY [-> PRESENTING -> COMPLETED]
    Artifact: DRAFT -> CANONICAL_READY -> RENDERS_PENDING -> READY [-> ARCHIVED]

Error edges from ARCHITECTURE.md §4 are permitted from any non-terminal state
so a failing workflow can record FAILED_RECOVERABLE / FAILED_TERMINAL.
"""

from app.artifacts.models import (
    ARTIFACT_STATE_ARCHIVED,
    ARTIFACT_STATE_CANONICAL_READY,
    ARTIFACT_STATE_DRAFT,
    ARTIFACT_STATE_READY,
    ARTIFACT_STATE_RENDERS_PENDING,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_CREATED,
    TASK_STATUS_FAILED_RECOVERABLE,
    TASK_STATUS_FAILED_TERMINAL,
    TASK_STATUS_PLANNED,
    TASK_STATUS_PRESENTING,
    TASK_STATUS_READY,
    TASK_STATUS_RENDERING,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_EXTERNAL,
)

TASK_TERMINAL_STATUSES = frozenset({TASK_STATUS_COMPLETED, TASK_STATUS_FAILED_TERMINAL})

# ---------------------------------------------------------------- what a status MEANS
#
# "Not terminal" is one fact and "the system is working on it" is another, and the world
# model used to answer the second question with the first: `tasks.running_count` was every
# task that had not reached COMPLETED or FAILED_TERMINAL. On 2026-09-12 production therefore
# reported 10 running tasks while nothing at all was running - the ten were finished research
# sitting in READY, waiting for the owner to be told. The owner asks "what are you doing?" and
# the honest answer to that is the first set below, not the complement of the last one.
#
# `test_task_status_buckets_partition_the_vocabulary` holds these four to cover every status
# exactly once, so a new status has to be placed deliberately rather than joining "running"
# by default.

#: The system is doing work right now.
TASK_ACTIVE_STATUSES = frozenset({TASK_STATUS_RUNNING, TASK_STATUS_RENDERING})

#: Accepted, not started - or failed in a way that will be retried.
TASK_PENDING_STATUSES = frozenset(
    {TASK_STATUS_CREATED, TASK_STATUS_PLANNED, TASK_STATUS_FAILED_RECOVERABLE}
)

#: Blocked on something outside this system; no work of ours is happening.
TASK_WAITING_STATUSES = frozenset({TASK_STATUS_WAITING_EXTERNAL})

#: The work is DONE and the owner has not had it yet. Not running, and not finished either -
#: which is exactly why it deserves its own count instead of hiding inside one of the others.
TASK_AWAITING_OWNER_STATUSES = frozenset({TASK_STATUS_READY, TASK_STATUS_PRESENTING})

_TASK_EDGES: dict[str, frozenset[str]] = {
    TASK_STATUS_CREATED: frozenset({TASK_STATUS_PLANNED}),
    TASK_STATUS_PLANNED: frozenset({TASK_STATUS_RUNNING}),
    TASK_STATUS_RUNNING: frozenset(
        {TASK_STATUS_WAITING_EXTERNAL, TASK_STATUS_RENDERING, TASK_STATUS_READY}
    ),
    TASK_STATUS_WAITING_EXTERNAL: frozenset({TASK_STATUS_RUNNING, TASK_STATUS_RENDERING}),
    TASK_STATUS_RENDERING: frozenset({TASK_STATUS_READY}),
    TASK_STATUS_READY: frozenset({TASK_STATUS_PRESENTING, TASK_STATUS_COMPLETED}),
    TASK_STATUS_PRESENTING: frozenset({TASK_STATUS_READY, TASK_STATUS_COMPLETED}),
    TASK_STATUS_COMPLETED: frozenset(),
    TASK_STATUS_FAILED_RECOVERABLE: frozenset({TASK_STATUS_RUNNING, TASK_STATUS_FAILED_TERMINAL}),
    TASK_STATUS_FAILED_TERMINAL: frozenset(),
}

# Error edges allowed from any non-terminal state.
_TASK_ERROR_TARGETS = frozenset({TASK_STATUS_FAILED_RECOVERABLE, TASK_STATUS_FAILED_TERMINAL})

_ARTIFACT_EDGES: dict[str, frozenset[str]] = {
    ARTIFACT_STATE_DRAFT: frozenset({ARTIFACT_STATE_CANONICAL_READY, ARTIFACT_STATE_ARCHIVED}),
    ARTIFACT_STATE_CANONICAL_READY: frozenset(
        {ARTIFACT_STATE_RENDERS_PENDING, ARTIFACT_STATE_READY, ARTIFACT_STATE_ARCHIVED}
    ),
    ARTIFACT_STATE_RENDERS_PENDING: frozenset({ARTIFACT_STATE_READY, ARTIFACT_STATE_ARCHIVED}),
    ARTIFACT_STATE_READY: frozenset({ARTIFACT_STATE_CANONICAL_READY, ARTIFACT_STATE_ARCHIVED}),
    ARTIFACT_STATE_ARCHIVED: frozenset(),
}


class IllegalTransition(ValueError):
    """Raised when a state transition is not permitted by the machine."""


def can_transition_task(current: str, new: str) -> bool:
    if current not in _TASK_EDGES:
        raise ValueError(f"unknown task status: {current!r}")
    if new not in _TASK_EDGES:
        raise ValueError(f"unknown task status: {new!r}")
    if new == current:
        return True  # idempotent re-assert (workflow retries)
    if new in _TASK_ERROR_TARGETS and current not in TASK_TERMINAL_STATUSES:
        return True
    return new in _TASK_EDGES[current]


def assert_task_transition(current: str, new: str) -> str:
    if not can_transition_task(current, new):
        raise IllegalTransition(f"illegal task transition {current} -> {new}")
    return new


def can_transition_artifact(current: str, new: str) -> bool:
    if current not in _ARTIFACT_EDGES:
        raise ValueError(f"unknown artifact state: {current!r}")
    if new not in _ARTIFACT_EDGES:
        raise ValueError(f"unknown artifact state: {new!r}")
    if new == current:
        return True
    return new in _ARTIFACT_EDGES[current]


def assert_artifact_transition(current: str, new: str) -> str:
    if not can_transition_artifact(current, new):
        raise IllegalTransition(f"illegal artifact transition {current} -> {new}")
    return new
