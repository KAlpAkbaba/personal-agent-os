"""Temporal worker entrypoint.

Run locally (compose stack must be up):
    uv run python -m app.worker

Registers the M0 health workflow and the M3 research pipeline. The research
activities are synchronous (blocking DB + object-store IO), so the worker is
given a ThreadPoolExecutor as its activity executor.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from app.config import Settings, get_settings
from app.executive.activities import EXECUTIVE_ACTIVITIES
from app.executive.workflow import ExecutiveWorkflow
from app.logging import configure_logging, get_logger
from app.operator.mission_activities import MISSION_ACTIVITIES
from app.operator.mission_workflow import OperatorMissionWorkflow
from app.research.activities import (
    compose_activity,
    gather_sources_activity,
    plan_activity,
    render_activity,
)
from app.research.browser_activities import BROWSER_RESEARCH_ACTIVITIES
from app.research.browser_workflow import BrowserResearchWorkflow
from app.research.workflow import ResearchWorkflow
from app.workflows import HealthPingWorkflow, ping_activity

logger = get_logger("app.worker")

RESEARCH_ACTIVITIES = [
    plan_activity,
    gather_sources_activity,
    compose_activity,
    render_activity,
]


def build_worker(
    client: Client, task_queue: str, *, activity_executor: ThreadPoolExecutor | None = None
) -> Worker:
    """Construct a Worker with all pagentos workflows/activities registered.

    Shared by the CLI entrypoint, the API's embedded-worker lifespan
    (ADR-0050 §9) and the integration tests so registration stays in one
    place.
    """
    executor = activity_executor or ThreadPoolExecutor(max_workers=8)
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[
            HealthPingWorkflow,
            ResearchWorkflow,
            BrowserResearchWorkflow,
            # M26 (docs/M26_EXECUTIVE_AUTONOMY_SPEC.md §3): registered on the SAME task
            # queue as BrowserResearchWorkflow — a research.run step starts that
            # workflow as its own, independent execution (app.executive.activities.
            # _run_research), never a child needing a separate worker/queue.
            ExecutiveWorkflow,
            # B39 (req 128): the operator mission's durable driver, same queue.
            OperatorMissionWorkflow,
        ],
        activities=[
            ping_activity,
            *RESEARCH_ACTIVITIES,
            *BROWSER_RESEARCH_ACTIVITIES,
            *EXECUTIVE_ACTIVITIES,
            *MISSION_ACTIVITIES,
        ],
        activity_executor=executor,
    )


async def run_worker(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    with ThreadPoolExecutor(max_workers=8) as executor:
        worker = build_worker(client, settings.temporal_task_queue, activity_executor=executor)
        logger.info(
            "worker_started",
            temporal_address=settings.temporal_address,
            task_queue=settings.temporal_task_queue,
        )
        await worker.run()


def main() -> None:
    configure_logging()
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("worker_stopped")


if __name__ == "__main__":
    main()
