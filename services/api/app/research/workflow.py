"""Durable research workflow (Temporal).

The research job is a durable workflow so a worker/broker restart does not lose
task intent (M3 acceptance: "research task becomes durable" / artifact persists
after restart). Intent lives in Temporal, not in the worker process: if the
worker dies mid-activity Temporal retries on a fresh worker; an optional durable
timer (`pause_seconds`) lets the restart test kill the worker during a gap and
prove resumption.

The workflow returns only compact metadata + the executive summary — never the
full report body. That is how a task reaches READY without auto-reading the
whole artifact (Executive layer). The body is fetched separately via
`GET /v1/artifacts/{id}/canonical`.
"""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.artifacts.renderers import DEFAULT_RENDER_FORMATS
    from app.research.activities import (
        compose_activity,
        gather_sources_activity,
        plan_activity,
        render_activity,
    )

DEFAULT_PROVIDER = "deterministic"
DEFAULT_SOURCE_LIMIT = 6

_ACTIVITY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=5,
)
_SHORT = timedelta(seconds=30)
_LONG = timedelta(seconds=120)


@dataclass
class ResearchRequest:
    task_id: str
    topic: str
    provider: str = DEFAULT_PROVIDER
    source_limit: int = DEFAULT_SOURCE_LIMIT
    pause_seconds: float = 0.0
    render_formats: list[str] | None = None


@workflow.defn
class ResearchWorkflow:
    @workflow.run
    async def run(self, request: ResearchRequest) -> dict:
        formats = request.render_formats or list(DEFAULT_RENDER_FORMATS)

        plan = await workflow.execute_activity(
            plan_activity,
            args=[request.task_id, request.topic, request.provider],
            start_to_close_timeout=_SHORT,
            retry_policy=_ACTIVITY_RETRY,
        )

        # Optional durable gap for the worker-restart durability test.
        if request.pause_seconds > 0:
            await workflow.sleep(timedelta(seconds=request.pause_seconds))

        scored = await workflow.execute_activity(
            gather_sources_activity,
            args=[request.task_id, request.topic, request.provider, request.source_limit],
            start_to_close_timeout=_SHORT,
            retry_policy=_ACTIVITY_RETRY,
        )

        composed = await workflow.execute_activity(
            compose_activity,
            args=[request.task_id, request.topic, scored],
            start_to_close_timeout=_LONG,
            retry_policy=_ACTIVITY_RETRY,
        )

        rendered = await workflow.execute_activity(
            render_activity,
            args=[request.task_id, composed["artifact_id"], composed["version"], formats],
            start_to_close_timeout=_LONG,
            retry_policy=_ACTIVITY_RETRY,
        )

        return {
            "task_id": request.task_id,
            "artifact_id": composed["artifact_id"],
            "version": composed["version"],
            "state": "READY",
            "executive_summary": composed["executive_summary"],
            "render_formats": [r["format"] for r in rendered["renders"]],
            "plan_steps": plan["steps"],
        }
