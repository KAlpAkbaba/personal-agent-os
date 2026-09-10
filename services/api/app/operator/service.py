"""OperatorService: runs one plan synchronously, keeps the current task, records the trail.

docs/M19_DIGITAL_OPERATOR_SPEC.md §4. Every plan a voice tool builds today is <= 3 steps
(spec §7's own scope), so :meth:`OperatorService.start_task` runs the whole
OBSERVE -> PLAN -> ACT -> OBSERVE AGAIN -> VERIFY loop inside the tool call itself — no
``ctx.followups``, no async completion, unlike ``research.start``. The running task is
kept on the live runtime (``ToolContext.live["operator"]``, wired by ``create_app`` the
same way the wake sequence and the device-status registry are, docs/M18_ACTION_CONTRACT.md
§4) so the ONE router can ask "is a task running right now?" for the ringing-aware
Cancel/Status pair (spec §3), and a genuinely concurrent second request can cancel it.

A process-wide module registry (:func:`register_operator_service` /
:func:`get_operator_service`) mirrors ``app.devices.commands.register_broker_runtime`` —
the same object ``create_app`` hands to ``RealtimeVoiceRuntime.register_live`` is reachable
from ``record_client_events`` (which builds no ``ToolContext`` at all: an utterance is
resolved before any tool is chosen).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_FAILED,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_OPERATOR_TASK_CANCELLED,
    EVENT_TYPE_OPERATOR_TASK_COMPLETED,
    EVENT_TYPE_OPERATOR_TASK_FAILED,
    EVENT_TYPE_OPERATOR_TASK_STARTED,
    SUBSYSTEM_OPERATOR,
)
from app.logging import get_logger
from app.operator import focus as focus_module
from app.operator.capabilities import RECEIPT_BY_PLAN, receipt_capability_for_plan
from app.operator.models import FOCUS_KIND_WINDOW
from app.operator.task import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    STATUS_VERIFYING,
    OperatorStep,
    OperatorTask,
    StepReceipt,
    new_task,
    run_task,
)
from app.routines.dispatch import DeviceActionPort
from app.uistate import UiState
from app.uistate import publish as publish_ui_state

logger = get_logger("app.operator.service")

_RUNNING_STATUSES: frozenset[str] = frozenset({STATUS_RUNNING, STATUS_VERIFYING})

_LEDGER_EVENT_BY_STATUS: dict[str, str] = {
    STATUS_SUCCEEDED: EVENT_TYPE_OPERATOR_TASK_COMPLETED,
    STATUS_FAILED: EVENT_TYPE_OPERATOR_TASK_FAILED,
    STATUS_CANCELLED: EVENT_TYPE_OPERATOR_TASK_CANCELLED,
}


@dataclass(frozen=True, slots=True)
class Plan:
    """The fixed step sequence and identity a voice tool asks the service to run.

    ``name`` must be one ``app.operator.capabilities.RECEIPT_BY_PLAN`` carries. The check
    is here, at construction, rather than at the receipt: by the time a receipt is minted
    the plan has already run, and a plan whose outcome cannot be recorded under a declared
    capability is an action that would go into the ledger under a name nothing can look up
    — which is exactly the defect this table was added to end. Refusing it before the
    first device call turns that into an authoring error instead of a silent one.
    """

    name: str
    goal: str
    steps: list[OperatorStep]

    def __post_init__(self) -> None:
        if self.name not in RECEIPT_BY_PLAN:
            raise ValueError(
                f"operator plan {self.name!r} has no receipt capability; add it to "
                "app.operator.capabilities.RECEIPT_BY_PLAN "
                f"(known: {sorted(RECEIPT_BY_PLAN)})"
            )


class OperatorService:
    """Owns the one currently-running task (single-owner system: there is at most one
    interactive desktop, so there is at most one operator task in flight)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: OperatorTask | None = None
        self._last: OperatorTask | None = None

    # ------------------------------------------------------------- reads

    def is_running(self) -> bool:
        with self._lock:
            current = self._current
        return current is not None and current.status in _RUNNING_STATUSES

    def status(self) -> OperatorTask | None:
        with self._lock:
            return self._current if self._current is not None else self._last

    def last_task(self) -> OperatorTask | None:
        with self._lock:
            return self._last

    # ------------------------------------------------------------- test/resume seam
    #
    # The exact slot start_task fills, exposed so a fixture (or a resumed process) can
    # observe "a task is running" without driving one to completion — never a bypass of
    # the loop, just the state a real concurrent request would also occupy.
    def set_current_task(self, task: OperatorTask | None) -> None:
        with self._lock:
            self._current = task

    # ------------------------------------------------------------- mutation

    def cancel(self, task_id: str | None = None) -> bool:
        """Cancel the running task (checked between steps by ``run_task``), or report
        nothing runs. ``task_id`` narrows to a specific task; omitted, the current one."""
        with self._lock:
            current = self._current
        if current is None:
            return False
        if task_id is not None and str(current.id) != str(task_id):
            return False
        current.request_cancel()
        return True

    def start_task(
        self,
        db: Session | None,
        plan: Plan,
        device_action: DeviceActionPort,
        *,
        now: datetime | None = None,
        session_id: str | None = None,
    ) -> OperatorTask:
        """Run ``plan`` to completion (spec §7: every voice plan is <= 3 steps, so this
        always returns synchronously) and record the trail: one ``operator.task.*``
        ledger row per lifecycle transition, and one ``ActionReceipt`` (capability
        ``app.operator.capabilities.RECEIPT_BY_PLAN[plan.name]`` — the registered tool
        name, subsystem ``operator``, ``observed_after.local`` the last OBSERVE, the plan
        itself under ``observed_after.server.plan``).
        ``speech`` is left empty here — the voice tool that called this
        knows the Turkish sentence and overlays it onto the returned receipt dict
        (``task.action_receipt``); the shape and the statuses are decided in one place.
        """
        started_at = now or datetime.now(UTC)
        task = new_task(goal=plan.goal, plan_name=plan.name, steps=plan.steps)
        with self._lock:
            self._current = task

        self._ledger(db, task, EVENT_TYPE_OPERATOR_TASK_STARTED, started_at)
        publish_ui_state(
            UiState.OPERATOR_RUNNING,
            subsystem=SUBSYSTEM_OPERATOR,
            task_id=str(task.id),
            label=plan.name[:64],
            metadata={"step": 0, "step_count": len(plan.steps)},
        )

        def _on_step(t: OperatorTask, receipt: StepReceipt) -> None:
            state = UiState.OPERATOR_RUNNING if receipt.ok else UiState.OPERATOR_VERIFYING
            observed = receipt.observed if isinstance(receipt.observed, dict) else {}
            observed_window = observed.get("window")
            window_title = ""
            if isinstance(observed_window, dict):
                window_title = str(observed_window.get("title") or "")[:64]
            elif receipt.capability == "app.launch":
                window_title = str(observed.get("title") or "")[:64]
            publish_ui_state(
                state,
                subsystem=SUBSYSTEM_OPERATOR,
                task_id=str(t.id),
                label=receipt.step_name[:64],
                metadata={
                    "step": t.current_step,
                    "capability": receipt.capability[:64],
                    **({"window_title": window_title} if window_title else {}),
                },
            )
            if db is not None and receipt.ok:
                self._maybe_set_window_focus(db, receipt, now=t.completed_at)

        try:
            run_task(task, device_action, now=started_at, on_step=_on_step)
        finally:
            with self._lock:
                self._current = None
                self._last = task

        event_type = _LEDGER_EVENT_BY_STATUS.get(task.status, EVENT_TYPE_OPERATOR_TASK_FAILED)
        self._ledger(db, task, event_type, task.completed_at or started_at)
        if task.status != STATUS_SUCCEEDED:
            publish_ui_state(
                UiState.OPERATOR_FAILED,
                subsystem=SUBSYSTEM_OPERATOR,
                task_id=str(task.id),
                label=plan.name[:64],
                severity="warning" if task.status == STATUS_FAILED else "info",
                metadata={"error_class": (task.error_class or "")[:64]},
            )

        task.action_receipt = self._receipt(db, task, session_id=session_id)
        return task

    def _maybe_set_window_focus(
        self, db: Session, receipt: StepReceipt, *, now: datetime | None
    ) -> None:
        """Durable window focus (spec §4): set on every successful step that observed a
        FOREGROUND window (``window.current/.activate/.maximize/.minimize/.restore`` all
        answer ``{"window": {...}}``), and on ``app.launch`` (whose own result carries
        ``window_id``/``title`` at the top level, spec §2's ``app.launch`` row)."""
        observed = receipt.observed if isinstance(receipt.observed, dict) else {}
        window = observed.get("window")
        window_id: Any = None
        title = ""
        if isinstance(window, dict) and window.get("foreground"):
            window_id = window.get("window_id")
            title = str(window.get("title") or "")
        elif receipt.capability == "app.launch" and observed.get("window_id"):
            window_id = observed.get("window_id")
            title = str(observed.get("title") or "")
        if not window_id:
            return
        source = "operator_launch" if receipt.capability == "app.launch" else "operator_observed"
        try:
            focus_module.set_focus(
                db, FOCUS_KIND_WINDOW, str(window_id), label=title, source=source, now=now
            )
        except Exception:  # noqa: BLE001 - focus is evidence, never a dependency of the step
            logger.warning("operator_focus_write_failed", window_id=str(window_id))
            try:
                db.rollback()
            except Exception:  # noqa: BLE001 - best effort to keep the session usable
                pass

    # ------------------------------------------------------------- recording

    def _ledger(
        self, db: Session | None, task: OperatorTask, event_type: str, occurred_at: datetime
    ) -> None:
        if db is None:
            return
        # The SAME capability the receipt carries (app.operator.capabilities): a reader
        # filtering the ledger for everything ``operator.type`` did gets the activity
        # events and the receipt in one query, which was not true while this line built
        # its own name from the plan. The plan is detail about the action, and travels as
        # detail.
        capability = receipt_capability_for_plan(task.plan_name)
        try:
            ledger_service.record(
                db,
                ledger_service.ActivityEvent(
                    event_type=event_type,
                    subsystem=SUBSYSTEM_OPERATOR,
                    action=capability,
                    factual_summary=f"{capability} ({task.plan_name}) -> {task.status}",
                    occurred_at=occurred_at,
                    detail_json={
                        "task_id": str(task.id),
                        "step_count": len(task.steps),
                        "plan": task.plan_name,
                    },
                    source="live",
                    source_ref=f"operator_task:{task.id}:{event_type}",
                ),
            )
        except Exception:  # noqa: BLE001 - evidence, never a dependency of the action
            logger.warning(
                "operator_task_ledger_failed", task_id=str(task.id), event_type=event_type
            )

    def _receipt(
        self, db: Session | None, task: OperatorTask, *, session_id: str | None
    ) -> dict[str, Any]:
        execution = EXECUTION_EXECUTED if task.status == STATUS_SUCCEEDED else EXECUTION_FAILED
        terminal = TERMINAL_VERIFIED if task.status == STATUS_SUCCEEDED else TERMINAL_FAILED
        # "No device advertises this capability" is, from the owner's chair, the honest
        # answer "there is no operator authority on this machine" - the same class the
        # eye/research tools name ``capability_missing`` (docs/M19_DIGITAL_OPERATOR_SPEC.md
        # §4). The device layer's own vocabulary (``no_capable_device``) stays on the
        # per-step receipt trail below; only the task-level receipt's error_class is
        # translated, so a reader of the trail still sees which device call actually failed.
        error_class = task.error_class
        if error_class == "no_capable_device":
            error_class = "capability_missing"
        # The capability the owner COMMANDED, from the one declared table
        # (app.operator.capabilities). Until 2026-09-10 this was f"operator.{plan_name}",
        # which put a name into the ledger that existed nowhere in the source: the tool
        # registry said ``operator.type``, the receipt said ``operator.type_text``, and the
        # incident the Supervisor raised from the receipt named a capability the self-model
        # could not find. The plan is still recorded — as ``observed_after.server.plan``,
        # detail about the action rather than the identity of it.
        receipt = ActionReceipt(
            action_id=str(task.id),
            capability=receipt_capability_for_plan(task.plan_name),
            requested_state=task.status,
            execution_status=execution,
            terminal_status=terminal,
            observed_after={
                "server": {"status": task.status, "plan": task.plan_name},
                "local": dict(task.last_observed),
            },
            evidence_refs=[{"kind": "operator_task", "ref": str(task.id)}],
            error_class=error_class if task.status != STATUS_SUCCEEDED else None,
            speech="",
            started_at=task.started_at or datetime.now(UTC),
            completed_at=task.completed_at or datetime.now(UTC),
            session_id=session_id,
            observed_at=task.completed_at,
        )
        if db is not None:
            record_receipt(db, receipt, SUBSYSTEM_OPERATOR)
        return receipt.as_dict()


# ------------------------------------------------------------- process registry

_operator_service: OperatorService | None = None


def register_operator_service(service: OperatorService | None) -> None:
    global _operator_service
    _operator_service = service


def get_operator_service() -> OperatorService | None:
    return _operator_service


__all__ = [
    "OperatorService",
    "Plan",
    "get_operator_service",
    "register_operator_service",
]
