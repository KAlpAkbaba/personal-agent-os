"""/v1/mobile — the native client's surface (M9).

Endpoints:

- ``GET    /v1/mobile/push/providers``            transports + what a credential activates
- ``POST   /v1/mobile/push/registrations``        register this session for push
- ``GET    /v1/mobile/push/registrations``        list registrations (owner-wide)
- ``DELETE /v1/mobile/push/registrations/{id}``   unregister
- ``POST   /v1/mobile/notifications/artifact-ready``  announce a READY artifact
- ``GET    /v1/mobile/notifications``             poll this session's deliveries
- ``GET    /v1/mobile/share/{artifact_id}``       what can be shared/exported
- ``GET    /v1/mobile/share/{artifact_id}/{fmt}`` download a render to save/share

Owner authentication is applied at the router (ADR-0027), so a new endpoint in
this module is protected by default rather than by memory. Nothing here may be
reachable unauthenticated: a push registration is a delivery target for the
owner's work product and a share URL is that work product.
"""

from __future__ import annotations

import asyncio
import unicodedata
import uuid
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.artifacts import render_store
from app.artifacts import service as artifact_service
from app.artifacts.models import ARTIFACT_STATE_READY, TASK_STATUS_READY
from app.artifacts.renderers import EXTENSIONS, SUPPORTED_FORMATS
from app.artifacts.runtime import ArtifactRuntime
from app.identity.dependencies import require_owner_session
from app.identity.service import SessionContext
from app.logging import get_logger
from app.mobile.config import share_max_bytes
from app.mobile.errors import MobileError, MobileErrorClass
from app.mobile.models import PUSH_PROVIDERS
from app.mobile.providers import MAX_TOKEN_CHARS, MIN_TOKEN_CHARS
from app.mobile.runtime import MobileRuntime
from app.mobile.service import MobileService

logger = get_logger("app.mobile.routes")

router = APIRouter(
    prefix="/v1/mobile",
    dependencies=[Depends(require_owner_session)],
)

ProviderName = Literal["fake", "fcm", "apns", "webpush"]
assert set(PUSH_PROVIDERS) == set(ProviderName.__args__)  # keep the literal honest

#: "Gönder" on a phone means a PDF unless the owner says otherwise
#: (MASTER_SPEC §B presentation).
PREFERRED_SHARE_FORMAT = "pdf"

#: Coarse HTTP class per typed mobile error. Same discipline as the identity
#: layer: the caller gets a status, the log gets the reason.
_STATUS = {
    MobileErrorClass.VALIDATION_ERROR: 422,
    MobileErrorClass.CAPABILITY_MISSING: 422,
    MobileErrorClass.PAYLOAD_TOO_LARGE: 413,
    MobileErrorClass.SESSION_REVOKED: 401,
    MobileErrorClass.PUSH_TOKEN_INVALID: 409,
    MobileErrorClass.PROVIDER_AUTH_MISSING: 503,
    MobileErrorClass.DEPENDENCY_UNAVAILABLE: 503,
    MobileErrorClass.TIMEOUT: 504,
    MobileErrorClass.OPTIONAL_DEPENDENCY_MISSING: 503,
}


def _http(exc: MobileError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS.get(exc.error_class, 500),
        detail={"error_class": str(exc.error_class), "message": exc.message},
    )


def _runtime(request: Request) -> MobileRuntime:
    state = request.app.state
    if not hasattr(state, "mobile"):  # pragma: no cover - wiring guard
        from app.config import get_settings

        state.mobile = MobileRuntime(get_settings())
    return state.mobile


def _service(request: Request) -> MobileService:
    return _runtime(request).service


