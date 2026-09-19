"""A mission through the REAL step activity under a REAL Temporal worker (B39).

The unit suites drive ``run_step_db`` directly and the workflow with fake activities; the
one layer neither covers is the activity as the worker runs it - its thread, its heartbeat,
its database session. That layer is where production failed on 2026-09-19 09:39:58: every
mission's first round died with ``RuntimeError: no running event loop`` (a heartbeat issued
from the round's worker thread), and no suite had noticed. This test runs the activities
the worker registers, on the dev stack's Temporal, with only the DEVICE faked.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from app.artifacts.runtime import build_artifact_context
from app.config import Settings
from app.operator import mission_activities, mission_service
from app.operator.mission import MISSION_SUCCEEDED, MissionPorts
from app.operator.mission_activities import MISSION_ACTIVITIES
from app.operator.mission_workflow import MissionRequest, OperatorMissionWorkflow
from tests.alarms_support import FakeDeviceAction, happy_operator_device_results

pytestmark = pytest.mark.integration


async def test_a_mission_runs_to_the_end_under_a_real_worker(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = FakeDeviceAction(results=happy_operator_device_results())
    monkeypatch.setattr(mission_activities, "_ports", lambda: MissionPorts(device=device))
    factory, _store = build_artifact_context(settings)
    with factory() as db:
        row = mission_service.start_mission_db(db, text="Not Defteri'ni aç ve merhaba yaz")
        mission_id = row.id

    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    task_queue = f"pagentos-mission-{uuid.uuid4().hex[:8]}"
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[OperatorMissionWorkflow],
        activities=list(MISSION_ACTIVITIES),
    ):
        result = await asyncio.wait_for(
            client.execute_workflow(
                OperatorMissionWorkflow.run,
                MissionRequest(mission_id=str(mission_id)),
                id=mission_service.workflow_id_for(mission_id),
                task_queue=task_queue,
            ),
            timeout=120,
        )

    assert result["status"] == MISSION_SUCCEEDED, result
    with factory() as db:
        finished = mission_service.get_mission(db, mission_id)
    assert finished.status == MISSION_SUCCEEDED, finished.mission_json
    assert finished.started_at is not None and finished.completed_at is not None
    called = device.capabilities_called()
    # The second step typed; the first found Notepad already in front and activated it.
    assert "keyboard.type" in called, called
