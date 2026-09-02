"""Realtime voice session REST surface (M12 spec §4).

- POST /v1/voice/realtime/sessions                      create (select provider, mint credential)
- GET  /v1/voice/realtime/sessions/{id}                 continuity state (no credential)
- POST /v1/voice/realtime/sessions/{id}/tool-calls      sideband tool relay (idempotent, call_id)
- POST /v1/voice/realtime/sessions/{id}/tool-calls/{call_id}/complete   long-running tool done
- POST /v1/voice/realtime/sessions/{id}/events          client timing/state events
- POST /v1/voice/realtime/sessions/{id}/attach          new client takes over (spec §7)
- POST /v1/voice/realtime/sessions/{id}/close
- GET  /v1/voice/realtime/sessions/{id}/benchmark       report from the client's timestamps
- GET  /v1/voice/realtime/providers                     candidates + selection (no secrets)

Owner authentication is applied at the router (M9/ADR-0027). The only secret
that ever leaves this surface is the per-session provider credential, once,
in the create/attach response; the owner-provisioned vendor key is never read
here. All DB work runs in a thread (sync SQLAlchemy).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.identity.dependencies import require_owner_session
from app.identity.service import SessionContext
from app.logging import get_logger, trace_id_var
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import TRANSPORTS
from app.voice.realtime_sessions import service
from app.voice.realtime_sessions.models import REALTIME_STATE_CLOSED, REALTIME_STATE_EXPIRED
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime

logger = get_logger("app.voice.realtime_sessions.routes")

router = APIRouter(
    prefix="/v1/voice/realtime",
    dependencies=[Depends(require_owner_session)],
)

_STATUS_BY_CLASS = {
    VoiceErrorClass.VALIDATION_ERROR: 422,
    VoiceErrorClass.CAPABILITY_MISSING: 503,
    VoiceErrorClass.PROVIDER_AUTH_MISSING: 503,
    VoiceErrorClass.DEPENDENCY_UNAVAILABLE: 503,
    VoiceErrorClass.OPTIONAL_DEPENDENCY_MISSING: 503,
    VoiceErrorClass.ALL_PROVIDERS_FAILED: 502,
    VoiceErrorClass.TIMEOUT: 504,
    VoiceErrorClass.INTERNAL_BUG: 500,
}

MAX_ARGUMENTS_BYTES = 16 * 1024
MAX_EVENT_PAYLOAD_BYTES = 4 * 1024
MAX_EVENTS_PER_REQUEST = 200
_FORBIDDEN_PAYLOAD_KEY_PARTS = ("audio", "pcm", "wave", "secret", "credential", "api_key")


def _runtime(request: Request) -> RealtimeVoiceRuntime:
    return request.app.state.voice_realtime


def _owner(request: Request) -> SessionContext:
    return request.state.owner_session


def _bind_loop(runtime: RealtimeVoiceRuntime) -> None:
    bind = getattr(runtime.sideband, "bind_loop", None)
    if bind is not None:
        bind(asyncio.get_running_loop())


def _raise_http(exc: VoiceError) -> None:
    status = _STATUS_BY_CLASS.get(exc.error_class, 400)
    state = exc.details.get("state") if exc.details else None
    if state in (REALTIME_STATE_CLOSED, REALTIME_STATE_EXPIRED):
        status = 410
    elif exc.details and exc.details.get("leg") == "mismatch":
        status = 409
    raise HTTPException(status_code=status, detail=exc.to_dict()) from exc


def _no_forbidden_keys(value: Any, *, where: str) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _FORBIDDEN_PAYLOAD_KEY_PARTS):
                raise ValueError(f"{where} must not carry audio or credentials ({key!r})")
            _no_forbidden_keys(inner, where=where)
    elif isinstance(value, list):
        for inner in value:
            _no_forbidden_keys(inner, where=where)


def _bounded_json(value: dict[str, Any], *, limit: int, where: str) -> dict[str, Any]:
    _no_forbidden_keys(value, where=where)
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where} must be JSON-serializable") from exc
    if len(encoded.encode("utf-8")) > limit:
        raise ValueError(f"{where} exceeds {limit} bytes")
    return value


# ------------------------------------------------------------------ payloads


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_kind: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,15}$")
    transport: str | None = None
    language: str = Field(default="tr-TR", pattern=r"^[a-z]{2}-[A-Z]{2}$")
    narration_session_id: uuid.UUID | None = None
    session_ttl_s: int | None = Field(default=None, ge=60, le=86_400)

    @field_validator("transport")
    @classmethod
    def _known_transport(cls, value: str | None) -> str | None:
        if value is not None and value not in TRANSPORTS:
            raise ValueError(f"transport must be one of {TRANSPORTS}")
        return value


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.]*$")
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("arguments")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, limit=MAX_ARGUMENTS_BYTES, where="arguments")


class ToolCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @field_validator("result", "error")
    @classmethod
    def _bounded(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return _bounded_json(value, limit=MAX_ARGUMENTS_BYTES, where="result")


class ClientEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=64)
    t_ms: int = Field(ge=0, le=10**12)
    turn: int = Field(default=0, ge=0, le=10**6)
    payload: dict[str, Any] = Field(default_factory=dict)
    text: str | None = Field(default=None, max_length=4000)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in service.CLIENT_EVENT_KINDS:
            raise ValueError(f"unknown event kind {value!r}")
        return value

    @field_validator("payload")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, limit=MAX_EVENT_PAYLOAD_BYTES, where="payload")


class EventsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[ClientEvent] = Field(min_length=1, max_length=MAX_EVENTS_PER_REQUEST)


class AttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_kind: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,15}$")
    transport: str | None = None

    @field_validator("transport")
    @classmethod
    def _known_transport(cls, value: str | None) -> str | None:
        if value is not None and value not in TRANSPORTS:
            raise ValueError(f"transport must be one of {TRANSPORTS}")
        return value


class CloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="client_closed", max_length=64, pattern=r"^[a-z_]+$")


# ------------------------------------------------------------------- routes


def _load(db: Any, session_id: uuid.UUID) -> Any:
    row = service.get_session(db, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown realtime session")
    return row


@router.get("/providers")
async def list_realtime_providers(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    caps = [p.capabilities().to_dict() for p in runtime.providers.values()]
    health = runtime.health_check()
    return {"providers": caps, "count": len(caps), "selection": health["selection"],
            "preference_order": list(runtime.settings.voice_realtime_provider_preference)}


@router.post("/sessions", status_code=201)
async def create_session(request: Request, body: CreateSessionRequest) -> dict[str, Any]:
    runtime = _runtime(request)
    owner = _owner(request)
    trace_id = trace_id_var.get()
    try:
        provider, selection = runtime.select(language=body.language)
    except VoiceError as exc:
        _raise_http(exc)
    transport = body.transport or selection.transport
    if transport not in provider.capabilities().transports:
        raise HTTPException(status_code=422, detail={
            "error_class": "validation_error",
            "message": f"provider {provider.name!r} does not offer transport {transport!r}",
            "transports": list(provider.capabilities().transports)})

    def work() -> dict[str, Any]:
        with runtime.session() as db:
            _, _, payload = service.create_session(
                db, owner=owner, provider=provider, transport=transport,
                client_kind=body.client_kind, language=body.language,
                session_ttl_s=body.session_ttl_s or runtime.settings.voice_realtime_session_ttl_s,
                credential_ttl_s=runtime.settings.voice_realtime_credential_ttl_s,
                narration_session_id=body.narration_session_id, registry=runtime.registry,
                selection=selection.to_dict(), trace_id=trace_id,
            )
            return payload

    try:
        payload = await asyncio.to_thread(work)
    except VoiceError as exc:
        _raise_http(exc)
    logger.info("voice_realtime_session_created", session_id=payload["session_id"],
                provider=payload["provider"], transport=payload["transport"])
    return payload


@router.get("/sessions/{session_id}")
async def get_session_state(request: Request, session_id: uuid.UUID) -> dict[str, Any]:
    runtime = _runtime(request)

    def work() -> dict[str, Any]:
        with runtime.session() as db:
            return service.session_state(db, _load(db, session_id))

    return await asyncio.to_thread(work)


@router.post("/sessions/{session_id}/tool-calls")
async def relay_tool_call(
    request: Request, session_id: uuid.UUID, body: ToolCallRequest
) -> dict[str, Any]:
    runtime = _runtime(request)
    owner = _owner(request)
    trace_id = trace_id_var.get()
    _bind_loop(runtime)

    def work() -> dict[str, Any]:
        with runtime.session() as db:
            return service.handle_tool_call(
                db, _load(db, session_id), owner=owner, call_id=body.call_id, name=body.name,
                arguments=body.arguments, registry=runtime.registry, sideband=runtime.sideband,
                trace_id=trace_id,
            )

    try:
        result = await asyncio.to_thread(work)
    except VoiceError as exc:
        _raise_http(exc)
    logger.info("voice_realtime_tool_call", session_id=str(session_id), call_id=body.call_id,
                tool=body.name, status=result["status"], replayed=result["replayed"])
    return result


@router.post("/sessions/{session_id}/tool-calls/{call_id}/complete")
async def complete_tool_call(
    request: Request, session_id: uuid.UUID, call_id: str, body: ToolCompleteRequest
) -> dict[str, Any]:
    runtime = _runtime(request)
    trace_id = trace_id_var.get()
    _bind_loop(runtime)
    if body.result is None and body.error is None:
        raise HTTPException(status_code=422, detail="provide 'result' or 'error'")

    def work() -> dict[str, Any]:
        with runtime.session() as db:
            return service.complete_tool_call(
                db, _load(db, session_id), call_id=call_id, result=body.result,
                error=body.error, sideband=runtime.sideband, trace_id=trace_id,
            )

    try:
        result = await asyncio.to_thread(work)
    except VoiceError as exc:
        _raise_http(exc)
    return result


@router.post("/sessions/{session_id}/events")
async def report_events(
    request: Request, session_id: uuid.UUID, body: EventsRequest
) -> dict[str, Any]:
    runtime = _runtime(request)
    owner = _owner(request)
    trace_id = trace_id_var.get()
    events = [e.model_dump() for e in body.events]

    def work() -> dict[str, Any]:
        with runtime.session() as db:
            return service.record_client_events(
                db, _load(db, session_id), owner=owner, events=events, trace_id=trace_id,
            )

    try:
        return await asyncio.to_thread(work)
    except VoiceError as exc:
        _raise_http(exc)
    return {}  # pragma: no cover - _raise_http always raises


@router.post("/sessions/{session_id}/attach")
async def attach_session(
    request: Request, session_id: uuid.UUID, body: AttachRequest | None = None
) -> dict[str, Any]:
    runtime = _runtime(request)
    owner = _owner(request)
    trace_id = trace_id_var.get()
    body = body or AttachRequest()
    _bind_loop(runtime)

    def work() -> dict[str, Any]:
        with runtime.session() as db:
            row = _load(db, session_id)
            provider = runtime.provider(row.provider)
            if provider is None:
                raise VoiceError(VoiceErrorClass.CAPABILITY_MISSING,
                                 f"provider {row.provider!r} is not registered",
                                 provider=row.provider)
            return service.attach(
                db, row, owner=owner, provider=provider, registry=runtime.registry,
                sideband=runtime.sideband, client_kind=body.client_kind,
                transport=body.transport,
                credential_ttl_s=runtime.settings.voice_realtime_credential_ttl_s,
                trace_id=trace_id,
            )

    try:
        payload = await asyncio.to_thread(work)
    except VoiceError as exc:
        _raise_http(exc)
    logger.info("voice_realtime_session_attached", session_id=str(session_id),
                client_kind=payload["state"]["client_kind"], legs=payload["state"]["legs"])
    return payload


@router.post("/sessions/{session_id}/close")
async def close_session(
    request: Request, session_id: uuid.UUID, body: CloseRequest | None = None
) -> dict[str, Any]:
    runtime = _runtime(request)
    trace_id = trace_id_var.get()
    body = body or CloseRequest()

    def work() -> dict[str, Any]:
        with runtime.session() as db:
            return service.close_session(db, _load(db, session_id), reason=body.reason,
                                         trace_id=trace_id)

    result = await asyncio.to_thread(work)
    logger.info("voice_realtime_session_closed", session_id=str(session_id), reason=body.reason)
    return result


@router.get("/sessions/{session_id}/benchmark")
async def session_benchmark(request: Request, session_id: uuid.UUID) -> dict[str, Any]:
    runtime = _runtime(request)

    def work() -> dict[str, Any]:
        with runtime.session() as db:
            return service.benchmark_report(db, _load(db, session_id)).to_dict()

    return await asyncio.to_thread(work)


__all__ = ["router"]
