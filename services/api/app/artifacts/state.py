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
    ARTIFACT_STATE_RENDERS_PENDING: frozenset(
        {ARTIFACT_STATE_READY, ARTIFACT_STATE_ARCHIVED}
    ),
    ARTIFACT_STATE_READY: frozenset(
        {ARTIFACT_STATE_CANONICAL_READY, ARTIFACT_STATE_ARCHIVED}
    ),
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
