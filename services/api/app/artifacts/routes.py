"""Research + artifact REST surface (M3, API_AND_PROTOCOLS.md §1/§2/§6).

Design notes:
- POST /v1/tasks returns a durable task_id immediately (202) after starting the
  ResearchWorkflow; it never blocks on completion.
- GET /v1/tasks/{id} exposes status (incl. READY) but never the report body.
- GET /v1/artifacts/{id} returns metadata + executive_summary + available
  renders; the full body is returned ONLY with ?include=body. This is the
  READY-without-auto-read guarantee at the API layer.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.artifacts import render_store, service
from app.artifacts.models import Artifact, ArtifactVersion, Task
from app.artifacts.renderers import DEFAULT_RENDER_FORMATS, EXTENSIONS, SUPPORTED_FORMATS
from app.artifacts.runtime import ArtifactRuntime
from app.logging import get_logger, trace_id_var
from app.research.provider import DeterministicResearchProvider
from app.research.workflow import ResearchRequest, ResearchWorkflow

logger = get_logger("app.artifacts.routes")

router = APIRouter(prefix="/v1")

WIRED_PROVIDERS = {DeterministicResearchProvider.name}


def _runtime(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def _temporal_client(runtime: ArtifactRuntime) -> Client:
    return await Client.connect(
        runtime.settings.temporal_address, namespace=runtime.settings.temporal_namespace
    )


# ------------------------------------------------------------------- tasks


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None
    origin_device_id: uuid.UUID | None = None
    modality: str | None = Field(default=None, max_length=32)
    provider: str = Field(default=DeterministicResearchProvider.name, max_length=64)
    source_limit: int = Field(default=6, ge=1, le=8)


def _task_payload(task: Task, *, artifact_id: uuid.UUID | None = None) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "status": task.status,
        "intent": task.intent,
        "workflow_id": task.workflow_id,
        "artifact_id": str(artifact_id) if artifact_id else None,
        "error_class": task.error_class,
        "created_at": _iso(task.created_at),
        "ready_at": _iso(task.ready_at),
        "completed_at": _iso(task.completed_at),
    }


@router.post("/tasks", status_code=202)
async def create_task(request: Request, body: CreateTaskRequest) -> JSONResponse:
    runtime = _runtime(request)
    if body.provider not in WIRED_PROVIDERS:
        raise HTTPException(status_code=422, detail=f"unwired research provider: {body.provider!r}")
    trace_id = trace_id_var.get()

    import asyncio

    def create() -> Task:
        with runtime.session() as session:
            return service.create_task(
                session,
                intent=body.input,
                conversation_id=body.conversation_id,
                created_from_device_id=body.origin_device_id,
                trace_id=trace_id,
            )

    task = await asyncio.to_thread(create)
    workflow_id = f"research-{task.id}"

    client = await _temporal_client(runtime)
    started = True
    try:
        await client.start_workflow(
            ResearchWorkflow.run,
            ResearchRequest(
                task_id=str(task.id),
                topic=body.input,
                provider=body.provider,
                source_limit=body.source_limit,
            ),
            id=workflow_id,
            task_queue=runtime.settings.temporal_task_queue,
        )
    except WorkflowAlreadyStartedError:
        started = False  # idempotent retry of POST

    def persist_wf() -> None:
        with runtime.session() as session:
            service.set_task_workflow_id(session, task.id, workflow_id)

    await asyncio.to_thread(persist_wf)
    logger.info(
        "task_created",
        task_id=str(task.id),
        workflow_id=workflow_id,
        provider=body.provider,
        workflow_started=started,
    )
    return JSONResponse(
        status_code=202,
        content={"task_id": str(task.id), "status": task.status, "workflow_id": workflow_id},
    )


@router.get("/tasks/{task_id}")
async def get_task(request: Request, task_id: uuid.UUID) -> dict[str, Any]:
    runtime = _runtime(request)
    import asyncio

    def load() -> tuple[Task | None, uuid.UUID | None]:
        with runtime.session() as session:
            task = service.get_task(session, task_id)
            if task is None:
                return None, None
            artifact = service.get_artifact_for_task(session, task_id)
            return task, (artifact.id if artifact else None)

    task, artifact_id = await asyncio.to_thread(load)
    if task is None:
        raise HTTPException(status_code=404, detail="unknown task")
    return _task_payload(task, artifact_id=artifact_id)


# --------------------------------------------------------------- artifacts


def _render_payload(runtime_renders: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "format": r.format,
            "mime_type": r.mime_type,
            "content_hash": r.content_hash,
            "size_bytes": r.size_bytes,
        }
        for r in runtime_renders
    ]


def _artifact_payload(
    artifact: Artifact,
    version: ArtifactVersion | None,
    renders: list[Any],
    *,
    include_body: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_id": str(artifact.id),
        "task_id": str(artifact.task_id) if artifact.task_id else None,
        "title": artifact.title,
        "kind": artifact.kind,
        "canonical_format": artifact.canonical_format,
        "state": artifact.state,
        "current_version": artifact.current_version,
        "executive_summary": artifact.executive_summary,
        "content_hash": version.content_hash if version else None,
        "available_renders": _render_payload(renders),
        "created_at": _iso(artifact.created_at),
        "updated_at": _iso(artifact.updated_at),
    }
    if include_body:
        payload["canonical_body"] = version.canonical_body if version else None
    return payload


@router.get("/artifacts")
async def list_artifacts(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    import asyncio

    def load() -> list[dict[str, Any]]:
        with runtime.session() as session:
            out = []
            for artifact in service.list_artifacts(session):
                version = service.get_current_version(session, artifact.id)
                renders = service.list_renders(session, version.id) if version else []
                out.append(
                    _artifact_payload(artifact, version, renders, include_body=False)
                )
            return out

    items = await asyncio.to_thread(load)
    return {"artifacts": items}


@router.get("/artifacts/{artifact_id}")
async def get_artifact(
    request: Request,
    artifact_id: uuid.UUID,
    include: str | None = Query(default=None),
) -> dict[str, Any]:
    runtime = _runtime(request)
    include_body = include == "body"
    import asyncio

    def load() -> dict[str, Any] | None:
        with runtime.session() as session:
            artifact = service.get_artifact(session, artifact_id)
            if artifact is None:
                return None
            version = service.get_current_version(session, artifact.id)
            renders = service.list_renders(session, version.id) if version else []
            return _artifact_payload(artifact, version, renders, include_body=include_body)

    payload = await asyncio.to_thread(load)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown artifact")
    return payload


@router.get("/artifacts/{artifact_id}/canonical")
async def get_artifact_canonical(request: Request, artifact_id: uuid.UUID) -> Response:
    runtime = _runtime(request)
    import asyncio

    def load() -> str | None:
        with runtime.session() as session:
            artifact = service.get_artifact(session, artifact_id)
            if artifact is None:
                return None
            version = service.get_current_version(session, artifact.id)
            return version.canonical_body if version else None

    body = await asyncio.to_thread(load)
    if body is None:
        raise HTTPException(status_code=404, detail="artifact or canonical body not found")
    return Response(content=body, media_type="text/markdown; charset=utf-8")


class CreateRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str = Field(max_length=16)


@router.post("/artifacts/{artifact_id}/renders", status_code=201)
async def create_render(
    request: Request, artifact_id: uuid.UUID, body: CreateRenderRequest
) -> dict[str, Any]:
    runtime = _runtime(request)
    fmt = body.format.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise HTTPException(status_code=422, detail=f"unsupported format: {body.format!r}")
    import asyncio

    def do_render() -> dict[str, Any] | None:
        with runtime.session() as session:
            artifact = service.get_artifact(session, artifact_id)
            if artifact is None:
                return None
            version = service.get_current_version(session, artifact.id)
            if version is None:
                return None
            row = render_store.ensure_render(
                session, runtime.store, version=version, title=artifact.title, fmt=fmt
            )
            return {
                "artifact_id": str(artifact_id),
                "format": row.format,
                "mime_type": row.mime_type,
                "content_hash": row.content_hash,
                "size_bytes": row.size_bytes,
            }

    payload = await asyncio.to_thread(do_render)
    if payload is None:
        raise HTTPException(status_code=404, detail="artifact or canonical body not found")
    return payload


@router.get("/artifacts/{artifact_id}/renders/{fmt}")
async def get_render(request: Request, artifact_id: uuid.UUID, fmt: str) -> Response:
    runtime = _runtime(request)
    fmt = fmt.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise HTTPException(status_code=422, detail=f"unsupported format: {fmt!r}")
    import asyncio

    def load() -> tuple[bytes, str, str, str] | None:
        with runtime.session() as session:
            artifact = service.get_artifact(session, artifact_id)
            if artifact is None:
                return None
            version = service.get_current_version(session, artifact.id)
            if version is None:
                return None
            data, mime, chash = render_store.fetch_render_bytes(
                session, runtime.store, version=version, title=artifact.title, fmt=fmt
            )
            return data, mime, chash, artifact.title

    result = await asyncio.to_thread(load)
    if result is None:
        raise HTTPException(status_code=404, detail="artifact or canonical body not found")
    data, mime, chash, _title = result
    filename = f"{artifact_id.hex}.{EXTENSIONS[fmt]}"
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Hash": chash,
        },
    )


# Re-export for wiring/tests.
__all__ = ["router", "DEFAULT_RENDER_FORMATS"]
