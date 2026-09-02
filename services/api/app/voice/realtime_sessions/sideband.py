"""Sideband push seam (M12 spec §1, §4 step 4).

Cloud Core -> client messages that are not the media path: ``plan_changed``,
``tool_progress``, ``tool_completed``, ``narration_cursor``, ``say``. The
transport is the EXISTING authenticated device WebSocket (``BrokerRuntime``);
the service only knows this Protocol, and a recording fake drives the tests.

A push that cannot be delivered (no device leg, a web client, the socket is
down) is not lost: the service queues it on the session record and a client
receives the backlog on ``attach`` or with its next ``/events`` response.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from app.broker.frames import (
    MAX_VOICE_SIDEBAND_FRAME_BYTES,
    VOICE_SIDEBAND_EVENTS,
    VOICE_SIDEBAND_FRAME_TYPE,
    voice_sideband_frame_size,
)
from app.logging import get_logger

logger = get_logger("app.voice.realtime_sessions.sideband")

# The frame type and event vocabulary are the device protocol's (ADR-0039):
# packages/schemas/device-protocol.schema.json $defs/voice_sideband.
SIDEBAND_FRAME_TYPE = VOICE_SIDEBAND_FRAME_TYPE

SB_PLAN_CHANGED = "plan_changed"
SB_TOOL_PROGRESS = "tool_progress"
SB_TOOL_COMPLETED = "tool_completed"
SB_NARRATION_CURSOR = "narration_cursor"
SB_SAY = "say"
SB_LEG_CLOSED = "leg_closed"  # the previous client's media leg was superseded
SIDEBAND_EVENTS = (
    SB_PLAN_CHANGED, SB_TOOL_PROGRESS, SB_TOOL_COMPLETED, SB_NARRATION_CURSOR, SB_SAY,
    SB_LEG_CLOSED,
)
if set(SIDEBAND_EVENTS) != set(VOICE_SIDEBAND_EVENTS):  # pragma: no cover - import-time guard
    raise RuntimeError("sideband vocabulary drifted from the device protocol frame definition")


def sideband_frame(session_id: uuid.UUID, event: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event not in SIDEBAND_EVENTS:
        raise ValueError(f"unknown sideband event {event!r}")
    return {
        "type": SIDEBAND_FRAME_TYPE,
        "session_id": str(session_id),
        "event": event,
        "payload": dict(payload),
        "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


@runtime_checkable
class SidebandPusher(Protocol):
    def push(self, *, device_id: uuid.UUID | None, frame: dict[str, Any]) -> bool: ...


class RecordingSideband:
    """Test double: records every frame; ``deliver`` decides the return value."""

    def __init__(self, *, deliver: bool = True) -> None:
        self.frames: list[tuple[uuid.UUID | None, dict[str, Any]]] = []
        self.deliver = deliver

    def push(self, *, device_id: uuid.UUID | None, frame: dict[str, Any]) -> bool:
        self.frames.append((device_id, frame))
        return self.deliver and device_id is not None

    def events(self) -> list[str]:
        return [frame["event"] for _, frame in self.frames]


class BrokerSideband:
    """Push over the device broker's live WebSocket. The service runs in a
    worker thread (``asyncio.to_thread``), so the send is scheduled onto the
    application's event loop, which the route binds before the work starts."""

    def __init__(self, broker: Any) -> None:
        self._broker = broker
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def push(self, *, device_id: uuid.UUID | None, frame: dict[str, Any]) -> bool:
        if device_id is None or self._loop is None or not self._broker.is_online(device_id):
            return False
        size = voice_sideband_frame_size(frame)
        if size > MAX_VOICE_SIDEBAND_FRAME_BYTES:
            # The Device Service would drop it at the pipe; refusing here keeps it on the
            # session's pending queue, where the HTTP paths (events/attach) have no such bound.
            logger.warning("voice_sideband_frame_oversize", device_id=str(device_id),
                           sideband_event=frame.get("event"), frame_bytes=size,
                           limit=MAX_VOICE_SIDEBAND_FRAME_BYTES)
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._broker.send_frame(device_id, frame), self._loop
            )
            return bool(future.result(timeout=2.0))
        except Exception as exc:  # noqa: BLE001 - a dead socket must not fail the tool call
            logger.warning("voice_sideband_push_failed", device_id=str(device_id),
                           sideband_event=frame.get("event"), error=f"{type(exc).__name__}: {exc}")
            return False


__all__ = [
    "SB_LEG_CLOSED",
    "SB_NARRATION_CURSOR",
    "SB_PLAN_CHANGED",
    "SB_SAY",
    "SB_TOOL_COMPLETED",
    "SB_TOOL_PROGRESS",
    "SIDEBAND_EVENTS",
    "SIDEBAND_FRAME_TYPE",
    "BrokerSideband",
    "RecordingSideband",
    "SidebandPusher",
    "sideband_frame",
]
