"""``OperatorMissionWorkflow`` (B39 req 128, 129): one durable driver per mission, the
M26 discipline applied to the operator - the workflow holds no owner-facing state, runs
ONE activity per step, and waits between activities for what the owner may say
(approve, pause, resume, cancel). The mission row is the truth; the activity writes it.

Why an activity per step and not per round: a round is one device plan (seconds), a step
is a bounded loop of rounds (under a minute); a pause or a cancel is honoured between
rounds by the loop's own flag (the activity re-reads the row) and between steps by this
workflow, so the owner never waits longer than one device plan for their word to land.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.operator.mission_activities import (
        mission_cancel_activity,
        mission_fail_activity,
        mission_mark_activity,
        mission_step_activity,
    )

#: A step's closed loop is bounded by the mission engine (6 rounds x 30 s device cap).
STEP_ACTIVITY_TIMEOUT_S = 240
#: The whole mission, including every wait for the owner.
MISSION_WALL_CLOCK_S = 6 * 60 * 60
#: How long a parked mission (preview, pause, escalation) waits for the owner.
OWNER_WAIT_S = 2 * 60 * 60


@dataclass
class MissionRequest:
    mission_id: str


@workflow.defn
class OperatorMissionWorkflow:
    def __init__(self) -> None:
        self._approved = False
        self._paused = False
        self._resumed = False
        self._cancelled = False
        self._status: dict[str, Any] = {"status": "planned", "current_step": 0}

    @workflow.signal
    def approve(self) -> None:
        self._approved = True

    @workflow.signal
    def pause(self) -> None:
        self._paused = True

    @workflow.signal
    def resume(self) -> None:
        self._paused = False
        self._resumed = True

    @workflow.signal
    def cancel(self) -> None:
        self._cancelled = True

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {**self._status, "paused": self._paused, "cancelled": self._cancelled}

    @workflow.run
    async def run(self, request: MissionRequest) -> dict[str, Any]:
        deadline = workflow.now() + timedelta(seconds=MISSION_WALL_CLOCK_S)
        while workflow.now() < deadline:
            if self._cancelled:
                self._status = await self._cancel(request.mission_id)
                break
            try:
                outcome = await workflow.execute_activity(
                    mission_step_activity,
                    args=[request.mission_id],
                    start_to_close_timeout=timedelta(seconds=STEP_ACTIVITY_TIMEOUT_S),
                    heartbeat_timeout=timedelta(seconds=90),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            except ActivityError as exc:
                # Production 2026-09-19 09:39:58: the step activity raised (a heartbeat
                # issued off its loop), this workflow failed with it, and the ROW never
                # learned - it stayed "planned" with nothing running it, refused every later
                # mission as in flight for six minutes, and the owner was told the mission
                # was waiting for THEM. A step that cannot run is a mission that FAILED:
                # written on the row, with its reason, and told.
                self._status = await self._fail(request.mission_id, _reason_of(exc))
                break
            self._status = dict(outcome)
            status = str(outcome.get("status"))
            if status in ("succeeded", "failed", "cancelled"):
                break
            if status == "awaiting_approval":
                self._approved = False
                await self._wait(lambda: self._approved or self._cancelled)
                if self._cancelled:
                    continue
                await workflow.execute_activity(
                    mission_mark_activity,
                    args=[request.mission_id, "approved"],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                continue
            if status == "paused":
                self._resumed = False
                await self._wait(lambda: self._resumed or self._cancelled)
                if self._cancelled:
                    continue
                await workflow.execute_activity(
                    mission_mark_activity,
                    args=[request.mission_id, "resumed"],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                continue
            if self._paused:
                await workflow.execute_activity(
                    mission_mark_activity,
                    args=[request.mission_id, "pause"],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
        else:
            self._status = await self._cancel(request.mission_id)
        return {"mission_id": request.mission_id, **self._status}

    async def _wait(self, predicate: Any) -> None:
        try:
            await workflow.wait_condition(predicate, timeout=timedelta(seconds=OWNER_WAIT_S))
        except TimeoutError:
            self._cancelled = True

    async def _fail(self, mission_id: str, reason: str) -> dict[str, Any]:
        result = await workflow.execute_activity(
            mission_fail_activity,
            args=[mission_id, reason],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        return dict(result)

    async def _cancel(self, mission_id: str) -> dict[str, Any]:
        result = await workflow.execute_activity(
            mission_cancel_activity,
            args=[mission_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        return dict(result)


def _reason_of(exc: ActivityError) -> str:
    """The failed activity's own error, as the row will carry it ("RuntimeError: no
    running event loop"), never the wrapper's generic "Activity task failed"."""
    cause = exc.cause
    if isinstance(cause, ApplicationError):
        return f"{cause.type or 'error'}: {cause.message}"
    return str(cause or exc)


__all__ = ["MissionRequest", "OperatorMissionWorkflow", "STEP_ACTIVITY_TIMEOUT_S"]
