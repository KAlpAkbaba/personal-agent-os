"""B06 req 67/68: what a task status MEANS, not just which ones are terminal.

The world model answered "how many tasks are running?" with "how many are not terminal", and
on 2026-09-12 production therefore reported ten running tasks while nothing ran: the ten were
finished research sitting in READY, waiting for the owner to be told. Two different questions
had one answer.

These tests hold the four meaning-buckets to cover the vocabulary exactly once, so a status
added later has to be placed deliberately instead of joining "running" by default.
"""

from __future__ import annotations

from app.artifacts import models as task_models
from app.artifacts.state import (
    TASK_ACTIVE_STATUSES,
    TASK_AWAITING_OWNER_STATUSES,
    TASK_PENDING_STATUSES,
    TASK_TERMINAL_STATUSES,
    TASK_WAITING_STATUSES,
)

_BUCKETS = {
    "active": TASK_ACTIVE_STATUSES,
    "pending": TASK_PENDING_STATUSES,
    "waiting_external": TASK_WAITING_STATUSES,
    "awaiting_owner": TASK_AWAITING_OWNER_STATUSES,
    "terminal": TASK_TERMINAL_STATUSES,
}


def _vocabulary() -> set[str]:
    """Every TASK_STATUS_* the models module declares - read, not restated."""
    return {
        value
        for name, value in vars(task_models).items()
        if name.startswith("TASK_STATUS_") and isinstance(value, str)
    }


def test_the_vocabulary_is_found_at_all() -> None:
    vocabulary = _vocabulary()
    assert len(vocabulary) >= 9, sorted(vocabulary)


def test_every_status_is_in_exactly_one_bucket() -> None:
    vocabulary = _vocabulary()
    placed: dict[str, list[str]] = {status: [] for status in vocabulary}
    for name, bucket in _BUCKETS.items():
        for status in bucket:
            assert status in vocabulary, f"{name} names {status!r}, which is not a task status"
            placed[status].append(name)

    unplaced = sorted(status for status, names in placed.items() if not names)
    assert not unplaced, (
        f"these statuses are in no bucket: {unplaced}. Decide what each one MEANS - is the "
        "system working on it, waiting on somebody else, or finished and waiting for the "
        "owner? - rather than letting it be counted as running by default."
    )
    doubled = sorted(f"{s} in {names}" for s, names in placed.items() if len(names) > 1)
    assert not doubled, f"these statuses are in more than one bucket: {doubled}"


def test_finished_work_is_not_active_work() -> None:
    """The defect, as a rule: READY is the owner's to look at, not ours to work on."""
    assert TASK_AWAITING_OWNER_STATUSES & TASK_ACTIVE_STATUSES == set()
    assert task_models.TASK_STATUS_READY in TASK_AWAITING_OWNER_STATUSES
    assert task_models.TASK_STATUS_READY not in TASK_ACTIVE_STATUSES


def test_the_active_bucket_is_the_smaller_claim() -> None:
    """A guard against the easy regression: widening "active" back to "not terminal"."""
    non_terminal = _vocabulary() - TASK_TERMINAL_STATUSES
    assert TASK_ACTIVE_STATUSES < non_terminal
    assert len(TASK_ACTIVE_STATUSES) < len(non_terminal)
