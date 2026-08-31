"""Temporal workflow execution + worker-restart durability (M0 gate).

The durability test runs the worker as real subprocesses (`python -m app.worker`),
kills the first worker while the workflow sits on a durable timer, starts a
fresh worker process and asserts the workflow still completes.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from app.config import Settings
from app.workflows import HealthPingWorkflow, ping_activity

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]


async def connect(settings: Settings) -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


async def test_health_ping_workflow_executes(settings: Settings) -> None:
    client = await connect(settings)
    task_queue = f"pagentos-test-{uuid.uuid4().hex[:8]}"
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[HealthPingWorkflow],
        activities=[ping_activity],
    ):
        result = await client.execute_workflow(
            HealthPingWorkflow.run,
            args=["m0", 0.5],
            id=f"health-ping-{uuid.uuid4().hex[:8]}",
            task_queue=task_queue,
        )
    assert result == "pong:m0"


def spawn_worker(task_queue: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["PAGENTOS_TEMPORAL_TASK_QUEUE"] = task_queue
    return subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=str(API_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def test_workflow_survives_worker_restart(settings: Settings) -> None:
    client = await connect(settings)
    task_queue = f"pagentos-restart-{uuid.uuid4().hex[:8]}"
    workflow_id = f"restart-durability-{uuid.uuid4().hex[:8]}"

    worker1 = spawn_worker(task_queue)
    try:
        # Timer long enough that the first worker dies mid-timer.
        handle = await client.start_workflow(
            HealthPingWorkflow.run,
            args=["restart", 8.0],
            id=workflow_id,
            task_queue=task_queue,
        )
        # Give worker1 time to pick up the first workflow task and start the timer.
        await asyncio.sleep(3.0)

        worker1.kill()
        worker1.wait(timeout=15)

        worker2 = spawn_worker(task_queue)
        try:
            result = await asyncio.wait_for(handle.result(), timeout=60)
        finally:
            worker2.kill()
            worker2.wait(timeout=15)
    finally:
        if worker1.poll() is None:
            worker1.kill()
            worker1.wait(timeout=15)

    assert result == "pong:restart"
