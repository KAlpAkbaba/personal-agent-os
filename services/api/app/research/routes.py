"""M13 research REST surface (M13_RESEARCH_SPEC.md §4). Owner-gated like every
other surface (artifacts, memory, devices).

Device selection happens HERE, synchronously, before the workflow starts —
not as the workflow's first activity — so a request that names an
unreachable device (or when nothing is online at all) fails fast with
``409 no_capable_device`` and a Turkish ``detail`` instead of starting a
workflow that is certain to fail later. The selected device is persisted on
the run row immediately, so the workflow's own ``select_device`` activity
(idempotent, spec §5) reuses it rather than re-deciding.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.artifacts import service as artifact_service
from app.artifacts.models import TASK_STATUS_FAILED_TERMINAL, Task
from app.artifacts.runtime import ArtifactRuntime
from app.broker.runtime import BrokerRuntime
from app.devices import service as devices_service
from app.devices.selection import NoCapableDeviceError, select_device
from app.identity.dependencies import require_owner_session
from app.logging import get_logger, trace_id_var
from app.research import runs_service
from app.research.browser_workflow import BrowserResearchRequest, BrowserResearchWorkflow
from app.research.models import STAGE_CANCELLED, STAGE_FAILED, STAGE_PLANNED, ResearchRunRow

logger = get_logger("app.research.routes")

router = APIRouter(prefix="/v1/research", dependencies=[Depends(require_owner_session)])

MAX_SOURCES_CEILING = 30


def _broker(request: Request) -> BrokerRuntime:
    return request.app.state.broker


def _artifacts(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def _temporal_client(request: Request) -> Client:
    settings = request.app.state.artifacts.settings
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


#: spec §5a: interactive_wait_s bounds (60s = one browser.wait slice; 1800s = 30 minutes).
MIN_INTERACTIVE_WAIT_S = 60
MAX_INTERACTIVE_WAIT_S = 1800
DEFAULT_INTERACTIVE_WAIT_S = 600


class CreateResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(min_length=1, max_length=4000)
    target_device: str | None = Field(default=None, max_length=256)
    recency_days: int | None = Field(default=None, ge=1, le=365)
    max_sources: int = Field(default=12, ge=1, le=MAX_SOURCES_CEILING)
    synthesis: str = Field(default="auto", max_length=32)
    #: spec §5a: owner-handoff mode. The web page sends `true`; the CLI
    #: runner (scripts/research_smoke.py) has `--interactive`. False
    #: (default) never waits on a Google interstitial.
    interactive: bool = Field(default=False)
    #: owner-facing spelling of the same choice (contract §3a search modes):
    #: "interactive" = Google -> owner handoff if needed -> fallback only afterwards;
    #: "unattended" = Google -> deterministic fallback if blocked. Wins over `interactive`.
    mode: str | None = Field(default=None, pattern="^(interactive|unattended)$")
    #: interactive runs only: "fallback" (default) or "fail" when the owner does not
    #: complete Google's page within interactive_wait_s (spec §5a).
    on_verification_timeout: str = Field(default="fallback", pattern="^(fallback|fail)$")
    interactive_wait_s: int = Field(
        default=DEFAULT_INTERACTIVE_WAIT_S,
        ge=MIN_INTERACTIVE_WAIT_S,
        le=MAX_INTERACTIVE_WAIT_S,
    )


def _effective_interactive(body: CreateResearchRequest) -> bool:
    if body.mode is not None:
        return body.mode == "interactive"
    return body.interactive


CreateResearchRequest.effective_interactive = property(_effective_interactive)  # type: ignore[attr-defined]


def _device_summary(view: Any) -> dict[str, Any]:
    return {"device_id": str(view.id), "name": view.name}


@router.post("", status_code=202)
async def create_research(request: Request, body: CreateResearchRequest) -> JSONResponse:
    broker = _broker(request)
    artifacts = _artifacts(request)
    trace_id = trace_id_var.get()

    def create_task_and_select() -> tuple[Task, dict[str, Any] | None, str | None]:
        with artifacts.session() as session:
            task = artifact_service.create_task(
                session,
                intent=body.input,
                trace_id=trace_id,
            )
            views = devices_service.list_device_views(session, broker)
            try:
                result = select_device(
                    views, capability="browser.chrome", target=body.target_device
                )
            except NoCapableDeviceError as exc:
                artifact_service.transition_task(
                    session,
                    task.id,
                    TASK_STATUS_FAILED_TERMINAL,
                    error_class="no_capable_device",
                    error_message=exc.detail_tr,
                )
                runs_service.update_run(
                    session,
                    task.id,
                    stage=STAGE_FAILED,
                    error=exc.detail_tr,
                    event={"stage": STAGE_FAILED, "detail": exc.detail_tr},
                )
                return task, None, exc.detail_tr
            runs_service.update_run(
                session,
                task.id,
                stage=STAGE_PLANNED,
                device_id=result.device.id,
                event={"stage": STAGE_PLANNED, "detail": f"selected {result.device.name}"},
            )
            return task, _device_summary(result.device), None

    task, device, error_detail = await asyncio.to_thread(create_task_and_select)
    if error_detail is not None:
        logger.info("research_no_capable_device", task_id=str(task.id), detail=error_detail)
        raise HTTPException(
            status_code=409,
            detail={
                "error_class": "no_capable_device",
                "detail": error_detail,
                "task_id": str(task.id),
            },
        )

    workflow_id = f"research-browser-{task.id}"
    client = await _temporal_client(request)
    try:
        await client.start_workflow(
            BrowserResearchWorkflow.run,
            BrowserResearchRequest(
                task_id=str(task.id),
                topic=body.input,
                target_device=body.target_device,
                recency_days=body.recency_days,
                max_sources=body.max_sources,
                synthesis=body.synthesis,
                interactive=body.effective_interactive,
                interactive_wait_s=body.interactive_wait_s,
                on_verification_timeout=body.on_verification_timeout,
            ),
            id=workflow_id,
            task_queue=artifacts.settings.temporal_task_queue,
        )
    except WorkflowAlreadyStartedError:
        pass  # idempotent retry of POST

    def persist_wf() -> None:
        with artifacts.session() as session:
            artifact_service.set_task_workflow_id(session, task.id, workflow_id)

    await asyncio.to_thread(persist_wf)
    logger.info("research_created", task_id=str(task.id), workflow_id=workflow_id)
    return JSONResponse(
        status_code=202,
        content={
            "task_id": str(task.id),
            "workflow_id": workflow_id,
            "status": "planned",
            "device": device,
        },
    )


@router.get("")
async def list_research(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> list[dict[str, Any]]:
        with artifacts.session() as session:
            tasks = artifact_service.list_tasks(session, limit=500)
            out = []
            for task in tasks:
                run = runs_service.get_run(session, task.id)
                if run is None:
                    continue  # not a research task
                report_row = runs_service.get_report(session, task.id)
                out.append(
                    {
                        "task_id": str(task.id),
                        "topic": task.intent,
                        "status": task.status,
                        "stage": run.stage,
                        "device": str(run.device_id) if run.device_id else None,
                        "created_at": _iso(task.created_at),
                        "ready_at": _iso(task.ready_at),
                        "artifact_id": str(report_row.artifact_id)
                        if report_row and report_row.artifact_id
                        else None,
                    }
                )
            return out

    tasks = await asyncio.to_thread(load)
    return {"tasks": tasks}


def _load_detail(artifacts: ArtifactRuntime, task_id: uuid.UUID) -> dict[str, Any] | None:
    with artifacts.session() as session:
        task = artifact_service.get_task(session, task_id)
        if task is None:
            return None
        run: ResearchRunRow | None = runs_service.get_run(session, task_id)
        report_row = runs_service.get_report(session, task_id)
        error = None
        if task.error_class:
            error = {"error_class": task.error_class, "detail": task.error_message}
        elif run is not None and run.error:
            error = {"error_class": "research_failed", "detail": run.error}
        return {
            "task_id": str(task.id),
            "topic": task.intent,
            "status": task.status,
            "stage": run.stage if run else None,
            "progress": run.progress_json if run else {},
            "device": str(run.device_id) if run and run.device_id else None,
            "plan": run.plan_json if run else None,
            "report": report_row.report_json if report_row else None,
            "artifact_id": str(report_row.artifact_id)
            if report_row and report_row.artifact_id
            else None,
            "memory_id": str(report_row.memory_id) if report_row and report_row.memory_id else None,
            "error": error,
            "events": (run.events_json or [])[-50:] if run else [],
        }


@router.get("/{task_id}")
async def get_research(request: Request, task_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)
    payload = await asyncio.to_thread(_load_detail, artifacts, task_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown research task")
    return payload


@router.get("/{task_id}/report")
async def get_research_report(request: Request, task_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> dict[str, Any] | None:
        with artifacts.session() as session:
            row = runs_service.get_report(session, task_id)
            return row.report_json if row else None

    report = await asyncio.to_thread(load)
    if report is None:
        raise HTTPException(status_code=404, detail="report not synthesized yet")
    return report


@router.post("/{task_id}/cancel")
async def cancel_research(request: Request, task_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load_workflow_id() -> str | None:
        with artifacts.session() as session:
            task = artifact_service.get_task(session, task_id)
            return task.workflow_id if task else None

    workflow_id = await asyncio.to_thread(load_workflow_id)
    if workflow_id is None:
        raise HTTPException(status_code=404, detail="unknown research task")

    client = await _temporal_client(request)
    handle = client.get_workflow_handle(workflow_id)
    try:
        await handle.cancel()
    except Exception as exc:  # noqa: BLE001 - the workflow may already be gone/terminal
        logger.warning("research_cancel_failed", task_id=str(task_id), error=str(exc))

    def mark() -> None:
        with artifacts.session() as session:
            runs_service.update_run(
                session,
                task_id,
                stage=STAGE_CANCELLED,
                event={"stage": STAGE_CANCELLED, "detail": "cancelled by owner"},
            )

    await asyncio.to_thread(mark)
    logger.info("research_cancelled", task_id=str(task_id), workflow_id=workflow_id)
    return {"task_id": str(task_id), "status": "cancel_requested"}


__all__ = ["router"]
