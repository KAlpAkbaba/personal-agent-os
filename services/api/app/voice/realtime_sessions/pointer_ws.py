"""ADR-0199 stage 2: the pointer WebSocket, ``GET
/v1/voice/realtime/sessions/{session_id}/pointer`` - the browser's live cursor/click
stream for the pinch-mouse, once ``operator.pointer_session`` (``tools_operator.py``)
has opened one.

**Owner-session-gated like the session's HTTP routes, on its own bare router.**
``app.voice.realtime_sessions.routes.router`` enforces authentication with
``dependencies=[Depends(require_owner_session)]`` - a dependency typed on Starlette's
``Request``, which FastAPI resolves against an HTTP scope. A websocket connection is a
different ASGI scope, and putting that same dependency on a websocket route is not a
supported way to gate one. ``app.broker.ws`` (the device WebSocket next door) hits the
same wall for an unrelated reason - a device authenticates with its own signed
challenge, never an owner token - and answers it the same way this module does:
authenticate BY HAND, inside the handler, against the SAME verification call the HTTP
dependency itself uses (``IdentityRuntime.service.verify`` - reused, not reinvented),
right after ``await websocket.accept()`` (``app.broker.ws.device_connect``'s own first
line), and close with a policy code on refusal.

**Where the credential travels differs from the HTTP routes, and has to.** The HTTP
surface reads ``Authorization: Bearer <token>``; a browser's native ``WebSocket``
constructor cannot set that header on the handshake. This route accepts the SAME bearer
token as a ``token`` query parameter (and, for a client that even so, still tests or
back-ends that CAN set it, the header too) - a deliberate, reversible choice, not a
second credential.

**Frame shapes (ADR-0199, binding - field names are not renamed here):**

- browser -> Cloud Core (this route): the first frame is always
  ``{"t":"hello","stream_token":"..."}``; after that, ``{"t":"move","dx":int,"dy":int,
  "seq":int}``, ``{"t":"button","button":"left"|"right","action":"down"|"up"|"click"}``,
  ``{"t":"end"}``.
- Cloud Core -> device (``app.broker.frames.pointer_stream_frame``, over the SAME
  ``BrokerRuntime.send_frame`` best-effort path a heartbeat/voice-sideband frame would
  use - never a ``device_commands`` row, never re-delivered):
  ``{"kind":"pointer_stream","session":"<id>","frames":[...]}``.

Ending: the client's own ``{"t":"end"}``, the socket closing, or 60s of silence all reach
the SAME :func:`app.voice.realtime_sessions.pointer_session.end_receipt` the tool's
``{"action":"end"}`` does - one receipt shape, three doors.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.broker import frames as broker_frames
from app.broker.audit import record_audit_event
from app.identity.tokens import parse_bearer
from app.logging import get_logger, trace_id_var
from app.voice.errors import VoiceError
from app.voice.realtime_sessions import pointer_session, service
from app.voice.realtime_sessions.models import RealtimeSessionRow

logger = get_logger("app.voice.realtime_sessions.pointer_ws")

router = APIRouter()

#: 4401 (the app-reserved 4000-4999 range): no valid owner session at all - distinct
#: from a policy refusal (1008) once a session DOES exist but the stream token does not
#: check out, so a client can tell "log in again" from "this stream is not yours" apart.
CLOSE_UNAUTHORIZED = 4401
CLOSE_POLICY = 1008
CLOSE_NORMAL = 1000

HELLO_TIMEOUT_S = 10.0
MAX_WS_FRAME_BYTES = 512

ACTION_POINTER_STREAM_OPENED = "voice_pointer_stream_opened"
ACTION_POINTER_STREAM_REFUSED = "voice_pointer_stream_refused"
ACTION_POINTER_STREAM_CLOSED = "voice_pointer_stream_closed"

_VALID_BUTTONS = ("left", "right")
_VALID_BUTTON_ACTIONS = ("down", "up", "click")


def _parse_frame(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _valid_delta(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) <= pointer_session.MAX_MOVE_DELTA
    )


async def _authenticate(websocket: WebSocket) -> Any | None:
    """The SAME check ``require_owner_session`` runs (unscoped, full owner authority) -
    against a token read from ``Authorization`` when a client can send one, else the
    ``token`` query parameter a browser's WebSocket constructor CAN set."""
    identity = getattr(websocket.app.state, "identity", None)
    if identity is None:
        return None
    token = parse_bearer(websocket.headers.get("Authorization")) or (
        websocket.query_params.get("token") or None
    )
    if not token:
        return None
    verdict = await asyncio.to_thread(identity.service.verify, token, trace_id=trace_id_var.get())
    if verdict.session is None or verdict.session.scopes:
        return None
    return verdict.session


