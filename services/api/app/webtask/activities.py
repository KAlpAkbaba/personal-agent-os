"""The browser task workflow's activities (ADR-0207 b): ONE round per activity.

Each activity opens its own database session, reaches the device through the same
command client research uses, runs one round through ``app.webtask.service`` and returns
the row's outcome. The round itself is synchronous (device commands poll), so it runs in
a worker thread and the heartbeat is handed back to the activity's loop - Temporal is
never touched from that thread (the operator mission's lesson, production 2026-09-19).

The planner in PR-B is the rule table and ``NoModelPlanner``: a round the rules cannot
answer is handed to the owner in words. The model planners are PR-C, behind the same
``TaskPlanner`` interface; nothing here changes when they arrive.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from temporalio import activity

from app.artifacts.runtime import build_artifact_context
from app.config import get_settings
from app.devices.commands import DeviceCommandClient
from app.webtask import service
from app.webtask.device_port import DeviceTaskBrowser
from app.webtask.loop import Ports
from app.webtask.planner import ChainPlanner, NoModelPlanner, RuleTablePlanner, TaskPlanner

#: Tests replace this to drive a real workflow over a fake browser and a scripted planner.
PortsFactory = Callable[[uuid.UUID, uuid.UUID | None, Callable[[], None]], Ports]
_ports_factory: PortsFactory | None = None


def set_ports_factory(factory: PortsFactory | None) -> None:
    global _ports_factory
    _ports_factory = factory


def _factory() -> Any:
    factory, _store = build_artifact_context(get_settings())
    return factory


def default_planner() -> TaskPlanner:
    return ChainPlanner([RuleTablePlanner(), NoModelPlanner()])


def _default_ports(
    task_id: uuid.UUID, device_id: uuid.UUID | None, heartbeat: Callable[[], None]
) -> Ports:
    if device_id is None:
        raise service.WebTaskError("no_device", "the task names no device")
    browser = DeviceTaskBrowser(
        DeviceCommandClient(_factory()),
        device_id=device_id,
        trace_id=f"webtask-{task_id}",
        heartbeat=heartbeat,
    )
    return Ports(browser=browser, planner=default_planner(), clock=time.monotonic)


def _heartbeat_from_the_round() -> Callable[..., None]:
    """A heartbeat the round may fire from the WORKER THREAD it runs on: the beat is
    handed back to the activity's event loop, in the activity's own context."""
    import asyncio
    import contextvars

    event_loop = asyncio.get_running_loop()
    context = contextvars.copy_context()

    def _beat(*_args: Any) -> None:
        if activity.in_activity():
            event_loop.call_soon_threadsafe(context.run, activity.heartbeat, "round")

    return _beat


@activity.defn(name="web_task_round")
async def web_task_round_activity(task_id: str) -> dict[str, Any]:
    import asyncio

    heartbeat = _heartbeat_from_the_round()

    def _run() -> dict[str, Any]:
        tid = uuid.UUID(task_id)
        with _factory()() as db:
            row = service.get_task(db, tid)
            build = _ports_factory or _default_ports
            try:
                ports = build(tid, row.device_id, heartbeat)
            except service.WebTaskError as exc:
                failed = service.fail_db(db, tid, reason=exc.reason, detail=str(exc))
                return service.outcome(failed) if failed is not None else {"status": "failed"}
            heartbeat()
            return service.run_round_db(db, tid, ports)

    return await asyncio.to_thread(_run)


@activity.defn(name="web_task_fail")
async def web_task_fail_activity(task_id: str, reason: str) -> dict[str, Any]:
    """The workflow's last word when a round activity itself failed."""
    import asyncio

    def _run() -> dict[str, Any]:
        with _factory()() as db:
            row = service.fail_db(
                db, uuid.UUID(task_id), reason="round_could_not_run", detail=reason
            )
            return service.outcome(row) if row is not None else {"status": "failed"}

    return await asyncio.to_thread(_run)


@activity.defn(name="web_task_cancel")
async def web_task_cancel_activity(task_id: str) -> dict[str, Any]:
    import asyncio

    def _run() -> dict[str, Any]:
        with _factory()() as db:
            return service.outcome(service.cancel_db(db, uuid.UUID(task_id)))

    return await asyncio.to_thread(_run)


@activity.defn(name="web_task_status")
async def web_task_status_activity(task_id: str) -> dict[str, Any]:
    """The row as it is. The owner's word is applied to the row by whoever SENT the
    signal, first - so that surface can answer truthfully at once - and only then is the
    workflow woken; it reads the row here rather than applying the word a second time."""
    import asyncio

    def _run() -> dict[str, Any]:
        with _factory()() as db:
            return service.outcome(service.get_task(db, uuid.UUID(task_id)))

    return await asyncio.to_thread(_run)


WEB_TASK_ACTIVITIES = (
    web_task_round_activity,
    web_task_fail_activity,
    web_task_cancel_activity,
    web_task_status_activity,
)

__all__ = [
    "WEB_TASK_ACTIVITIES",
    "default_planner",
    "set_ports_factory",
    "web_task_cancel_activity",
    "web_task_fail_activity",
    "web_task_round_activity",
    "web_task_status_activity",
]
