"""Temporal worker entrypoint.

Run locally (compose stack must be up):
    uv run python -m app.worker
"""

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from app.config import get_settings
from app.logging import configure_logging, get_logger
from app.workflows import HealthPingWorkflow, ping_activity

logger = get_logger("app.worker")


async def run_worker() -> None:
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[HealthPingWorkflow],
        activities=[ping_activity],
    )
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
