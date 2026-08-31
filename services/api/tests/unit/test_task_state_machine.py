"""Unit tests: task + artifact state machines."""

import pytest

from app.artifacts import state
from app.artifacts.models import (
    ARTIFACT_STATE_CANONICAL_READY,
    ARTIFACT_STATE_DRAFT,
    ARTIFACT_STATE_READY,
    ARTIFACT_STATE_RENDERS_PENDING,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_CREATED,
    TASK_STATUS_FAILED_TERMINAL,
    TASK_STATUS_PLANNED,
    TASK_STATUS_READY,
    TASK_STATUS_RENDERING,
    TASK_STATUS_RUNNING,
)


def test_happy_path_research_task_flow() -> None:
    chain = [
        TASK_STATUS_CREATED,
        TASK_STATUS_PLANNED,
        TASK_STATUS_RUNNING,
        TASK_STATUS_RENDERING,
        TASK_STATUS_READY,
    ]
    for cur, nxt in zip(chain, chain[1:], strict=False):
        assert state.can_transition_task(cur, nxt)
        assert state.assert_task_transition(cur, nxt) == nxt


def test_illegal_task_jump_raises() -> None:
    with pytest.raises(state.IllegalTransition):
        state.assert_task_transition(TASK_STATUS_CREATED, TASK_STATUS_READY)
    with pytest.raises(state.IllegalTransition):
        state.assert_task_transition(TASK_STATUS_RENDERING, TASK_STATUS_PLANNED)


def test_task_idempotent_reassert_allowed() -> None:
    assert state.can_transition_task(TASK_STATUS_RUNNING, TASK_STATUS_RUNNING)


def test_task_error_edges_from_non_terminal() -> None:
    assert state.can_transition_task(TASK_STATUS_RUNNING, TASK_STATUS_FAILED_TERMINAL)
    # terminal state cannot move on
    assert not state.can_transition_task(TASK_STATUS_COMPLETED, TASK_STATUS_FAILED_TERMINAL)


def test_task_unknown_status_raises_value_error() -> None:
    with pytest.raises(ValueError):
        state.can_transition_task("BOGUS", TASK_STATUS_READY)


def test_artifact_happy_path() -> None:
    chain = [
        ARTIFACT_STATE_DRAFT,
        ARTIFACT_STATE_CANONICAL_READY,
        ARTIFACT_STATE_RENDERS_PENDING,
        ARTIFACT_STATE_READY,
    ]
    for cur, nxt in zip(chain, chain[1:], strict=False):
        assert state.assert_artifact_transition(cur, nxt) == nxt


def test_artifact_illegal_jump_raises() -> None:
    with pytest.raises(state.IllegalTransition):
        state.assert_artifact_transition(ARTIFACT_STATE_DRAFT, ARTIFACT_STATE_READY)
