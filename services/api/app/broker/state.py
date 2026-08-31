"""Command status state machine (DEVICE_PROTOCOL.md §5).

Broker-side lifecycle:

    pending -> delivered -> accepted -> running -> succeeded|failed
    pending|delivered ----------------------------> expired (server sweep)
    pending (undelivered) ------------------------> cancelled (REST cancel)

Ack transitions from the agent are monotonic. Because delivery is
at-least-once, duplicate and stale acks are tolerated (ignored, not errors);
only a *conflicting* terminal re-ack (a different terminal status than the
recorded one) is reported back as a protocol error.
"""

from enum import StrEnum

from app.broker.models import (
    COMMAND_STATUS_ACCEPTED,
    COMMAND_STATUS_CANCELLED,
    COMMAND_STATUS_DELIVERED,
    COMMAND_STATUS_EXPIRED,
    COMMAND_STATUS_FAILED,
    COMMAND_STATUS_PENDING,
    COMMAND_STATUS_RUNNING,
    COMMAND_STATUS_SUCCEEDED,
)

TERMINAL_STATUSES = frozenset(
    {
        COMMAND_STATUS_SUCCEEDED,
        COMMAND_STATUS_FAILED,
        COMMAND_STATUS_EXPIRED,
        COMMAND_STATUS_CANCELLED,
    }
)

_RANK = {
    COMMAND_STATUS_PENDING: 0,
    COMMAND_STATUS_DELIVERED: 1,
    COMMAND_STATUS_ACCEPTED: 2,
    COMMAND_STATUS_RUNNING: 3,
    COMMAND_STATUS_SUCCEEDED: 4,
    COMMAND_STATUS_FAILED: 4,
    COMMAND_STATUS_EXPIRED: 4,
    COMMAND_STATUS_CANCELLED: 4,
}


class TransitionDecision(StrEnum):
    APPLY = "apply"  # move forward, persist the new status
    IGNORE_DUPLICATE = "ignore_duplicate"  # idempotent re-ack, no-op, not an error
    IGNORE_STALE = "ignore_stale"  # backward non-terminal ack (redelivery race), no-op
    CONFLICT = "conflict"  # different terminal status than recorded -> protocol error


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def classify_transition(current: str, new: str) -> TransitionDecision:
    """Classify an ack from the agent given the currently persisted status."""
    if current not in _RANK:
        raise ValueError(f"unknown current status: {current!r}")
    if new not in _RANK:
        raise ValueError(f"unknown ack status: {new!r}")

    if current in TERMINAL_STATUSES:
        if new == current:
            return TransitionDecision.IGNORE_DUPLICATE
        if new in TERMINAL_STATUSES:
            return TransitionDecision.CONFLICT
        return TransitionDecision.IGNORE_STALE

    if _RANK[new] > _RANK[current]:
        return TransitionDecision.APPLY
    if new == current:
        return TransitionDecision.IGNORE_DUPLICATE
    return TransitionDecision.IGNORE_STALE
