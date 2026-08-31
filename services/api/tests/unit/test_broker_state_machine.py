"""Unit tests: monotonic command ack state machine."""

import pytest

from app.broker.state import TransitionDecision, classify_transition, is_terminal


@pytest.mark.parametrize(
    ("current", "new"),
    [
        ("pending", "accepted"),
        ("pending", "running"),
        ("pending", "succeeded"),
        ("delivered", "accepted"),
        ("delivered", "running"),
        ("delivered", "failed"),
        ("accepted", "running"),
        ("accepted", "succeeded"),
        ("running", "succeeded"),
        ("running", "failed"),
    ],
)
def test_forward_transitions_apply(current: str, new: str) -> None:
    assert classify_transition(current, new) is TransitionDecision.APPLY


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "expired", "cancelled"])
def test_idempotent_terminal_reack_is_not_error(terminal: str) -> None:
    assert classify_transition(terminal, terminal) is TransitionDecision.IGNORE_DUPLICATE


def test_conflicting_terminal_reack_is_conflict() -> None:
    assert classify_transition("succeeded", "failed") is TransitionDecision.CONFLICT
    assert classify_transition("failed", "succeeded") is TransitionDecision.CONFLICT
    assert classify_transition("expired", "succeeded") is TransitionDecision.CONFLICT
    assert classify_transition("cancelled", "failed") is TransitionDecision.CONFLICT


@pytest.mark.parametrize(
    ("current", "new"),
    [
        ("running", "accepted"),
        ("accepted", "delivered"),
        ("succeeded", "running"),
        ("cancelled", "accepted"),
    ],
)
def test_backward_acks_are_ignored_never_applied(current: str, new: str) -> None:
    decision = classify_transition(current, new)
    assert decision in (TransitionDecision.IGNORE_STALE, TransitionDecision.IGNORE_DUPLICATE)
    assert decision is not TransitionDecision.APPLY


def test_duplicate_non_terminal_ack_ignored() -> None:
    assert classify_transition("accepted", "accepted") is TransitionDecision.IGNORE_DUPLICATE
    assert classify_transition("running", "running") is TransitionDecision.IGNORE_DUPLICATE


def test_unknown_statuses_raise() -> None:
    with pytest.raises(ValueError):
        classify_transition("nope", "running")
    with pytest.raises(ValueError):
        classify_transition("running", "nope")


def test_is_terminal() -> None:
    assert all(is_terminal(s) for s in ("succeeded", "failed", "expired", "cancelled"))
    assert not any(is_terminal(s) for s in ("pending", "delivered", "accepted", "running"))
