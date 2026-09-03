"""BrowserResearchWorkflow (Temporal) — the durable M13 pipeline (spec §5).

Workflow id: ``research-browser-{task_id}``. Every activity is idempotent, so
a Cloud Core restart resumes the workflow from Temporal history and a
DeviceService/Chrome restart is absorbed by the fetch activity's retry +
next-attempt idempotency key (``app.research.browser_gateway.fetch_idempotency_key``).

The workflow itself holds no DB/device state — every fact it needs to resume
correctly (the plan, the selected device, discovered candidates, fetched
evidence, the synthesized report) lives in Postgres, written by the
activities. The workflow only sequences activities and returns the final
summary — never the full report body inline beyond what
``GET /v1/research/{task_id}`` already returns (the REST route reads the
persisted rows directly; this return value is what the caller/route uses to
know the run finished and where to look).
"""

from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.research.browser_activities import (
        close_session_activity,
        discover_activity,
        fail_run_activity,
        fetch_activity,
        fetch_targets_activity,
        persist_artifact_activity,
        plan_activity,
        rank_activity,
        remember_activity,
        select_device_activity,
        synthesize_activity,
    )
    from app.research.models import STAGE_FAILED, STAGE_READY

DEFAULT_MAX_SOURCES = 12
DEFAULT_SYNTHESIS = "auto"

_SHORT = timedelta(seconds=30)
_MEDIUM = timedelta(seconds=90)
_FETCH_TIMEOUT = timedelta(seconds=120)  # browser.* commands may run up to 120s

_STANDARD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=20),
    maximum_attempts=3,
)
_FETCH_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=4,
)
_NO_RETRY = RetryPolicy(maximum_attempts=1)


@dataclass
class BrowserResearchRequest:
    task_id: str
    topic: str
    target_device: str | None = None
    recency_days: int | None = None
    max_sources: int = DEFAULT_MAX_SOURCES
    synthesis: str = DEFAULT_SYNTHESIS
    discovery_source_classes: list[str] = field(
        default_factory=lambda: ["official", "technical", "academic", "news", "community"]
    )


@workflow.defn
class BrowserResearchWorkflow:
    @workflow.run
    async def run(self, request: BrowserResearchRequest) -> dict:
        plan = await workflow.execute_activity(
            plan_activity,
            args=[request.task_id, request.topic, request.recency_days, request.max_sources],
            start_to_close_timeout=_SHORT,
            retry_policy=_STANDARD_RETRY,
        )

        try:
            device = await workflow.execute_activity(
                select_device_activity,
                args=[request.task_id, request.target_device],
                start_to_close_timeout=_SHORT,
                retry_policy=_NO_RETRY,
            )
        except ActivityError as exc:
            return self._failed(request.task_id, plan, self._error_detail(exc), "no_capable_device")

        device_id = device["device_id"]

        for source_class in plan["source_classes"]:
            for i, query_text in enumerate(plan["queries"]):
                query_id = f"{source_class}:{i}"
                try:
                    await workflow.execute_activity(
                        discover_activity,
                        args=[
                            request.task_id, device_id, query_id, query_text, source_class,
                            plan["recency"]["start"],
                        ],
                        start_to_close_timeout=_MEDIUM,
                        retry_policy=_STANDARD_RETRY,
                    )
                except ActivityError:
                    continue  # one query/class failing must not fail the whole run

        targets = await workflow.execute_activity(
            fetch_targets_activity,
            args=[request.task_id, request.max_sources],
            start_to_close_timeout=_SHORT,
            retry_policy=_STANDARD_RETRY,
        )

        for target in targets:
            try:
                await workflow.execute_activity(
                    fetch_activity,
                    args=[
                        request.task_id, device_id, target["url"], target["query"],
                        target["source_class"],
                    ],
                    start_to_close_timeout=_FETCH_TIMEOUT,
                    heartbeat_timeout=timedelta(seconds=20),
                    retry_policy=_FETCH_RETRY,
                )
            except ActivityError:
                continue  # recorded as a fetch failure; one bad URL never fails the run

        try:
            await workflow.execute_activity(
                rank_activity,
                args=[
                    request.task_id,
                    plan["topic"],
                    plan["recency"]["start"],
                    plan["recency"]["end"],
                ],
                start_to_close_timeout=_SHORT,
                retry_policy=_STANDARD_RETRY,
            )

            report = await workflow.execute_activity(
                synthesize_activity,
                args=[request.task_id, plan["topic"], plan["recency"], request.synthesis],
                start_to_close_timeout=_MEDIUM,
                retry_policy=_STANDARD_RETRY,
            )

            persisted = await workflow.execute_activity(
                persist_artifact_activity,
                args=[request.task_id, plan["topic"]],
                start_to_close_timeout=_MEDIUM,
                retry_policy=_STANDARD_RETRY,
            )

            memory_id = await workflow.execute_activity(
                remember_activity,
                args=[request.task_id, plan["topic"]],
                start_to_close_timeout=_SHORT,
                retry_policy=_STANDARD_RETRY,
            )

        except ActivityError as exc:
            # The run must end in a visible terminal state even when an activity
            # exhausts its retries (seen live: a synthesis failure left the status
            # endpoint on 'ranking' until the harness gave up).
            detail = self._error_detail(exc)
            error_class = self._error_class(exc)
            try:
                await workflow.execute_activity(
                    fail_run_activity,
                    args=[request.task_id, error_class, detail],
                    start_to_close_timeout=_SHORT,
                    retry_policy=_STANDARD_RETRY,
                )
            except ActivityError:
                pass
            try:
                await workflow.execute_activity(
                    close_session_activity,
                    args=[request.task_id, device_id],
                    start_to_close_timeout=_SHORT,
                    retry_policy=_NO_RETRY,
                )
            except ActivityError:
                pass
            return self._failed(request.task_id, plan, detail, error_class)

        try:
            await workflow.execute_activity(
                close_session_activity,
                args=[request.task_id, device_id],
                start_to_close_timeout=_SHORT,
                retry_policy=_NO_RETRY,
            )
        except ActivityError:
            pass  # best-effort (spec §5)

        return {
            "task_id": request.task_id,
            "stage": STAGE_READY,
            "device": device,
            "artifact_id": persisted["artifact_id"],
            "memory_id": memory_id,
            "synthesis_provider": report.get("synthesis_provider"),
            "findings_count": len(report.get("findings", [])),
            "sources_count": len(report.get("sources", [])),
        }

    def _error_detail(self, exc: ActivityError) -> str:
        cause = exc.cause
        if isinstance(cause, ApplicationError):
            return str(cause.message)
        return str(exc)

    def _error_class(self, exc: ActivityError) -> str:
        cause = exc.cause
        if isinstance(cause, ApplicationError) and cause.type:
            return str(cause.type)
        return "research_failed"

    def _failed(self, task_id: str, plan: dict, detail: str, error_class: str) -> dict:
        return {
            "task_id": task_id,
            "stage": STAGE_FAILED,
            "device": None,
            "error": {"error_class": error_class, "detail": detail},
            "artifact_id": None,
            "memory_id": None,
        }


__all__ = ["BrowserResearchRequest", "BrowserResearchWorkflow"]
