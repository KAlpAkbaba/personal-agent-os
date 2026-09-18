"""OperatorTask: OBSERVE -> PLAN -> ACT -> OBSERVE AGAIN -> VERIFY POSTCONDITION.

docs/M19_DIGITAL_OPERATOR_SPEC.md §1, §4. A task is a fixed, deterministic sequence of
:class:`OperatorStep` (the PLAN, built by ``app.operator.plans`` before the task starts —
this milestone never re-plans mid-flight). Running it drives each step over an injected
``app.routines.dispatch.DeviceActionPort`` — the SAME protocol
``app.alarms.sequence.WakeSequence`` runs against, never a singleton: ACT (dispatch the
capability), OBSERVE AGAIN (the device's own read-back, already inside the
``DeviceRunResult`` a synchronous command returns) and VERIFY POSTCONDITION (the step's own
predicate over that result). A step whose postcondition still fails after its retries ends
the task ``failed`` with ``error_class = "postcondition_failed"``; a cancel flag is checked
before every attempt; every attempt's result is appended to the task's receipt trail,
whether it passed or not — the trail is the evidence, not just the happy path.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.routines.dispatch import DeviceActionPort, DeviceRunResult

# --------------------------------------------------------------------- vocabulary

STATUS_PLANNED = "planned"
STATUS_RUNNING = "running"
STATUS_VERIFYING = "verifying"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

TASK_STATUSES: tuple[str, ...] = (
    STATUS_PLANNED,
    STATUS_RUNNING,
    STATUS_VERIFYING,
    STATUS_SUCCEEDED,
    STATUS_FAILED,
    STATUS_CANCELLED,
)

#: Task/step-level error classes this module names (the tool layer may add its own, e.g.
#: "capability_missing" for no capable device, "no_current_window" for a resolver miss).
ERROR_POSTCONDITION_FAILED = "postcondition_failed"
ERROR_MODAL_OPEN = "modal_open"
ERROR_TIMEOUT = "timeout"
ERROR_CANCELLED = "cancelled"
ERROR_PRECONDITION_FAILED = "precondition_failed"

#: Interaction levels (spec §2's preference order), recorded on each step for the receipt.
LEVEL_API = "api"
LEVEL_DOM = "dom"
LEVEL_UI_AUTOMATION = "ui_automation"
LEVEL_KEYBOARD = "keyboard"
LEVEL_VISUAL = "visual"
LEVEL_POINTER = "pointer"

MAX_STEP_TIMEOUT_S: float = 30.0
MAX_STEP_RETRIES: int = 2
#: The longest pause between two looks at a postcondition. Bounded so a step can wait for
#: a page to load but can never park a mission.
MAX_RETRY_DELAY_S: float = 5.0
#: Indirection so a test can watch the waits instead of sleeping through them.
_sleep: Callable[[float], None] = time.sleep

PostconditionFn = Callable[[DeviceRunResult], bool]
PreconditionFn = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class OperatorStep:
    """One capability dispatch a plan needs, and how to verify it landed."""

    capability: str
    payload: dict[str, Any] = field(default_factory=dict)
    postcondition: PostconditionFn | None = None
    precondition: PreconditionFn | None = None
    timeout_s: float = 15.0
    retries: int = 1
    level: str = LEVEL_API
    #: A short name for receipts/errors ("open_application:launch"); defaults to the
    #: capability when the plan does not need to disambiguate steps sharing one.
    name: str = ""
    #: B39: fields resolved at DISPATCH time from what an EARLIER step of the same plan
    #: observed (the window id a launch produced). Merged over ``payload``; the receipt
    #: records the payload that was actually sent. A plan still never re-plans - this
    #: only lets a fixed step name a thing that did not exist when the plan was built.
    payload_from: Callable[[], dict[str, Any]] | None = None
    #: 2026-09-18: wait this long before looking again when the device answered but the
    #: postcondition did not hold YET - a page loading after Enter changes the window title
    #: a second or two later, and three looks in the same instant all see the old one.
    #: Zero (the default) keeps every existing step exactly as it was.
    retry_delay_s: float = 0.0

    def __post_init__(self) -> None:
        if self.timeout_s <= 0 or self.timeout_s > MAX_STEP_TIMEOUT_S:
            raise ValueError(f"OperatorStep.timeout_s must be in (0, {MAX_STEP_TIMEOUT_S}]")
        if self.retries < 0 or self.retries > MAX_STEP_RETRIES:
            raise ValueError(f"OperatorStep.retries must be in [0, {MAX_STEP_RETRIES}]")
        if self.retry_delay_s < 0 or self.retry_delay_s > MAX_RETRY_DELAY_S:
            raise ValueError(f"OperatorStep.retry_delay_s must be in [0, {MAX_RETRY_DELAY_S}]")

    @property
    def step_name(self) -> str:
        return self.name or self.capability


@dataclass(slots=True)
class StepReceipt:
    """One attempt's outcome — successful or not; every attempt is appended (module
    docstring). This IS the OBSERVE-AGAIN evidence: ``observed`` is the device's own
    read-back, never what the plan assumed would happen."""

    step_name: str
    capability: str
    payload: dict[str, Any]
    attempt: int
    ok: bool
    observed: dict[str, Any] = field(default_factory=dict)
    error_class: str = ""
    message: str = ""
    level: str = LEVEL_API
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "capability": self.capability,
            "payload": dict(self.payload),
            "attempt": self.attempt,
            "ok": self.ok,
            "observed": dict(self.observed),
            "error_class": self.error_class or None,
            "message": self.message,
            "level": self.level,
            "started_at": _iso(self.started_at),
            "completed_at": _iso(self.completed_at),
        }


def _iso(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class OperatorTask:
    """One operator task: a goal, a fixed plan, and the trail of what happened."""

    id: uuid.UUID
    goal: str
    steps: list[OperatorStep]
    plan_name: str = ""
    status: str = STATUS_PLANNED
    current_step: int = 0
    receipts: list[StepReceipt] = field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_class: str | None = None
    error_message: str = ""
    cancel_requested: bool = False
    #: The last OBSERVE this task made (a device result's own read-back), for the
    #: operator's own ActionReceipt.observed_after.local (spec's service.py deliverable)
    #: and for the UI state's ``window_title`` metadata.
    last_observed: dict[str, Any] = field(default_factory=dict)
    #: Filled by ``app.operator.service.OperatorService.start_task`` once the loop
    #: finishes: the ``ActionReceipt.as_dict()`` (speech empty) the tool overlays its
    #: Turkish sentence onto. ``None`` for a task no service has finished recording yet.
    action_receipt: dict[str, Any] | None = None

    def request_cancel(self) -> None:
        self.cancel_requested = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": str(self.id),
            "goal": self.goal,
            "plan_name": self.plan_name,
            "status": self.status,
            "current_step": self.current_step,
            "step_count": len(self.steps),
            "receipts": [r.as_dict() for r in self.receipts],
            "started_at": _iso(self.started_at) if self.started_at else None,
            "completed_at": _iso(self.completed_at) if self.completed_at else None,
            "error_class": self.error_class,
            "error_message": self.error_message,
            "last_observed": dict(self.last_observed),
            "action_receipt": dict(self.action_receipt) if self.action_receipt else None,
        }


def new_task(
    *, goal: str, plan_name: str, steps: Iterable[OperatorStep], task_id: uuid.UUID | None = None
) -> OperatorTask:
    return OperatorTask(
        id=task_id or uuid.uuid4(), goal=goal, plan_name=plan_name, steps=list(steps)
    )


def run_task(
    task: OperatorTask,
    device_action: DeviceActionPort,
    *,
    now: datetime | None = None,
    on_step: Callable[[OperatorTask, StepReceipt], None] | None = None,
) -> OperatorTask:
    """Run every step of ``task`` to completion, mutating and returning it.

    ``on_step`` is called after EVERY attempt (pass or fail) — the caller's hook for
    publishing UI state and writing ledger rows without this loop knowing about either.
    """
    start = now or datetime.now(UTC)
    task.status = STATUS_RUNNING
    task.started_at = start

    for index, step in enumerate(task.steps):
        task.current_step = index
        if task.cancel_requested:
            return _finish(task, STATUS_CANCELLED, ERROR_CANCELLED, "cancelled between steps")
        if step.precondition is not None and not step.precondition():
            return _finish(
                task,
                STATUS_FAILED,
                ERROR_PRECONDITION_FAILED,
                f"precondition failed: {step.step_name}",
            )

        ok = False
        last_result: DeviceRunResult | None = None
        attempt = 0
        while attempt <= step.retries:
            attempt += 1
            if task.cancel_requested:
                return _finish(task, STATUS_CANCELLED, ERROR_CANCELLED, "cancelled between steps")

            task.status = STATUS_RUNNING
            started_at = datetime.now(UTC)
            payload = dict(step.payload)
            if step.payload_from is not None:
                payload.update(step.payload_from())
            result = device_action.run(
                capability=step.capability,
                payload=payload,
                idempotency_key=f"operator:{task.id}:{index}:{attempt}",
                timeout_s=step.timeout_s,
            )
            completed_at = datetime.now(UTC)
            last_result = result
            # ACT is done; this is OBSERVE AGAIN -> VERIFY POSTCONDITION (module docstring).
            task.status = STATUS_VERIFYING
            modal = result.ok and isinstance(result.result, dict) and result.result.get("modal")
            postcondition_ok = bool(
                result.ok
                and not modal
                and (step.postcondition is None or step.postcondition(result))
            )
            receipt = StepReceipt(
                step_name=step.step_name,
                capability=step.capability,
                payload=payload,
                attempt=attempt,
                ok=postcondition_ok,
                observed=dict(result.result) if isinstance(result.result, dict) else {},
                error_class="" if result.ok else result.error_class,
                message=result.message,
                level=step.level,
                started_at=started_at,
                completed_at=completed_at,
            )
            task.receipts.append(receipt)
            if isinstance(result.result, dict) and result.result:
                task.last_observed = dict(result.result)
            if on_step is not None:
                on_step(task, receipt)

            if modal:
                dialog = result.result.get("modal") if isinstance(result.result, dict) else None
                return _finish(
                    task,
                    STATUS_FAILED,
                    ERROR_MODAL_OPEN,
                    f"a dialog is open: {dialog!r}",
                )
            if not result.ok:
                if attempt <= step.retries:
                    continue
                return _finish(
                    task,
                    STATUS_FAILED,
                    result.error_class or "device_error",
                    result.message or f"{step.step_name} failed",
                )
            if postcondition_ok:
                ok = True
                break
            if step.retry_delay_s and attempt <= step.retries:
                _sleep(step.retry_delay_s)
            # The device call succeeded but the postcondition did not hold yet; retry
            # while attempts remain (this is the VERIFY -> retry loop the task brief asks
            # for — a step whose postcondition still fails after retries fails the task).
        if not ok:
            return _finish(
                task,
                STATUS_FAILED,
                ERROR_POSTCONDITION_FAILED,
                f"postcondition never held for {step.step_name}"
                + (f" (last observed: {last_result.result!r})" if last_result else ""),
            )

    return _finish(task, STATUS_SUCCEEDED, None, "")


def _finish(task: OperatorTask, status: str, error_class: str | None, message: str) -> OperatorTask:
    task.status = status
    task.error_class = error_class
    task.error_message = message
    task.completed_at = datetime.now(UTC)
    return task


__all__ = [
    "ERROR_CANCELLED",
    "ERROR_MODAL_OPEN",
    "ERROR_POSTCONDITION_FAILED",
    "ERROR_PRECONDITION_FAILED",
    "ERROR_TIMEOUT",
    "LEVEL_API",
    "LEVEL_DOM",
    "LEVEL_KEYBOARD",
    "LEVEL_POINTER",
    "LEVEL_UI_AUTOMATION",
    "LEVEL_VISUAL",
    "MAX_STEP_RETRIES",
    "MAX_STEP_TIMEOUT_S",
    "OperatorStep",
    "OperatorTask",
    "STATUS_CANCELLED",
    "STATUS_FAILED",
    "STATUS_PLANNED",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
    "STATUS_VERIFYING",
    "StepReceipt",
    "TASK_STATUSES",
    "new_task",
    "run_task",
]
