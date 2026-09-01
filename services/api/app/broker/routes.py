"""Broker REST surface (DEVICE_PROTOCOL.md §8).

M9/ADR-0027 posture: every endpoint here requires an owner bearer session, with
ONE deliberate exception — `POST /enroll`. That endpoint is authenticated by the
single-use, short-TTL enrollment token that an authenticated owner minted at
`POST /enrollment-tokens`; the enrolling agent has that token and, by
construction, no owner session yet. It is the same shape as the WS handshake
exception: a surface with its own credential, rooted in an owner action, rather
than an open one. Minting still additionally requires a loopback peer.
"""

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.broker import frames, service
from app.broker.frames import CAPABILITY_PATTERN
from app.broker.models import DEVICE_STATUS_REVOKED, Device, DeviceCommand
from app.broker.runtime import BrokerRuntime
from app.broker.ws import deliver_command
from app.identity.dependencies import require_owner_session
from app.logging import get_logger, trace_id_var

logger = get_logger("app.broker.routes")

router = APIRouter(prefix="/v1/devices")

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _runtime(request: Request) -> BrokerRuntime:
    return request.app.state.broker


def _require_loopback(request: Request) -> None:
    client = request.client
    host = client.host if client else None
    if host is not None and host not in _LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail="loopback only in dev")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------ enrollment


class EnrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=8, max_length=256)
    name: str = Field(min_length=1, max_length=200)
    platform: str = Field(min_length=1, max_length=64)
    public_key_spki_b64: str = Field(min_length=32, max_length=4096)
    capabilities: list[str] = Field(default_factory=list, max_length=128)


