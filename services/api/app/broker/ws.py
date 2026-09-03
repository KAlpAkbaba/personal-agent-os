"""WS /v1/devices/connect — device agent session (DEVICE_PROTOCOL.md §3-5).

Handshake: hello -> challenge -> auth -> welcome. Unknown device, revoked
device and bad signature all fail identically (auth_error, close) so the
failure never reveals which check failed: the broker always issues a
challenge and always runs a signature verification (against a process-local
dummy key when the device is unknown) before rejecting.

Malformed frames get a protocol `error` frame (validation_error) and the
connection stays open. Liveness: any frame refreshes the deadline; silence
for liveness_factor x heartbeat_interval closes the session and marks the
device offline.
"""

import asyncio
import base64
import contextlib
import uuid
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.broker import frames, service
from app.broker.frames import (
    PROTOCOL_VERSION,
    AgentErrorFrame,
    AuthFrame,
    CommandAckFrame,
    HeartbeatFrame,
    HelloFrame,
    InboundFrame,
)
from app.broker.models import DEVICE_STATUS_ENROLLED, Device, DeviceCommand
from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.broker.security import generate_nonce, verify_device_signature
from app.broker.state import TransitionDecision
from app.logging import get_logger

logger = get_logger("app.broker.ws")

router = APIRouter()

# Dummy P-256 key used to equalize the auth path for unknown devices.
_DUMMY_SPKI_B64 = base64.b64encode(
    ec.generate_private_key(ec.SECP256R1())
    .public_key()
    .public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
).decode("ascii")


class _HandshakeFailure(Exception):
    pass


