"""/v1/identity — the owner's authentication surface.

Deliberately small. One owner means there is no account management to build:
bootstrap once, exchange the credential for a session, refresh it, revoke it,
see what is active, read the audit. That is the whole product surface.

`POST /bootstrap` is the only endpoint that can create authority from nothing,
so it is doubly constrained: it refuses once a credential exists, and it
requires a loopback peer (the owner is on the machine). Recovering a lost
credential is not an API operation at all — see `app.identity.recover`.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.identity.dependencies import require_owner_session
from app.identity.errors import (
    AlreadyBootstrapped,
    InvalidOwnerCredential,
    NotBootstrapped,
    Throttled,
)
from app.identity.models import CLIENT_KINDS, SESSION_EVENT_ACTIONS
from app.identity.runtime import IdentityRuntime
from app.identity.service import IdentityService, IssuedSession, SessionContext
from app.identity.tokens import MAX_TOKEN_CHARS
from app.logging import get_logger, trace_id_var

logger = get_logger("app.identity.routes")

router = APIRouter(prefix="/v1/identity")

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}

ClientKind = Literal["web", "mobile", "desktop", "device", "cli"]
assert set(CLIENT_KINDS) == set(ClientKind.__args__)  # keep the literal honest


def _runtime(request: Request) -> IdentityRuntime:
    runtime: IdentityRuntime | None = getattr(request.app.state, "identity", None)
    if runtime is None:  # pragma: no cover - wiring guard
        raise HTTPException(status_code=503, detail="identity subsystem unavailable")
    return runtime


def _service(request: Request) -> IdentityService:
    return _runtime(request).service


def _require_loopback(request: Request, settings_enabled: bool) -> None:
    if not settings_enabled:
        return
    client = request.client
    host = client.host if client else None
    if host is not None and host not in _LOOPBACK_HOSTS:
        # Same coarse refusal as everything else: no "you are not on loopback".
        raise HTTPException(status_code=403, detail="forbidden")


def _session_payload(context: SessionContext) -> dict[str, Any]:
    return {
        "session_id": str(context.session_id),
        "client_kind": context.client_kind,
        "client_label": context.client_label,
        "device_id": str(context.device_id) if context.device_id else None,
        "scopes": list(context.scopes),
        "unrestricted": context.unrestricted,
        "created_at": context.created_at.isoformat(),
        "expires_at": context.expires_at.isoformat(),
        "last_seen_at": context.last_seen_at.isoformat() if context.last_seen_at else None,
    }


def _issued_payload(issued: IssuedSession, idle_timeout_s: int) -> dict[str, Any]:
    payload = _session_payload(issued.context)
    # The token appears here and nowhere else, ever.
    payload["token"] = issued.token
    payload["idle_timeout_s"] = idle_timeout_s
    return payload


# ------------------------------------------------------------------ bootstrap


@router.post("/bootstrap", status_code=201)
async def bootstrap(request: Request) -> dict[str, Any]:
    """One-time owner action: mint the first owner credential.

    Returned exactly once. If it is lost, recovery runs on the host
    (`python -m app.identity.recover`), not through this API.
    """
    runtime = _runtime(request)
    _require_loopback(request, runtime.settings.identity_bootstrap_loopback_only)
    trace_id = trace_id_var.get()

    def mint() -> str:
        return runtime.service.bootstrap(trace_id=trace_id)

    try:
        credential = await asyncio.to_thread(mint)
    except AlreadyBootstrapped as exc:
        raise HTTPException(
            status_code=409,
            detail="owner credential already exists; use the host recovery path",
        ) from exc
    record = runtime.root.load()
    return {
        "owner_credential": credential,
        "created_at": record.created_at.isoformat() if record else "",
        "note": "store this now; it is shown once and cannot be retrieved",
    }


# ------------------------------------------------------------------- sessions


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_credential: str = Field(min_length=8, max_length=MAX_TOKEN_CHARS)
    client_kind: ClientKind
    label: str = Field(default="", max_length=128)
    device_id: uuid.UUID | None = None
    scopes: list[str] = Field(default_factory=list, max_length=16)
    ttl_s: int | None = Field(default=None, ge=60, le=31_536_000)


@router.post("/sessions", status_code=201)
async def create_session(request: Request, body: CreateSessionRequest) -> dict[str, Any]:
    """Exchange the owner credential for an opaque bearer session."""
    runtime = _runtime(request)
    trace_id = trace_id_var.get()

    def exchange() -> IssuedSession:
        return runtime.service.exchange_credential(
            body.owner_credential,
            client_kind=body.client_kind,
            label=body.label,
            device_id=body.device_id,
            scopes=body.scopes,
            ttl_s=body.ttl_s,
            trace_id=trace_id,
        )

    try:
        issued = await asyncio.to_thread(exchange)
    except Throttled as exc:
        raise HTTPException(
            status_code=429,
            detail="too many attempts",
            headers={"Retry-After": str(exc.retry_after_s)},
        ) from exc
    except (InvalidOwnerCredential, NotBootstrapped) as exc:
        # One coarse class: "no credential exists yet" and "wrong credential"
        # are indistinguishable to the caller by design.
        raise HTTPException(
            status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"}
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _issued_payload(issued, runtime.settings.session_idle_timeout_s)


@router.post("/sessions/refresh")
async def refresh_session(
    request: Request,
    session: Annotated[SessionContext, Depends(require_owner_session)],
) -> dict[str, Any]:
    """Rotate the presented token and extend the window on the same session."""
    runtime = _runtime(request)
    token = request.headers.get("Authorization", "").split(None, 1)[-1]
    trace_id = trace_id_var.get()

    def rotate():
        return runtime.service.refresh(token, trace_id=trace_id)

    result = await asyncio.to_thread(rotate)
    if not isinstance(result, IssuedSession):
        raise HTTPException(
            status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"}
        )
    return _issued_payload(result, runtime.settings.session_idle_timeout_s)


@router.get("/sessions/current")
async def current_session(
    session: Annotated[SessionContext, Depends(require_owner_session)],
) -> dict[str, Any]:
    """Who am I? — the native client's connection check (M9 acceptance)."""
    return _session_payload(session)


