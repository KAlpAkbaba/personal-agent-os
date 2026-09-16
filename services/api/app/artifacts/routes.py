"""Research + artifact REST surface (M3, API_AND_PROTOCOLS.md §1/§2/§6).

Design notes:
- POST /v1/tasks returns a durable task_id immediately (202) after starting the
  ResearchWorkflow; it never blocks on completion.
- GET /v1/tasks/{id} exposes status (incl. READY) but never the report body.
- GET /v1/artifacts/{id} returns metadata + executive_summary + available
  renders; the full body is returned ONLY with ?include=body. This is the
  READY-without-auto-read guarantee at the API layer.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.artifacts import factory, open_service, render_store, service
from app.artifacts import lifecycle as artifact_lifecycle
from app.artifacts.models import (
    CANONICAL_FORMAT_ARTIFACT_SPEC_JSON,
    Artifact,
    ArtifactVersion,
    Task,
)
from app.artifacts.provenance import ACTOR_OWNER_REST, Actor
from app.artifacts.render_fetch_store import RenderFetchStore, get_render_fetch_store
from app.artifacts.renderers import (
    DEFAULT_RENDER_FORMATS,
    EXTENSIONS,
    FACTORY_FORMATS,
    SUPPORTED_FORMATS,
)
from app.artifacts.runtime import ArtifactRuntime
from app.artifacts.spec import ArtifactSpec
from app.errors import owner_detail
from app.identity.dependencies import require_owner_session
from app.logging import get_logger, trace_id_var
from app.research.provider import DeterministicResearchProvider
from app.research.workflow import ResearchRequest, ResearchWorkflow

#: The union of formats EITHER surface can name in a bare ``fmt`` path/body param —
#: the M13 markdown-routed set and the M22 factory set. Which ones are actually
#: legal for a GIVEN artifact is decided by its own canonical_format/kind further
#: down (ArtifactSpec.formats() for a factory artifact), not by this constant alone.
_ALL_KNOWN_FORMATS = frozenset(SUPPORTED_FORMATS) | frozenset(FACTORY_FORMATS)

logger = get_logger("app.artifacts.routes")

# M9/ADR-0027: owner authentication is applied at the router, so a new
# endpoint in this module is protected by default rather than by memory.
# tasks and artifacts are the owner's work product,
# and this module has no surface that must stay reachable unauthenticated.
router = APIRouter(
    prefix="/v1",
    dependencies=[Depends(require_owner_session)],
)

#: The token-authenticated exception (ADR-0085 addendum 5), the same pattern
#: ``app.alarms.routes.audio_router`` already establishes for the greeting WAV: a
#: separate router with NO ``require_owner_session`` dependency, because the fetcher is
#: a device holding no owner session — the single-use render-fetch token minted by
#: ``app.artifacts.open_service`` (via ``app.artifacts.render_fetch_store``) IS the
#: authority. Declared as its own router so the exemption is a visible, single line
#: rather than a per-route flag someone could copy by accident.
device_router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])

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
            "state": r.state,
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
        # B42 (req 405-408): the current version's provenance and source manifest.
        "provenance": version.provenance_json if version else None,
        "source_manifest": version.source_manifest_json if version else None,
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
                out.append(_artifact_payload(artifact, version, renders, include_body=False))
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
    if fmt not in _ALL_KNOWN_FORMATS:
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
                session,
                runtime.store,
                version=version,
                title=artifact.title,
                fmt=fmt,
                canonical_format=artifact.canonical_format,
            )
            return {
                "artifact_id": str(artifact_id),
                "format": row.format,
                "mime_type": row.mime_type,
                "content_hash": row.content_hash,
                "size_bytes": row.size_bytes,
                "state": row.state,
            }

    try:
        payload = await asyncio.to_thread(do_render)
    except ValueError as exc:
        # render_factory() refuses a format the artifact's own kind cannot produce
        # (ArtifactSpec.formats()) — a 422, not a 500: the caller asked for something
        # this artifact was never going to be able to make.
        raise HTTPException(status_code=422, detail=owner_detail("validation_error")) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="artifact or canonical body not found")
    return payload


@router.get("/artifacts/{artifact_id}/renders/{fmt}")
async def get_render(request: Request, artifact_id: uuid.UUID, fmt: str) -> Response:
    runtime = _runtime(request)
    fmt = fmt.lower()
    if fmt not in _ALL_KNOWN_FORMATS:
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
                session,
                runtime.store,
                version=version,
                title=artifact.title,
                fmt=fmt,
                canonical_format=artifact.canonical_format,
            )
            return data, mime, chash, artifact.title

    try:
        result = await asyncio.to_thread(load)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=owner_detail("validation_error")) from exc
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


@router.get("/artifacts/{artifact_id}/renders/{fmt}/validation")
async def get_render_validation(
    request: Request, artifact_id: uuid.UUID, fmt: str
) -> dict[str, Any]:
    """ADR-0085 decision 3: the independent reader's ValidationReport for one
    render, so the Cockpit/voice can ask "is this file correct?" without
    re-downloading and re-parsing the bytes themselves."""
    runtime = _runtime(request)
    fmt = fmt.lower()
    import asyncio

    def load() -> dict[str, Any] | None:
        with runtime.session() as session:
            return factory.render_validation(session, artifact_id=artifact_id, fmt=fmt)

    payload = await asyncio.to_thread(load)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown artifact or render")
    return payload


# ------------------------------------------------------------------- factory


class CreateFactoryArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The ArtifactSpec's own fields, validated inside the handler (rather than as
    #: a nested pydantic model here) so a bad spec surfaces the SAME field-level
    #: pydantic error detail either way, and this request model never drifts out of
    #: sync with ArtifactSpec's own shape.
    spec: dict[str, Any]
    conversation_id: uuid.UUID | None = None


def _factory_render_payload(request: Request, artifact_id: uuid.UUID, r: Any) -> dict[str, Any]:
    base = str(request.base_url).rstrip("/")
    return {
        "format": r.format,
        "mime_type": r.mime_type,
        "content_hash": r.content_hash,
        "size_bytes": r.size_bytes,
        "state": r.state,
        "failing_refs": r.failing_refs,
        # ADR-0085 decision 4: the device's file.fetch dials the EXISTING M13 render
        # download route on the origin it dialled — built from THIS request's own
        # base_url (ADR-0069's rule: never a configured URL), never a new route.
        "download_url": f"{base}{factory.download_path(artifact_id, r.format)}",
    }


@router.post("/artifacts/factory", status_code=201)
async def create_factory_artifact(
    request: Request, body: CreateFactoryArtifactRequest
) -> dict[str, Any]:
    runtime = _runtime(request)
    try:
        spec = ArtifactSpec.model_validate(body.spec)
    except ValidationError as exc:
        # include_context=False: pydantic's raw errors() carries the ORIGINAL
        # exception object (e.g. a ValueError) under ctx.error for a custom
        # model_validator failure, which json.dumps cannot serialize -- without
        # this the error handler itself crashes (a real bug this route's own test
        # caught: a 500 with no body, not the 422 the caller was owed).
        raise HTTPException(status_code=422, detail=owner_detail("validation_error")) from exc
    import asyncio

    def do_create() -> factory.FactoryResult:
        with runtime.session() as session:
            return factory.create(
                session,
                runtime.store,
                spec=spec,
                conversation_id=body.conversation_id,
                actor=Actor(
                    ACTOR_OWNER_REST, session_id=str(request.state.owner_session.session_id)
                ),
            )

    result = await asyncio.to_thread(do_create)
    return {
        "artifact_id": str(result.artifact_id),
        "kind": result.kind,
        "title": result.title,
        "version": result.version,
        "created": result.created,
        "canonical_format": CANONICAL_FORMAT_ARTIFACT_SPEC_JSON,
        "all_valid": result.all_valid,
        "renders": [
            _factory_render_payload(request, result.artifact_id, r) for r in result.renders
        ],
    }


# --------------------------------------------------------------------- open


class OpenArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Which render to fetch+open; ``None`` (the default, and what the Cockpit's
    #: ``POST .../open`` with an empty body sends — ADR-0085 addendum 2 item 5) lets the
    #: factory pick the first VALID render in the artifact kind's own format order.
    format: str | None = Field(default=None, max_length=16)


# ------------------------------------------------------------ B42: the lifecycle


class EditArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edit: dict[str, Any]


class CloneArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=500)
    version: int | None = Field(default=None, ge=1)


class DeleteArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


def _lifecycle_error(exc: artifact_lifecycle.ArtifactLifecycleError) -> HTTPException:
    status = 404 if exc.code == "not_found" else 422
    return HTTPException(status_code=status, detail={"code": exc.code, "message": exc.speech})


class ImageArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    object_key: str = Field(min_length=1, max_length=512)
    sources: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    conversation_id: uuid.UUID | None = None


@router.post("/artifacts/image", status_code=201)
async def register_image_artifact(request: Request, body: ImageArtifactRequest) -> dict[str, Any]:
    """B42 (req 400): an image the creative path stored (``creative/<run>/<name>``) becomes
    an artifact with the same provenance, versions, clone, delete and compare as every
    other kind - the bytes copied under the artifact's own key."""
    runtime = _runtime(request)
    actor = Actor(ACTOR_OWNER_REST, session_id=str(request.state.owner_session.session_id))

    def do() -> dict[str, Any]:
        with runtime.session() as session:
            return artifact_lifecycle.register_image_artifact(
                session,
                runtime.store,
                title=body.title,
                object_key=body.object_key,
                actor=actor,
                sources=body.sources,
                conversation_id=body.conversation_id,
            ).as_dict()

    try:
        return await asyncio.to_thread(do)
    except artifact_lifecycle.ArtifactLifecycleError as exc:
        raise _lifecycle_error(exc) from exc
    except ValueError as exc:  # validate_object_key
        raise HTTPException(status_code=422, detail=owner_detail("validation_error")) from exc