async def _receive_frame(
    websocket: WebSocket, runtime: BrokerRuntime, timeout_s: float
) -> InboundFrame:
    """Receive the next well-formed frame; answer malformed ones and keep waiting.

    Raises TimeoutError when timeout_s elapses without a well-formed frame and
    WebSocketDisconnect when the peer goes away.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=remaining)
        frame, error = frames.parse_frame(raw)
        if error is not None:
            runtime.counters["malformed_frames"] += 1
            logger.warning("broker_malformed_frame", detail=error["error"]["message"][:200])
            await websocket.send_json(error)
            continue
        assert frame is not None
        return frame


async def _handshake(
    websocket: WebSocket, runtime: BrokerRuntime
) -> tuple[Device, HelloFrame]:
    """Run hello/challenge/auth. Raises _HandshakeFailure after auth_error."""
    timeout_s = runtime.settings.broker_handshake_timeout_s

    async def fail() -> None:
        runtime.counters["auth_failures"] += 1
        with contextlib.suppress(Exception):
            await websocket.send_json(
                frames.error_frame("auth_error", "authentication failed")
            )
        with contextlib.suppress(Exception):
            await websocket.close(code=1008)
        raise _HandshakeFailure

    hello = await _receive_frame(websocket, runtime, timeout_s)
    if not isinstance(hello, HelloFrame):
        await fail()
    assert isinstance(hello, HelloFrame)
    if hello.protocol_version != PROTOCOL_VERSION:
        logger.warning(
            "broker_protocol_version_rejected",
            device_id=str(hello.device_id),
            protocol_version=hello.protocol_version,
        )
        await fail()

    device: Device | None = await asyncio.to_thread(_load_device, runtime, hello.device_id)
    device_valid = device is not None and device.status == DEVICE_STATUS_ENROLLED

    nonce = generate_nonce()
    await websocket.send_json(
        frames.challenge_frame(base64.b64encode(nonce).decode("ascii"))
    )

    auth = await _receive_frame(websocket, runtime, timeout_s)
    if not isinstance(auth, AuthFrame):
        await fail()
    assert isinstance(auth, AuthFrame)

    spki = device.public_key_spki_b64 if device_valid and device else _DUMMY_SPKI_B64
    verified = await asyncio.to_thread(
        verify_device_signature, spki, auth.signature, nonce, str(hello.device_id)
    )
    if not (device_valid and verified):
        logger.warning("broker_auth_rejected", device_id=str(hello.device_id))
        await fail()
    assert device is not None
    return device, hello


def _load_device(runtime: BrokerRuntime, device_id: uuid.UUID) -> Device | None:
    with runtime.session() as db:
        return service.get_device(db, device_id)


def _command_to_frame(command: DeviceCommand) -> dict[str, Any]:
    return frames.command_frame(
        command_id=str(command.id),
        idempotency_key=command.idempotency_key,
        capability=command.capability,
        payload=command.payload_json,
        expires_at=command.expires_at,
        trace_id=command.trace_id,
    )


async def deliver_command(
    runtime: BrokerRuntime, connection: DeviceConnection, command: DeviceCommand
) -> bool:
    """Send a command frame over an active connection and record delivery."""
    try:
        await connection.send_json(_command_to_frame(command))
    except Exception as exc:  # noqa: BLE001 - socket may die mid-send
        logger.warning(
            "broker_command_delivery_failed",
            command_id=str(command.id),
            device_id=str(connection.device_id),
            command_trace_id=command.trace_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return False
    await asyncio.to_thread(_mark_delivered, runtime, command.id)
    runtime.counters["commands_delivered"] += 1
    logger.info(
        "broker_command_delivered",
        command_id=str(command.id),
        device_id=str(connection.device_id),
        command_trace_id=command.trace_id,
    )
    return True


def _mark_delivered(runtime: BrokerRuntime, command_id: uuid.UUID) -> None:
    with runtime.session() as db:
        service.mark_command_delivered(db, command_id)


async def _redeliver_pending(runtime: BrokerRuntime, connection: DeviceConnection) -> None:
    def load() -> list[DeviceCommand]:
        with runtime.session() as db:
            return service.deliverable_commands(db, connection.device_id)

    for command in await asyncio.to_thread(load):
        await deliver_command(runtime, connection, command)


async def _handle_command_ack(
    runtime: BrokerRuntime, connection: DeviceConnection, ack: CommandAckFrame
) -> None:
    def apply() -> tuple[TransitionDecision | None, str | None, str | None]:
        with runtime.session() as db:
            decision, command = service.apply_command_ack(
                db,
                device_id=connection.device_id,
                command_id=ack.command_id,
                ack_status=ack.status,
                result=ack.result,
                error_class=ack.error.error_class if ack.error else None,
                error_message=ack.error.message if ack.error else None,
            )
            return (
                decision,
                command.status if command else None,
                command.trace_id if command else None,
            )

    decision, status, trace_id = await asyncio.to_thread(apply)
    if decision is None:
        runtime.counters["acks_unknown_command"] += 1
        await connection.send_json(
            frames.error_frame(
                "validation_error",
                "unknown command for this device",
                command_id=str(ack.command_id),
            )
        )
        return
    log = logger.info if decision is TransitionDecision.APPLY else logger.debug
    log(
        "broker_command_ack",
        command_id=str(ack.command_id),
        device_id=str(connection.device_id),
        ack_status=ack.status,
        decision=decision.value,
        stored_status=status,
        command_trace_id=trace_id,
    )
    if decision is TransitionDecision.APPLY:
        runtime.counters["acks_applied"] += 1
    elif decision is TransitionDecision.CONFLICT:
        runtime.counters["acks_conflict"] += 1
        await connection.send_json(
            frames.error_frame(
                "validation_error",
                f"conflicting terminal ack {ack.status!r} (recorded {status!r})",
                command_id=str(ack.command_id),
            )
        )
    else:
        runtime.counters["acks_ignored"] += 1


@router.websocket("/v1/devices/connect")
async def device_connect(websocket: WebSocket) -> None:
    runtime: BrokerRuntime = websocket.app.state.broker
    settings = runtime.settings
    await websocket.accept()

    try:
        device, hello = await _handshake(websocket, runtime)
    except _HandshakeFailure:
        return
    except (TimeoutError, WebSocketDisconnect):
        with contextlib.suppress(Exception):
            await websocket.close(code=1002)
        return

    device_id = device.id

    def start_session() -> uuid.UUID:
        with runtime.session() as db:
            client = websocket.client
            row = service.start_device_session(
                db,
                device_id=device_id,
                software_version=hello.software_version,
                connection_metadata={
                    "remote_addr": client.host if client else None,
                    "protocol_version": hello.protocol_version,
                    "capabilities": hello.capabilities,
                },
                trace_id=None,
            )
            # M13/app.devices §8: every hello is authoritative for what THIS
            # device can do and which build it runs right now (DeviceCapabilities,
            # DeviceHealth.software_version) — refreshed on every handshake, not
            # just at enrollment.
            service.apply_hello(
                db,
                device_id,
                capabilities=hello.capabilities,
                software_version=hello.software_version,
            )
            return row.id

    session_id = await asyncio.to_thread(start_session)
    connection = DeviceConnection(
        device_id=device_id, session_id=session_id, websocket=websocket
    )

    # Replace any lingering previous connection for this device.
    previous = runtime.get_connection(device_id)
    if previous is not None:
        with contextlib.suppress(Exception):
            await previous.websocket.close(code=1012)
    runtime.register_connection(connection)
    logger.info(
        "broker_session_started", device_id=str(device_id), session_id=str(session_id)
    )

    await connection.send_json(
        frames.welcome_frame(str(session_id), settings.broker_heartbeat_interval_s)
    )

    idle_timeout = settings.broker_heartbeat_interval_s * settings.broker_liveness_factor
    end_reason = "disconnect"
    try:
        await _redeliver_pending(runtime, connection)
        while True:
            try:
                frame = await _receive_frame(websocket, runtime, idle_timeout)
            except TimeoutError:
                end_reason = "liveness_timeout"
                logger.warning("broker_liveness_timeout", device_id=str(device_id))
                with contextlib.suppress(Exception):
                    await websocket.close(code=1001)
                break
            await asyncio.to_thread(_touch, runtime, device_id)
            if isinstance(frame, HeartbeatFrame):
                await connection.send_json(frames.heartbeat_ack_frame(frame.seq))
            elif isinstance(frame, CommandAckFrame):
                await _handle_command_ack(runtime, connection, frame)
            elif isinstance(frame, AgentErrorFrame):
                logger.warning(
                    "broker_agent_error_frame",
                    device_id=str(device_id),
                    error_class=frame.error.error_class,
                    message=frame.error.message[:200],
                    command_id=str(frame.command_id) if frame.command_id else None,
                )
            else:
                # Well-formed but not valid mid-session (e.g. second hello).
                runtime.counters["malformed_frames"] += 1
                await connection.send_json(
                    frames.error_frame(
                        "validation_error", f"unexpected frame type {frame.type!r}"
                    )
                )
    except WebSocketDisconnect:
        end_reason = "disconnect"
    finally:
        runtime.unregister_connection(connection)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(_end_session, runtime, session_id, device_id, end_reason)
        logger.info(
            "broker_session_ended",
            device_id=str(device_id),
            session_id=str(session_id),
            reason=end_reason,
        )


def _touch(runtime: BrokerRuntime, device_id: uuid.UUID) -> None:
    with runtime.session() as db:
        service.touch_last_seen(db, device_id)


def _end_session(
    runtime: BrokerRuntime, session_id: uuid.UUID, device_id: uuid.UUID, reason: str
) -> None:
    with runtime.session() as db:
        service.end_device_session(
            db, session_id=session_id, device_id=device_id, reason=reason, trace_id=None
        )