@router.post(
    "/enrollment-tokens",
    status_code=201,
    dependencies=[Depends(require_owner_session)],
)
async def create_enrollment_token(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    runtime = _runtime(request)

    def issue() -> tuple[str, datetime]:
        with runtime.session() as db:
            return service.issue_enrollment_token(
                db,
                ttl_s=runtime.settings.broker_enrollment_token_ttl_s,
                trace_id=trace_id_var.get(),
            )


    token, expires_at = await asyncio.to_thread(issue)
    logger.info("broker_enrollment_token_issued")
    return {"token": token, "expires_at": _iso(expires_at)}


# Intentionally NOT owner-session protected: see the module docstring. The
# enrollment token is the credential here, and it was minted by the owner.
@router.post("/enroll", status_code=201)
async def enroll(request: Request, body: EnrollRequest) -> dict[str, Any]:
    runtime = _runtime(request)
    for cap in body.capabilities:
        if not re.match(CAPABILITY_PATTERN, cap):
            raise HTTPException(status_code=422, detail=f"invalid capability name: {cap!r}")

    def do_enroll() -> uuid.UUID:
        with runtime.session() as db:
            if not service.consume_enrollment_token(db, body.token):
                raise HTTPException(
                    status_code=400, detail="invalid, expired or already used token"
                )
            try:
                device = service.enroll_device(
                    db,
                    name=body.name,
                    platform=body.platform,
                    public_key_spki_b64=body.public_key_spki_b64,
                    capabilities=body.capabilities,
                    trace_id=trace_id_var.get(),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return device.id


    device_id = await asyncio.to_thread(do_enroll)
    runtime.counters["devices_enrolled"] += 1
    logger.info("broker_device_enrolled", device_id=str(device_id), platform=body.platform)
    return {"device_id": str(device_id)}


# --------------------------------------------------------------------- devices


def _device_payload(runtime: BrokerRuntime, device: Device) -> dict[str, Any]:
    if device.status == DEVICE_STATUS_REVOKED:
        computed = "revoked"
    elif runtime.is_online(device.id):
        computed = "online"
    else:
        computed = "offline"
    return {
        "device_id": str(device.id),
        "name": device.name,
        "platform": device.platform,
        "status": computed,
        "capabilities": device.capabilities_json,
        "enrolled_at": _iso(device.enrolled_at),
        "last_seen_at": _iso(device.last_seen_at),
        "revoked_at": _iso(device.revoked_at),
    }


@router.get("", dependencies=[Depends(require_owner_session)])
async def get_devices(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)


    def load() -> list[Device]:
        with runtime.session() as db:
            return service.list_devices(db)

    devices = await asyncio.to_thread(load)
    return {"devices": [_device_payload(runtime, d) for d in devices]}


@router.post("/{device_id}/revoke", dependencies=[Depends(require_owner_session)])
async def revoke_device(request: Request, device_id: uuid.UUID) -> dict[str, Any]:
    runtime = _runtime(request)


    def revoke() -> Device | None:
        with runtime.session() as db:
            return service.revoke_device(db, device_id, trace_id=trace_id_var.get())

    device = await asyncio.to_thread(revoke)
    if device is None:
        raise HTTPException(status_code=404, detail="unknown device")
    await runtime.close_device_connection(device_id, code=1008)

    # M9 acceptance: "device revocation invalidates session". Killing the WS is
    # not enough — a revoked phone still holding a bearer token could keep
    # driving the REST API. Every session bound to this device dies with it.
    identity = getattr(request.app.state, "identity", None)
    sessions_revoked = 0
    if identity is not None:
        sessions_revoked = await asyncio.to_thread(
            lambda: identity.service.revoke_sessions_for_device(
                device_id, reason="device_revoked", trace_id=trace_id_var.get()
            )
        )

    runtime.counters["devices_revoked"] += 1
    logger.info(
        "broker_device_revoked",
        device_id=str(device_id),
        sessions_revoked=sessions_revoked,
    )
    return {
        "device_id": str(device_id),
        "status": "revoked",
        "sessions_revoked": sessions_revoked,
    }


# -------------------------------------------------------------------- commands


MAX_COMMAND_PAYLOAD_BYTES = 64 * 1024


class CreateCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str = Field(pattern=CAPABILITY_PATTERN)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    timeout_s: float | None = Field(default=None, gt=0, le=86400)

    @field_validator("payload")
    @classmethod
    def _payload_size_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        size = len(json.dumps(value, separators=(",", ":"), default=str).encode())
        if size > MAX_COMMAND_PAYLOAD_BYTES:
            raise ValueError(
                f"payload too large: {size} bytes (max {MAX_COMMAND_PAYLOAD_BYTES})"
            )
        return value


def _command_payload(command: DeviceCommand) -> dict[str, Any]:
    error = None
    if command.error_class is not None:
        error = {"class": command.error_class, "message": command.error_message or ""}
    return {
        "command_id": str(command.id),
        "device_id": str(command.device_id),
        "capability": command.capability,
        "payload": command.payload_json,
        "idempotency_key": command.idempotency_key,
        "status": command.status,
        "result": command.result_json,
        "error": error,
        "trace_id": command.trace_id,
        "created_at": _iso(command.created_at),
        "expires_at": _iso(command.expires_at),
        "delivered_at": _iso(command.delivered_at),
        "terminal_at": _iso(command.terminal_at),
    }


@router.post(
    "/{device_id}/commands",
    status_code=202,
    dependencies=[Depends(require_owner_session)],
)
async def create_command(
    request: Request, device_id: uuid.UUID, body: CreateCommandRequest
) -> JSONResponse:
    runtime = _runtime(request)
    trace_id = trace_id_var.get() or uuid.uuid4().hex
    idempotency_key = body.idempotency_key or str(uuid.uuid4())
    timeout_s = body.timeout_s or runtime.settings.broker_default_command_timeout_s


    def create() -> tuple[DeviceCommand, bool]:
        with runtime.session() as db:
            device = service.get_device(db, device_id)
            if device is None:
                raise HTTPException(status_code=404, detail="unknown device")
            if device.status == DEVICE_STATUS_REVOKED:
                raise HTTPException(status_code=409, detail="device is revoked")
            return service.create_command(
                db,
                device_id=device_id,
                capability=body.capability,
                payload=body.payload,
                idempotency_key=idempotency_key,
                timeout_s=timeout_s,
                trace_id=trace_id,
            )

    command, created = await asyncio.to_thread(create)
    if created:
        runtime.counters["commands_created"] += 1
        logger.info(
            "broker_command_created",
            command_id=str(command.id),
            device_id=str(device_id),
            capability=command.capability,
            command_trace_id=command.trace_id,
        )
        # Live delivery when the device is connected right now.
        connection = runtime.get_connection(device_id)
        if connection is not None:
            await deliver_command(runtime, connection, command)
    else:
        runtime.counters["commands_deduplicated"] += 1
        logger.info(
            "broker_command_deduplicated",
            command_id=str(command.id),
            device_id=str(device_id),
            idempotency_key=idempotency_key,
            command_trace_id=command.trace_id,
        )
    return JSONResponse(
        status_code=202,
        content={"command_id": str(command.id), "status": command.status},
    )


@router.get(
    "/{device_id}/commands/{command_id}",
    dependencies=[Depends(require_owner_session)],
)
async def get_command(
    request: Request, device_id: uuid.UUID, command_id: uuid.UUID
) -> dict[str, Any]:
    runtime = _runtime(request)


    def load() -> DeviceCommand | None:
        with runtime.session() as db:
            return service.get_command(db, device_id, command_id)

    command = await asyncio.to_thread(load)
    if command is None:
        raise HTTPException(status_code=404, detail="unknown command")
    return _command_payload(command)


@router.post(
    "/{device_id}/commands/{command_id}/cancel",
    dependencies=[Depends(require_owner_session)],
)
async def cancel_command(
    request: Request, device_id: uuid.UUID, command_id: uuid.UUID
) -> dict[str, Any]:
    runtime = _runtime(request)


    def cancel() -> tuple[str, DeviceCommand | None]:
        with runtime.session() as db:
            return service.cancel_command(
                db, device_id=device_id, command_id=command_id, trace_id=trace_id_var.get()
            )

    outcome, command = await asyncio.to_thread(cancel)
    if outcome == "not_found" or command is None:
        raise HTTPException(status_code=404, detail="unknown command")

    cancel_forwarded = False
    if outcome == "forward":
        cancel_forwarded = await runtime.send_frame(
            device_id, frames.cancel_frame(str(command_id))
        )
    runtime.counters["commands_cancel_requested"] += 1
    logger.info(
        "broker_command_cancel",
        command_id=str(command_id),
        device_id=str(device_id),
        outcome=outcome,
        cancel_forwarded=cancel_forwarded,
        command_trace_id=command.trace_id,
    )
    return {
        "command_id": str(command_id),
        "status": command.status,
        "cancel_forwarded": cancel_forwarded,
    }


# ----------------------------------------------------------------------- stats


@router.get("/stats", dependencies=[Depends(require_owner_session)])
async def get_stats(request: Request) -> dict[str, Any]:
    return _runtime(request).stats()