@router.websocket("/v1/voice/realtime/sessions/{session_id}/pointer")
async def pointer_stream_ws(websocket: WebSocket, session_id: uuid.UUID) -> None:
    runtime = websocket.app.state.voice_realtime
    await websocket.accept()

    owner = await _authenticate(websocket)
    if owner is None:
        with contextlib.suppress(Exception):
            await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    def _load() -> RealtimeSessionRow | None:
        with runtime.session() as db:
            row = service.get_session(db, session_id)
            if row is None:
                return None
            try:
                service.require_live(db, row)
            except VoiceError:
                return None
            return row

    row = await asyncio.to_thread(_load)
    if row is None:
        with contextlib.suppress(Exception):
            await websocket.close(code=CLOSE_POLICY)
        return

    # The first frame is always hello + the single-use stream token this session's
    # operator.pointer_session {"action":"begin"} minted.
    try:
        raw_hello = await asyncio.wait_for(websocket.receive_text(), timeout=HELLO_TIMEOUT_S)
    except (TimeoutError, WebSocketDisconnect):
        with contextlib.suppress(Exception):
            await websocket.close(code=CLOSE_POLICY)
        return
    hello = _parse_frame(raw_hello)
    stream_token = str(hello.get("stream_token") or "") if hello.get("t") == "hello" else ""

    def _consume_token() -> dict[str, Any] | None:
        """Holds the token against the CURRENT record, marks it spent (single-use) and
        audits either outcome - one DB round trip, so a race with a second connect using
        the same token sees the first connect's ``used: true`` and is refused."""
        with runtime.session() as db:
            fresh = service.get_session(db, session_id)
            if fresh is None:
                return None
            record = pointer_session.record_from_context(fresh.context_json or {})
            now = service.utcnow()
            token_ok = pointer_session.token_matches(record, stream_token)
            if not token_ok or pointer_session.is_expired(record, now=now):
                record_audit_event(
                    db,
                    category=service.AUDIT_CATEGORY,
                    action=ACTION_POINTER_STREAM_REFUSED,
                    subject_ref=str(session_id),
                    device_id=fresh.device_id,
                    trace_id=trace_id_var.get(),
                    metadata={"reason": "bad_stream_token"},
                )
                db.commit()
                return None
            ctx = dict(fresh.context_json or {})
            pointer_session.mark_used(ctx, record)
            fresh.context_json = dict(ctx)
            record_audit_event(
                db,
                category=service.AUDIT_CATEGORY,
                action=ACTION_POINTER_STREAM_OPENED,
                subject_ref=str(session_id),
                device_id=fresh.device_id,
                trace_id=trace_id_var.get(),
                metadata={"pointer_session": record["session"]},
            )
            db.commit()
            return record

    record = await asyncio.to_thread(_consume_token)
    if record is None:
        with contextlib.suppress(Exception):
            await websocket.close(code=CLOSE_POLICY)
        return

    broker = runtime.live_sources().get("broker_runtime")
    device_action = runtime.live_sources().get("device_action")
    started_at = pointer_session.parse_iso(record.get("started_at")) or service.utcnow()

    def _connected_device_id() -> uuid.UUID | None:
        # Single-owner system: at most one interactive desktop is ever connected. The
        # device forwarded to is whichever one is live NOW, never the one recorded at
        # "begin" - a companion restart mid-stream must not forward into a dead socket.
        if broker is None:
            return None
        return next(iter(broker.connections), None)

    moves = 0
    buttons = 0
    dropped = 0
    pending: dict[str, Any] = {"dx": 0, "dy": 0, "buttons": []}
    pending_ready = asyncio.Event()
    closing = asyncio.Event()
    end_reason = "socket_closed"

    async def _sender() -> None:
        while True:
            await pending_ready.wait()
            batch: list[dict[str, Any]] = []
            if pending["dx"] or pending["dy"]:
                batch.append({"t": "move", "dx": pending["dx"], "dy": pending["dy"]})
            batch.extend(pending["buttons"])
            pending["dx"] = 0
            pending["dy"] = 0
            pending["buttons"] = []
            pending_ready.clear()
            if batch and broker is not None:
                target = _connected_device_id()
                if target is not None:
                    await broker.send_frame(
                        target,
                        broker_frames.pointer_stream_frame(session=record["session"], frames=batch),
                    )
            if closing.is_set() and not batch:
                return

    sender_task = asyncio.create_task(_sender())
    loop = asyncio.get_running_loop()
    window_starts: list[float] = []

    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=pointer_session.SILENCE_TIMEOUT_S
                )
            except TimeoutError:
                end_reason = "silence_timeout"
                break
            except WebSocketDisconnect:
                end_reason = "socket_closed"
                break
            if len(raw.encode("utf-8")) > MAX_WS_FRAME_BYTES:
                dropped += 1
                continue
            frame = _parse_frame(raw)
            kind = frame.get("t")
            # "end" is a control frame, not a stream-data frame: it must always be able
            # to end the session, even once the rate limit below has started dropping
            # move/button frames in the same instant - a rate-limited "end" would leave
            # the session open with the client already gone, waiting out the full 60s
            # silence timeout for no reason.
            if kind == "end":
                end_reason = "end_frame"
                break
            if kind not in ("move", "button"):
                dropped += 1
                continue
            now_mono = loop.time()
            window_starts = [t for t in window_starts if now_mono - t < 1.0]
            if len(window_starts) >= pointer_session.MAX_CLIENT_FRAMES_PER_S:
                dropped += 1
                continue
            window_starts.append(now_mono)
            if kind == "move":
                dx, dy = frame.get("dx"), frame.get("dy")
                if not _valid_delta(dx) or not _valid_delta(dy):
                    dropped += 1
                    continue
                pending["dx"] += int(dx)
                pending["dy"] += int(dy)
                moves += 1
                pending_ready.set()
            else:  # "button"
                button, act = frame.get("button"), frame.get("action")
                if button not in _VALID_BUTTONS or act not in _VALID_BUTTON_ACTIONS:
                    dropped += 1
                    continue
                pending["buttons"].append({"t": "button", "button": button, "action": act})
                buttons += 1
                pending_ready.set()
    finally:
        closing.set()
        pending_ready.set()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(sender_task, timeout=2.0)

        def _end() -> dict[str, Any]:
            with runtime.session() as db:
                receipt = pointer_session.end_receipt(
                    db,
                    session_id=session_id,
                    action_id=str(uuid.uuid4()),
                    record=record,
                    device_action=device_action,
                    moves=moves,
                    buttons=buttons,
                    dropped=dropped,
                    started_at=started_at,
                    now=service.utcnow(),
                )
                fresh = service.get_session(db, session_id)
                if fresh is not None:
                    ctx = dict(fresh.context_json or {})
                    current = pointer_session.record_from_context(ctx)
                    # Never clear a DIFFERENT (newer) pointer session that "begin"
                    # opened while this socket was still finishing up.
                    if current is not None and current.get("session") == record.get("session"):
                        pointer_session.clear(ctx)
                        fresh.context_json = dict(ctx)
                server = receipt.get("observed_after", {}).get("server", {})
                record_audit_event(
                    db,
                    category=service.AUDIT_CATEGORY,
                    action=ACTION_POINTER_STREAM_CLOSED,
                    subject_ref=str(session_id),
                    device_id=row.device_id,
                    trace_id=trace_id_var.get(),
                    metadata={"reason": end_reason, **(server if isinstance(server, dict) else {})},
                )
                db.commit()
            return receipt

        try:
            await asyncio.to_thread(_end)
        except Exception:  # noqa: BLE001 - the socket still has to close either way
            logger.exception("pointer_stream_end_failed", session_id=str(session_id))
        with contextlib.suppress(Exception):
            await websocket.close(code=CLOSE_NORMAL)


__all__ = ["router"]
