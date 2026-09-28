"""``BrowserTaskWorkflow`` (ADR-0207 b): one durable driver per browser task.

The workflow holds no owner-facing state. It runs ONE activity per round and, between
rounds, waits for what the owner may say. The task row is the truth; the activity writes
it. A workflow that dies resumes at the round the ROW is in, and that round begins by
observing again - an action is never replayed.

Signals carry no content. The owner's word (a confirmation, "devam", "hayır", cancel) is
applied to the row by the surface that heard it, which can then answer at once; the
signal only wakes this workflow, which reads the row.

* ``woken``  - the owner said something that may let the task run again;
* ``cancel`` - stop, wherever the task is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.webtask.activities import (
        web_task_cancel_activity,
        web_task_fail_activity,
        web_task_round_activity,
        web_task_status_activity,
    )

#: One round: an observation, a plan, one device command, an observation. The device
#: caps a browser command at 120 s; a round holds at most three of them.
ROUND_ACTIVITY_TIMEOUT_S = 420
#: The whole task, every wait for the owner included.
TASK_WALL_CLOCK_S = 3 * 60 * 60
#: How long a task parked for the owner waits before it is cancelled.
OWNER_WAIT_S = 2 * 60 * 60
#: A hard ceiling on activities, above the loop's own round budget: the workflow must end
#: even if the row somehow never does.
MAX_ACTIVITIES = 120

_TERMINAL = ("done", "failed", "cancelled")


@dataclass
class BrowserTaskRequest:
    task_id: str


@workflow.defn
class BrowserTaskWorkflow:
    def __init__(self) -> None:
        self._woken = 0
        self._handled = 0
        self._cancelled = False
        self._status: dict[str, Any] = {"status": "running", "round_index": 0}

    @workflow.signal
    def woken(self) -> None:
        self._woken += 1

    @workflow.signal
    def cancel(self) -> None:
        self._cancelled = True

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {**self._status, "cancelled": self._cancelled}

    @workflow.run
    async def run(self, request: BrowserTaskRequest) -> dict[str, Any]:
        deadline = workflow.now() + timedelta(seconds=TASK_WALL_CLOCK_S)
        activities = 0
        while workflow.now() < deadline and activities < MAX_ACTIVITIES:
            if self._cancelled:
                self._status = await self._cancel(request.task_id)
                break
            activities += 1
            try:
                outcome = await workflow.execute_activity(
                    web_task_round_activity,
                    args=[request.task_id],
                    start_to_close_timeout=timedelta(seconds=ROUND_ACTIVITY_TIMEOUT_S),
                    heartbeat_timeout=timedelta(seconds=150),
                    # A round is NOT retried: it may have acted. The next round observes.
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            except ActivityError as exc:
                self._status = await self._fail(request.task_id, _reason_of(exc))
                break
            self._status = dict(outcome)
            status = str(outcome.get("status"))
            if status in _TERMINAL:
                break
            # Parked for the owner: wait here, and stay here for as long as the ROW says
            # so. A word that was refused (a "devam" on a confirmation, a confirmation
            # before its read-back) wakes this workflow and changes nothing - no round
            # is run for a task that is waiting. It never assumes.
            while status == "waiting_owner" and not self._cancelled and workflow.now() < deadline:
                seen = self._handled
                try:
                    await workflow.wait_condition(
                        lambda seen=seen: self._woken > seen or self._cancelled,
                        timeout=timedelta(seconds=OWNER_WAIT_S),
                    )
                except TimeoutError:
                    self._cancelled = True
                    break
                self._handled = self._woken
                if self._cancelled:
                    break
                self._status = dict(
                    await workflow.execute_activity(
                        web_task_status_activity,
                        args=[request.task_id],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                )
                status = str(self._status.get("status"))
            if status in _TERMINAL:
                break
        else:
            self._status = await self._cancel(request.task_id)
        return {"task_id": request.task_id, **self._status}

    async def _fail(self, task_id: str, reason: str) -> dict[str, Any]:
        result = await workflow.execute_activity(
            web_task_fail_activity,
            args=[task_id, reason],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        return dict(result)

    async def _cancel(self, task_id: str) -> dict[str, Any]:
        result = await workflow.execute_activity(
            web_task_cancel_activity,
            args=[task_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        return dict(result)


def _reason_of(exc: ActivityError) -> str:
    cause = exc.cause
    if isinstance(cause, ApplicationError):
        return f"{cause.type or 'error'}: {cause.message}"
    return str(cause or exc)


__all__ = ["BrowserTaskRequest", "BrowserTaskWorkflow", "ROUND_ACTIVITY_TIMEOUT_S"]