@router.get("/sessions")
async def list_sessions(
    request: Request,
    session: Annotated[SessionContext, Depends(require_owner_session)],
    active_only: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    service = _service(request)
    contexts = await asyncio.to_thread(
        lambda: service.list_sessions(active_only=active_only, limit=limit)
    )
    return {
        "sessions": [
            {**_session_payload(ctx), "current": ctx.session_id == session.session_id}
            for ctx in contexts
        ]
    }


@router.delete("/sessions/current")
async def revoke_current_session(
    request: Request,
    session: Annotated[SessionContext, Depends(require_owner_session)],
) -> dict[str, Any]:
    """Sign out this client."""
    service = _service(request)
    trace_id = trace_id_var.get()
    revoked = await asyncio.to_thread(
        lambda: service.revoke_session(
            session.session_id, reason="owner_signed_out", trace_id=trace_id
        )
    )
    return {"session_id": str(session.session_id), "revoked": revoked}


@router.post("/sessions/{session_id}/revoke")
async def revoke_session(
    request: Request,
    session_id: uuid.UUID,
    session: Annotated[SessionContext, Depends(require_owner_session)],
) -> dict[str, Any]:
    """Revoke any session — e.g. the one on a phone the owner no longer has."""
    service = _service(request)
    trace_id = trace_id_var.get()
    revoked = await asyncio.to_thread(
        lambda: service.revoke_session(session_id, reason="owner_revoked", trace_id=trace_id)
    )
    if not revoked:
        raise HTTPException(status_code=404, detail="unknown or already revoked session")
    return {"session_id": str(session_id), "revoked": True}


# ---------------------------------------------------------------------- panic


@router.post("/panic")
async def panic(
    request: Request,
    session: Annotated[SessionContext, Depends(require_owner_session)],
) -> dict[str, Any]:
    """SECURITY_MODEL §10 kill control: revoke EVERY session, this one included.

    The owner credential still works afterwards, so the owner can sign back in
    on a device they still hold. If the credential itself is suspect, rotate it
    on the host with `python -m app.identity.recover --rotate`.
    """
    service = _service(request)
    trace_id = trace_id_var.get()
    revoked = await asyncio.to_thread(
        lambda: service.revoke_all(reason="owner_panic", trace_id=trace_id)
    )
    logger.warning("identity_panic_invoked", revoked=revoked)
    return {"revoked": revoked, "own_session_revoked": True}


# ---------------------------------------------------------------------- audit


@router.get("/events")
async def list_events(
    request: Request,
    session: Annotated[SessionContext, Depends(require_owner_session)],
    limit: int = Query(default=50, ge=1, le=500),
    action: str | None = Query(default=None, max_length=16),
) -> dict[str, Any]:
    """Append-only authentication audit. Never contains a token."""
    if action is not None and action not in SESSION_EVENT_ACTIONS:
        raise HTTPException(status_code=422, detail="unknown action")
    service = _service(request)
    events = await asyncio.to_thread(
        lambda: service.list_events(limit=limit, action=action)
    )
    return {"events": events}


__all__ = ["router"]