@router.get("/artifacts/{artifact_id}/versions")
async def list_artifact_versions(request: Request, artifact_id: uuid.UUID) -> dict[str, Any]:
    """B42 (req 409): every version with its provenance and source manifest."""
    runtime = _runtime(request)

    def load() -> dict[str, Any] | None:
        with runtime.session() as session:
            artifact = service.get_artifact(session, artifact_id)
            if artifact is None:
                return None
            versions = service.list_versions(session, artifact_id)
            return {
                "artifact_id": str(artifact.id),
                "title": artifact.title,
                "current_version": artifact.current_version,
                "versions": [artifact_lifecycle.version_payload(v) for v in versions],
            }

    payload = await asyncio.to_thread(load)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown artifact")
    return payload


@router.post("/artifacts/{artifact_id}/edit", status_code=201)
async def edit_artifact(
    request: Request, artifact_id: uuid.UUID, body: EditArtifactRequest
) -> dict[str, Any]:
    """B42 (req 410): the edit becomes the next version of the SAME artifact."""
    runtime = _runtime(request)
    try:
        edit = artifact_lifecycle.ArtifactEdit.model_validate(body.edit)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=owner_detail("validation_error")) from exc
    actor = Actor(ACTOR_OWNER_REST, session_id=str(request.state.owner_session.session_id))

    def do() -> dict[str, Any]:
        with runtime.session() as session:
            return artifact_lifecycle.edit_artifact(
                session, runtime.store, artifact_id, edit, actor=actor
            ).as_dict()

    try:
        return await asyncio.to_thread(do)
    except artifact_lifecycle.ArtifactLifecycleError as exc:
        raise _lifecycle_error(exc) from exc
    except (ValidationError, ValueError) as exc:
        # The edited spec failed the spec's own rules (never invented, no formula
        # injection): 422 with the owner sentence, the failing input never echoed.
        raise HTTPException(status_code=422, detail=owner_detail("validation_error")) from exc


