"""The mission workflow's activities (B39): each one opens its own session, reaches the
device through the SAME broker port the executive's activities use (never a second
desktop-control path), runs one step's closed loop through ``mission_service``, and
returns the row's outcome. The vision provider is built from settings, as ``create_app``
builds it - a worker with no key has no fifth rung, and the loop says so.
"""

from __future__ import annotations

import uuid
from typing import Any

from temporalio import activity

from app.artifacts.runtime import build_artifact_context
from app.config import get_settings
from app.executive.activities import get_device_action
from app.operator import mission_service
from app.operator.mission import MissionPorts
from app.operator.vision import build_vision_provider


def _ports() -> MissionPorts:
    return MissionPorts(device=get_device_action(), vision=build_vision_provider(get_settings()))


def _factory():
    factory, _store = build_artifact_context(get_settings())
    return factory


@activity.defn(name="operator_mission_step")
async def mission_step_activity(mission_id: str) -> dict[str, Any]:
    import asyncio

    def _run() -> dict[str, Any]:
        with _factory()() as db:
            return mission_service.run_step_db(
                db,
                uuid.UUID(mission_id),
                _ports(),
                on_task=lambda *_a: activity.heartbeat("round") if activity.in_activity() else None,
            )

    return await asyncio.to_thread(_run)


@activity.defn(name="operator_mission_mark")
async def mission_mark_activity(mission_id: str, mark: str) -> dict[str, Any]:
    """The owner's word, written to the row between steps: ``approved`` / ``resumed``
    re-open the loop, ``pause`` asks the next round to stop."""
    import asyncio

    def _run() -> dict[str, Any]:
        mid = uuid.UUID(mission_id)
        with _factory()() as db:
            if mark == "approved":
                row = mission_service.approve_db(db, mid)
            elif mark == "resumed":
                row = mission_service.resume_db(db, mid)
            elif mark == "pause":
                row = mission_service.pause_db(db, mid)
            else:
                row = mission_service.get_mission(db, mid)
            return {"status": row.status, "current_step": row.current_step}

    return await asyncio.to_thread(_run)


@activity.defn(name="operator_mission_cancel")
async def mission_cancel_activity(mission_id: str) -> dict[str, Any]:
    import asyncio

    def _run() -> dict[str, Any]:
        mid = uuid.UUID(mission_id)
        with _factory()() as db:
            try:
                row = mission_service.cancel_db(db, mid)
            except mission_service.MissionServiceError:
                row = mission_service.get_mission(db, mid)
            return {"status": row.status, "current_step": row.current_step}

    return await asyncio.to_thread(_run)


MISSION_ACTIVITIES = (mission_step_activity, mission_mark_activity, mission_cancel_activity)

__all__ = [
    "MISSION_ACTIVITIES",
    "mission_cancel_activity",
    "mission_mark_activity",
    "mission_step_activity",
]