def _artifacts(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


# ------------------------------------------------------------------ providers


@router.get("/push/providers")
async def list_providers(request: Request) -> dict[str, Any]:
    """Declared capabilities per transport, plus whether it is activated.

    The orchestrator queries this instead of assuming a transport, and the
    owner reads it to see exactly which credential would light which path up.
    """
    service = _service(request)
    return {"providers": await asyncio.to_thread(service.capabilities)}


# -------------------------------------------------------------- registrations


class RegisterPushRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderName = "fake"
    #: The opaque provider token (FCM/APNs) or push subscription URL (WebPush).
    #: Stored only as a SHA-256 hash; the live value never leaves memory.
    token: str = Field(min_length=MIN_TOKEN_CHARS, max_length=MAX_TOKEN_CHARS)
    platform: str = Field(default="", max_length=32)
    locale: str = Field(default="tr-TR", max_length=16)


@router.post("/push/registrations", status_code=201)
async def register_push(
    request: Request,
    body: RegisterPushRequest,
    session: Annotated[SessionContext, Depends(require_owner_session)],
) -> dict[str, Any]:
    """Register this client for push. Bound to the CURRENT owner session, so it
    dies with the session — including when the session's device is revoked."""
    service = _service(request)

    def work():
        return service.register(
            session_id=session.session_id,
            provider_name=body.provider,
            token=body.token,
            platform=body.platform,
            locale=body.locale,
        )

    try:
        view = await asyncio.to_thread(work)
    except MobileError as exc:
        raise _http(exc) from exc
    return view.to_dict()


@router.get("/push/registrations")
async def list_push_registrations(
    request: Request,
    session: Annotated[SessionContext, Depends(require_owner_session)],
    mine_only: bool = Query(default=False),
    active_only: bool = Query(default=False),
) -> dict[str, Any]:
    """One owner, so the default view is every registration, flagged `current`
    — that is the "which of my devices still get notified?" question."""
    service = _service(request)
    scope = session.session_id if mine_only else None
    views = await asyncio.to_thread(
        lambda: service.list_registrations(session_id=scope, active_only=active_only)
    )
    return {
        "registrations": [
            {**view.to_dict(), "current": view.session_id == session.session_id}
            for view in views
        ]
    }


@router.delete("/push/registrations/{registration_id}")
async def unregister_push(
    request: Request,
    registration_id: uuid.UUID,
    session: Annotated[SessionContext, Depends(require_owner_session)],
    any_session: bool = Query(
        default=False,
        description="Retire a registration belonging to another session "
        "(e.g. a phone the owner no longer has).",
    ),
) -> dict[str, Any]:
    service = _service(request)
    scope = None if any_session else session.session_id
    removed = await asyncio.to_thread(
        lambda: service.unregister(registration_id=registration_id, session_id=scope)
    )
    if not removed:
        raise HTTPException(status_code=404, detail="unknown or already retired registration")
    return {"registration_id": str(registration_id), "unregistered": True}


# ------------------------------------------------------------- notifications


class ArtifactReadyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None


@router.post("/notifications/artifact-ready", status_code=200)
async def notify_artifact_ready(
    request: Request, body: ArtifactReadyRequest
) -> dict[str, Any]:
    """Announce that a research artifact reached READY.

    **Why this is an endpoint and not a call inside the render activity.** The
    activity that flips a task to READY runs inside the *Temporal worker*, a
    different process from the API. A push adapter holds the live provider
    tokens in process memory (the database has only hashes, by the frozen
    schema's own design note), so a delivery attempted from the worker would
    run in a process with no registrations and no provider state — it would
    "succeed" at delivering nothing. Delivery belongs to the process that holds
    the sessions; the workflow's job is to *announce* readiness.

    It is not a claim the caller can fake, either: the announcement is
    authorised by durable state, not by the request. This refuses with 409
    unless the task is actually READY (or, for an artifact-only announcement,
    the artifact is in state READY).
    """
    if (body.artifact_id is None) == (body.task_id is None):
        raise HTTPException(
            status_code=422, detail="provide exactly one of 'artifact_id' or 'task_id'"
        )
    runtime = _artifacts(request)
    service = _service(request)

    def resolve() -> tuple[uuid.UUID, str, uuid.UUID | None] | str:
        with runtime.session() as db:
            if body.task_id is not None:
                task = artifact_service.get_task(db, body.task_id)
                if task is None:
                    return "unknown_task"
                if task.status != TASK_STATUS_READY:
                    return f"task_not_ready:{task.status}"
                artifact = artifact_service.get_artifact_for_task(db, body.task_id)
                if artifact is None:
                    return "no_artifact_for_task"
                return artifact.id, artifact.title, task.id
            artifact = artifact_service.get_artifact(db, body.artifact_id)  # type: ignore[arg-type]
            if artifact is None:
                return "unknown_artifact"
            if artifact.state != ARTIFACT_STATE_READY:
                return f"artifact_not_ready:{artifact.state}"
            return artifact.id, artifact.title, artifact.task_id

    resolved = await asyncio.to_thread(resolve)
    if isinstance(resolved, str):
        if resolved.startswith("unknown") or resolved == "no_artifact_for_task":
            raise HTTPException(status_code=404, detail=resolved)
        raise HTTPException(status_code=409, detail=resolved)
    artifact_id, title, task_id = resolved

    def deliver():
        return service.notify_artifact_ready(
            artifact_id=artifact_id, title=title, task_id=task_id
        )

    result = await asyncio.to_thread(deliver)
    logger.info(
        "mobile_artifact_ready_announced",
        artifact_id=str(artifact_id),
        delivered=result.delivered,
        failed=result.failed,
    )
    return {"artifact_id": str(artifact_id), **result.to_dict()}


@router.get("/notifications")
async def list_notifications(
    request: Request,
    session: Annotated[SessionContext, Depends(require_owner_session)],
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Notifications recorded for THIS session's registrations.

    This is the stand-in for receiving an OS push on a device the project does
    not have. It reads the deterministic provider's in-process delivery log; a
    real transport hands the message to Apple/Google and leaves nothing to
    poll, which is stated in the payload so no caller mistakes this for a
    durable notification inbox.
    """
    service = _service(request)
    items = await asyncio.to_thread(
        lambda: service.deliveries_for_session(session.session_id, limit=limit)
    )
    return {
        "notifications": items,
        "source": "provider_delivery_log",
        "durable": False,
    }


# ------------------------------------------------------------- share / export


def _ascii_slug(title: str, *, fallback: str) -> str:
    """A safe ASCII filename stem. Turkish loses its diacritics here; the exact
    title still travels in the RFC 5987 `filename*` parameter."""
    folded = (
        title.replace("ı", "i")
        .replace("İ", "I")
        .replace("ğ", "g")
        .replace("Ğ", "G")
        .replace("ş", "s")
        .replace("Ş", "S")
    )
    decomposed = unicodedata.normalize("NFKD", folded)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    out: list[str] = []
    for ch in ascii_only:
        if ch.isalnum():
            out.append(ch.lower())
        elif out and out[-1] != "-":
            out.append("-")
    slug = "".join(out).strip("-")[:64]
    return slug or fallback


def _content_disposition(filename_ascii: str, filename_utf8: str) -> str:
    # Both forms: legacy clients read `filename`, everything modern reads
    # `filename*` and gets the real Turkish title.
    return (
        f'attachment; filename="{filename_ascii}"; '
        f"filename*=UTF-8''{quote(filename_utf8, safe='')}"
    )


@router.get("/share/{artifact_id}")
async def share_index(request: Request, artifact_id: uuid.UUID) -> dict[str, Any]:
    """What this artifact can be handed to the OS share sheet as ("Gönder")."""
    runtime = _artifacts(request)
    max_bytes = share_max_bytes()

    def load() -> dict[str, Any] | None:
        with runtime.session() as db:
            artifact = artifact_service.get_artifact(db, artifact_id)
            if artifact is None:
                return None
            version = artifact_service.get_current_version(db, artifact.id)
            renders = artifact_service.list_renders(db, version.id) if version else []
            stem = _ascii_slug(artifact.title, fallback=artifact_id.hex[:12])
            return {
                "artifact_id": str(artifact_id),
                "title": artifact.title,
                "state": artifact.state,
                "version": artifact.current_version,
                "preferred_format": PREFERRED_SHARE_FORMAT,
                "max_bytes": max_bytes,
                "renders": [
                    {
                        "format": r.format,
                        "mime_type": r.mime_type,
                        "size_bytes": r.size_bytes,
                        "content_hash": r.content_hash,
                        "filename": f"{stem}-v{artifact.current_version}.{EXTENSIONS[r.format]}",
                        "url": f"/v1/mobile/share/{artifact_id}/{r.format}",
                        "within_bound": r.size_bytes <= max_bytes,
                    }
                    for r in renders
                ],
            }

    payload = await asyncio.to_thread(load)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown artifact")
    return payload


@router.get("/share/{artifact_id}/{fmt}")
async def share_render(request: Request, artifact_id: uuid.UUID, fmt: str) -> Response:
    """Return an artifact render for saving/sharing on the client.

    Deliberately the *same* render storage as `/v1/artifacts/{id}/renders/{fmt}`
    — `render_store.fetch_render_bytes`, which regenerates a missing object from
    the canonical body. There is no second rendering path and no second bucket.
    What this adds over the artifacts route is the mobile share sheet's needs: a
    human-readable filename (RFC 5987, so a Turkish title survives) and a size
    bound, because a phone should refuse a 200 MB download rather than discover
    it at byte 199,999,999.
    """
    fmt = fmt.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise HTTPException(status_code=422, detail=f"unsupported format: {fmt!r}")
    runtime = _artifacts(request)
    max_bytes = share_max_bytes()

    def load() -> tuple[bytes, str, str, str, int] | str:
        with runtime.session() as db:
            artifact = artifact_service.get_artifact(db, artifact_id)
            if artifact is None:
                return "unknown artifact"
            version = artifact_service.get_current_version(db, artifact.id)
            if version is None:
                return "artifact has no canonical body"
            existing = artifact_service.get_render(db, version.id, fmt)
            # Refuse *before* materialising the bytes when we already know the
            # size; the bound is a promise to the client, not a post-hoc check.
            if existing is not None and existing.size_bytes > max_bytes:
                return f"too_large:{existing.size_bytes}"
            data, mime, chash = render_store.fetch_render_bytes(
                db, runtime.store, version=version, title=artifact.title, fmt=fmt
            )
            if len(data) > max_bytes:
                return f"too_large:{len(data)}"
            return data, mime, chash, artifact.title, version.version

    result = await asyncio.to_thread(load)
    if isinstance(result, str):
        if result.startswith("too_large:"):
            raise HTTPException(
                status_code=413,
                detail={
                    "error_class": str(MobileErrorClass.PAYLOAD_TOO_LARGE),
                    "message": "render exceeds the mobile share bound",
                    "size_bytes": int(result.split(":", 1)[1]),
                    "max_bytes": max_bytes,
                },
            )
        raise HTTPException(status_code=404, detail=result)

    data, mime, chash, title, version_no = result
    ext = EXTENSIONS[fmt]
    stem = _ascii_slug(title, fallback=artifact_id.hex[:12])
    ascii_name = f"{stem}-v{version_no}.{ext}"
    utf8_name = f"{title.strip() or stem} v{version_no}.{ext}"
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": _content_disposition(ascii_name, utf8_name),
            "Content-Length": str(len(data)),
            "X-Content-Hash": chash,
            # A render is immutable for a given (artifact, version, format):
            # the phone can cache it and skip the next download.
            "Cache-Control": "private, max-age=86400",
        },
    )


__all__ = ["PREFERRED_SHARE_FORMAT", "router"]