@router.post("/artifacts/{artifact_id}/clone", status_code=201)
async def clone_artifact(
    request: Request, artifact_id: uuid.UUID, body: CloneArtifactRequest
) -> dict[str, Any]:
    """B42 (req 411): a new artifact whose provenance names this one."""
    runtime = _runtime(request)
    actor = Actor(ACTOR_OWNER_REST, session_id=str(request.state.owner_session.session_id))

    def do() -> dict[str, Any]:
        with runtime.session() as session:
            return artifact_lifecycle.clone_artifact(
                session,
                runtime.store,
                artifact_id,
                title=body.title,
                actor=actor,
                version_number=body.version,
            ).as_dict()

    try:
        return await asyncio.to_thread(do)
    except artifact_lifecycle.ArtifactLifecycleError as exc:
        raise _lifecycle_error(exc) from exc


@router.post("/artifacts/{artifact_id}/delete")
async def delete_artifact(
    request: Request, artifact_id: uuid.UUID, body: DeleteArtifactRequest | None = None
) -> dict[str, Any]:
    """B42 (req 412): under the owner's delete policy (settings.artifact_delete_policy)."""
    runtime = _runtime(request)
    policy = str(getattr(runtime.settings, "artifact_delete_policy", "confirm") or "confirm")
    confirmed = bool(body.confirm) if body is not None else False

    def do() -> dict[str, Any]:
        with runtime.session() as session:
            return artifact_lifecycle.delete_artifact(
                session, runtime.store, artifact_id, policy=policy, confirmed=confirmed
            ).as_dict()

    try:
        outcome = await asyncio.to_thread(do)
    except artifact_lifecycle.ArtifactLifecycleError as exc:
        raise _lifecycle_error(exc) from exc
    return {**outcome, "policy": policy}


