"""Unit tests: app.operator.task (docs/M19_DIGITAL_OPERATOR_SPEC.md §1, §4).

OBSERVE -> PLAN -> ACT -> OBSERVE AGAIN -> VERIFY POSTCONDITION, over a fake
``DeviceActionPort``. No database, no network, no device — the loop itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.operator.task import (
    ERROR_CANCELLED,
    ERROR_MODAL_OPEN,
    ERROR_POSTCONDITION_FAILED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    OperatorStep,
    new_task,
    run_task,
)
from app.routines.dispatch import DeviceRunResult
from tests.alarms_support import window_id as window_id_for


@dataclass
class SequencedDeviceAction:
    """Queued results per capability, one popped per call; the LAST queued result
    repeats once its queue is exhausted (so a test only scripts what changes)."""

    queues: dict[str, list[DeviceRunResult]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def run(
        self, *, capability: str, payload: dict[str, Any], idempotency_key: str, timeout_s: float
    ) -> DeviceRunResult:
        self.calls.append(
            {
                "capability": capability,
                "payload": dict(payload),
                "idempotency_key": idempotency_key,
                "timeout_s": timeout_s,
            }
        )
        queue = self.queues.get(capability)
        if not queue:
            return DeviceRunResult(True, result={})
        return queue.pop(0) if len(queue) > 1 else queue[0]


def ok(**result: Any) -> DeviceRunResult:
    return DeviceRunResult(True, result=result)


def failed(error_class: str = "dependency_unavailable") -> DeviceRunResult:
    return DeviceRunResult(False, error_class, error_class)


def _task(steps: list[OperatorStep]) -> Any:
    return new_task(goal="test goal", plan_name="test_plan", steps=steps, task_id=uuid.uuid4())


# ------------------------------------------------------------------------- happy path


def test_every_step_runs_and_the_receipt_trail_names_it() -> None:
    device = SequencedDeviceAction(
        queues={"a.step": [ok(value=1)], "b.step": [ok(value=2)]}
    )
    task = _task(
        [
            OperatorStep(capability="a.step", payload={"x": 1}, name="first"),
            OperatorStep(capability="b.step", payload={"y": 2}, name="second"),
        ]
    )
    run_task(task, device)

    assert task.status == STATUS_SUCCEEDED
    assert task.error_class is None
    assert [c["capability"] for c in device.calls] == ["a.step", "b.step"]
    assert [r.step_name for r in task.receipts] == ["first", "second"]
    assert all(r.ok for r in task.receipts)
    assert task.last_observed == {"value": 2}
    assert task.started_at is not None and task.completed_at is not None
    # The receipt trail is JSON-round-trippable evidence, not an internal detail.
    as_dict = task.receipts[0].as_dict()
    assert as_dict["capability"] == "a.step"
    assert as_dict["ok"] is True
    assert as_dict["attempt"] == 1


def test_task_as_dict_names_the_plan_and_every_receipt() -> None:
    device = SequencedDeviceAction(queues={"a.step": [ok()]})
    task = _task([OperatorStep(capability="a.step", payload={})])
    run_task(task, device)
    out = task.as_dict()
    assert out["status"] == STATUS_SUCCEEDED
    assert out["plan_name"] == "test_plan"
    assert out["step_count"] == 1
    assert len(out["receipts"]) == 1


# --------------------------------------------------------------- postcondition retries


def test_a_postcondition_that_fails_once_then_holds_retries_and_succeeds() -> None:
    calls = {"n": 0}

    def flaky_postcondition(result: DeviceRunResult) -> bool:
        calls["n"] += 1
        return calls["n"] >= 2  # fails the first check, holds the second

    device = SequencedDeviceAction(queues={"a.step": [ok(state="pending"), ok(state="ready")]})
    step = OperatorStep(
        capability="a.step", payload={}, postcondition=flaky_postcondition, retries=1
    )
    task = _task([step])
    run_task(task, device)

    assert task.status == STATUS_SUCCEEDED
    assert len(task.receipts) == 2
    assert task.receipts[0].ok is False
    assert task.receipts[1].ok is True
    assert len(device.calls) == 2


def test_a_postcondition_that_never_holds_fails_after_its_retries() -> None:
    device = SequencedDeviceAction(queues={"a.step": [ok(state="pending")]})
    step = OperatorStep(
        capability="a.step", payload={}, postcondition=lambda r: False, retries=2
    )
    task = _task([step])
    run_task(task, device)

    assert task.status == STATUS_FAILED
    assert task.error_class == ERROR_POSTCONDITION_FAILED
    # retries=2 -> up to 3 attempts total (the first try plus two retries).
    assert len(task.receipts) == 3
    assert all(not r.ok for r in task.receipts)


def test_a_device_failure_is_retried_then_fails_with_its_own_error_class() -> None:
    device = SequencedDeviceAction(queues={"a.step": [failed("dependency_unavailable")]})
    step = OperatorStep(capability="a.step", payload={}, retries=1)
    task = _task([step])
    run_task(task, device)

    assert task.status == STATUS_FAILED
    assert task.error_class == "dependency_unavailable"
    assert len(device.calls) == 2  # the first try plus one retry


def test_a_device_failure_becomes_a_timeout_error_class_when_the_device_says_so() -> None:
    device = SequencedDeviceAction(
        queues={"a.step": [DeviceRunResult(False, "timeout", "expired")]}
    )
    task = _task([OperatorStep(capability="a.step", payload={}, retries=0)])
    run_task(task, device)
    assert task.status == STATUS_FAILED
    assert task.error_class == "timeout"
    assert len(device.calls) == 1  # retries=0: no retry


# ------------------------------------------------------------------------------ modal


def test_a_modal_in_the_result_fails_the_task_naming_the_dialog() -> None:
    device = SequencedDeviceAction(
        queues={"app.close": [ok(closed=False, modal={"title": "Save changes?"})]}
    )
    task = _task([OperatorStep(capability="app.close", payload={"window_id": window_id_for(1)})])
    run_task(task, device)

    assert task.status == STATUS_FAILED
    assert task.error_class == ERROR_MODAL_OPEN
    assert "Save changes?" in task.error_message
    assert task.receipts[-1].ok is False


# ----------------------------------------------------------------------------- cancel


def test_cancel_between_steps_stops_before_the_next_one() -> None:
    device = SequencedDeviceAction(queues={"a.step": [ok()], "b.step": [ok()]})
    task = _task(
        [
            OperatorStep(capability="a.step", payload={}, name="first"),
            OperatorStep(capability="b.step", payload={}, name="second"),
        ]
    )

    def cancel_after_first(t: Any, receipt: Any) -> None:
        if receipt.step_name == "first":
            t.request_cancel()

    run_task(task, device, on_step=cancel_after_first)

    assert task.status == STATUS_CANCELLED
    assert task.error_class == ERROR_CANCELLED
    assert [c["capability"] for c in device.calls] == ["a.step"]  # "second" never ran
    assert len(task.receipts) == 1


def test_cancel_flagged_before_the_task_ever_starts_a_step() -> None:
    device = SequencedDeviceAction(queues={"a.step": [ok()]})
    task = _task([OperatorStep(capability="a.step", payload={})])
    task.request_cancel()
    run_task(task, device)
    assert task.status == STATUS_CANCELLED
    assert device.calls == []


# --------------------------------------------------------------------- on_step hook


def test_on_step_is_called_for_every_attempt_pass_or_fail() -> None:
    seen: list[tuple[str, bool]] = []
    device = SequencedDeviceAction(queues={"a.step": [failed(), ok()]})
    step = OperatorStep(capability="a.step", payload={}, retries=1)
    task = _task([step])
    run_task(task, device, on_step=lambda t, r: seen.append((r.step_name, r.ok)))
    assert task.status == STATUS_SUCCEEDED
    assert len(seen) == 2
    assert seen[0][1] is False
    assert seen[1][1] is True


# ------------------------------------------------------------------- step validation


def test_step_timeout_and_retries_are_bounded() -> None:
    with pytest.raises(ValueError):
        OperatorStep(capability="a.step", payload={}, timeout_s=31)
    with pytest.raises(ValueError):
        OperatorStep(capability="a.step", payload={}, timeout_s=0)
    with pytest.raises(ValueError):
        OperatorStep(capability="a.step", payload={}, retries=3)
    with pytest.raises(ValueError):
        OperatorStep(capability="a.step", payload={}, retries=-1)
    # The bounds themselves are fine.
    OperatorStep(capability="a.step", payload={}, timeout_s=30, retries=2)


def test_precondition_failure_never_dispatches_the_step() -> None:
    device = SequencedDeviceAction(queues={"a.step": [ok()]})
    step = OperatorStep(capability="a.step", payload={}, precondition=lambda: False)
    task = _task([step])
    run_task(task, device)
    assert task.status == STATUS_FAILED
    assert task.error_class == "precondition_failed"
    assert device.calls == []