@router.get("/artifacts/{artifact_id}/compare")
async def compare_artifact(
    request: Request,
    artifact_id: uuid.UUID,
    against: Annotated[uuid.UUID | None, Query()] = None,
    version: Annotated[int | None, Query(ge=1)] = None,
    against_version: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    """B42 (req 415/416): what differs, named, and the diff. With no ``against`` the
    artifact's current version is compared with the version before it."""
    runtime = _runtime(request)

    def do() -> dict[str, Any]:
        with runtime.session() as session:
            other = against or artifact_id
            other_version = against_version
            if against is None and against_version is None:
                artifact = service.get_artifact(session, artifact_id)
                if artifact is None:
                    raise artifact_lifecycle.ArtifactLifecycleError(
                        artifact_lifecycle.ERROR_NOT_FOUND, "Böyle bir artefakt bulamadım efendim."
                    )
                if artifact.current_version < 2:
                    raise artifact_lifecycle.ArtifactLifecycleError(
                        artifact_lifecycle.ERROR_NO_PREVIOUS_VERSION,
                        "Bu artefaktın karşılaştırılacak önceki sürümü yok efendim.",
                    )
                other_version = artifact.current_version - 1
            return artifact_lifecycle.compare_versions(
                session, other, artifact_id, a_version=other_version, b_version=version
            )

    try:
        return await asyncio.to_thread(do)
    except artifact_lifecycle.ArtifactLifecycleError as exc:
        raise _lifecycle_error(exc) from exc


@router.post("/artifacts/{artifact_id}/open")
async def open_artifact_route(
    request: Request, artifact_id: uuid.UUID, body: OpenArtifactRequest | None = None
) -> dict[str, Any]:
    """Fetch + open one artifact's render on the owner's machine (spec §4): the SAME
    ``file.fetch`` path the voice tool ``artifact.open`` uses
    (``app.artifacts.open_service``), so the Cockpit's "Aç" and "Bunu aç." can never
    disagree about what "opened" means."""
    runtime = _runtime(request)
    device_action = getattr(request.app.state, "device_action", None)
    fmt = body.format.lower() if body is not None and body.format else None
    base_url = str(request.base_url)
    # Same store the device-facing redemption route below reads (``_render_fetch_store``)
    # — minting and redeeming must agree on ONE store object, and a test overriding
    # ``app.state.artifact_render_fetch_store`` (e.g. a short TTL) affects both sides of
    # the same round trip, the way overriding ``device_action`` already does.
    fetch_store = _render_fetch_store(request)
    import asyncio

    def do_open() -> open_service.OpenOutcome:
        with runtime.session() as session:
            return open_service.open_artifact(
                session,
                device_action,
                artifact_id=artifact_id,
                fmt=fmt,
                base_url=base_url,
                render_fetch_store=fetch_store,
            )

    outcome = await asyncio.to_thread(do_open)
    if outcome.error_class == open_service.ERROR_NOT_FOUND:
        raise HTTPException(
            status_code=404, detail={"code": outcome.error_class, "message": outcome.speech}
        )
    if outcome.error_class is not None:
        raise HTTPException(
            status_code=422, detail={"code": outcome.error_class, "message": outcome.speech}
        )
    return {
        "artifact_id": outcome.artifact_id,
        "format": outcome.format,
        "state": outcome.state,
        "window_title": outcome.window_title,
        "speech": outcome.speech,
        "error_class": outcome.error_class,
    }


# ------------------------------------------------------------ device render fetch


def _render_fetch_store(request: Request) -> RenderFetchStore:
    return (
        getattr(request.app.state, "artifact_render_fetch_store", None) or get_render_fetch_store()
    )


@device_router.get("/renders/fetch/{token}")
async def fetch_render_by_token(
    request: Request, token: Annotated[str, Field(max_length=128)]
) -> Response:
    """Redeem a one-time render-fetch token minted by ``app.artifacts.open_service`` for
    the device's ``file.fetch`` (DEVICE_PROTOCOL.md §6k step 6: the device's GET carries
    no owner token, cookie or header of its own — ADR-0085 addendum 5, closing the gap
    addendum 4 decision 5 recorded). The ordinary owner-session-gated
    ``GET /v1/artifacts/{id}/renders/{fmt}`` above is unchanged and keeps serving the
    web/Cockpit; this route exists ONLY for a token minted for exactly one render.

    Unknown, expired, already-redeemed and content-hash-mismatched tokens are all the
    SAME bare 404 with no body — deliberately indistinguishable, like
    ``app.alarms.routes.get_greeting_audio``'s greeting-token redemption, so a probe
    learns nothing and the artifact id is never named in the response, a log line or a
    ledger row.
    """
    runtime = _runtime(request)
    store = _render_fetch_store(request)

    def redeem() -> tuple[bytes, str] | None:
        target = store.take(token)
        if target is None:
            return None
        with runtime.session() as session:
            artifact = service.get_artifact(session, target.artifact_id)
            if artifact is None:
                return None
            version = service.get_current_version(session, artifact.id)
            if version is None:
                return None
            row = service.get_render(session, version.id, target.fmt)
            # Pinned at mint time (artifact id + format + content hash): a render that no
            # longer matches what the token named is refused rather than served — never
            # trust the token's own claim once the current row disagrees with it.
            if row is None or row.content_hash != target.content_hash:
                return None
            try:
                data = runtime.store.get(row.object_key)
            except KeyError:
                return None
            return data, row.mime_type

    result = await asyncio.to_thread(redeem)
    if result is None:
        # Bare 404, no body: never the artifact id, the format or WHICH of the refusal
        # reasons applied (module docstring above) — the shape addendum 2 item 5 already
        # documents web callers reading as "not available", never a hint to a prober.
        raise HTTPException(status_code=404)
    data, mime = result
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "no-store", "Content-Length": str(len(data))},
    )


# Re-export for wiring/tests.
__all__ = ["router", "device_router", "DEFAULT_RENDER_FORMATS"]
