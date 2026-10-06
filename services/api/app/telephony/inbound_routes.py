"""The inbound line's routes: Twilio's two webhooks and its media WebSocket.

NOT owner-gated, and NOT mounted by ``create_app`` yet - the binding card (ADR) adds it next to
the telephony router, puts the two POSTs into ``test_identity_enforcement.EXPECTED_OPEN`` and the
socket into its WebSocket set. Twilio holds no owner session: the webhooks' authority is
Twilio's signature (403 with an empty body otherwise), the socket's is the one-time bridge token.

The signature is computed over the configured PUBLIC base url plus the request path - the app
sees ``http://<bind ip>:8001/...`` behind the edge proxy, which is not what Twilio signed.
Nothing is written while the phone rings: the call's one ledger row is finalize's.
"""

from __future__ import annotations

import asyncio
from typing import Final

from fastapi import APIRouter, Request, Response, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.logging import get_logger
from app.telephony.inbound_bridge import CLOSE_NORMAL, InboundLine
from app.telephony.inbound_twilio import (
    MEDIA_PATH,
    REFUSE_TEXT_TR,
    SIGNATURE_HEADER,
    STATUS_PATH,
    VOICE_PATH,
    port_variant,
    stream_url,
)

logger = get_logger("app.telephony.inbound_routes")

STATE_ATTR: Final[str] = "telephony_inbound"
XML: Final[str] = "application/xml"

router = APIRouter(tags=["telephony-inbound"])


def _line(app: object) -> InboundLine:
    return getattr(app.state, STATE_ATTR)  # type: ignore[attr-defined]


async def _signed_form(request: Request, line: InboundLine) -> dict[str, str] | None:
    form = await request.form()
    fields = [(str(k), str(val)) for k, val in form.multi_items()]
    url = line.settings.public_base_url.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    header = request.headers.get(SIGNATURE_HEADER)
    if line.provider.validate_request(url, fields, header):
        return dict(fields)
    variant = port_variant(url)
    if variant is not None and line.provider.validate_request(variant, fields, header):
        # Twilio's own validator would have accepted this; the contract does not. One line
        # tells the first real call's 403 apart from a forgery.
        logger.warning("telephony_inbound_signature_port_variant", path=request.url.path)
    else:
        logger.warning("telephony_inbound_signature_refused", path=request.url.path)
    return None


@router.post(VOICE_PATH)
async def inbound_voice(request: Request) -> Response:
    line = _line(request.app)
    fields = await _signed_form(request, line)
    if fields is None:
        return Response(status_code=403)
    call_sid = fields.get("CallSid", "")
    caller = fields.get("From", "")
    used = await asyncio.to_thread(line.recorder.seconds_used_today, line.now())
    admission = line.admit(call_sid, caller, used_seconds=used)
    if admission.token is None:
        logger.info("telephony_inbound_refused", call_sid=call_sid, reason=admission.reason)
        return Response(line.provider.refuse(REFUSE_TEXT_TR), media_type=XML)
    logger.info("telephony_inbound_answering", call_sid=call_sid, max_seconds=admission.max_seconds)
    body = line.provider.answer(
        line.settings.kvkk_text, stream_url(line.settings.public_base_url), admission.token
    )
    return Response(body, media_type=XML)


@router.post(STATUS_PATH)
async def inbound_status(request: Request) -> Response:
    line = _line(request.app)
    fields = await _signed_form(request, line)
    if fields is None:
        return Response(status_code=403)
    raw_duration = fields.get("CallDuration", "")
    duration = int(raw_duration) if raw_duration.isdigit() else None
    await line.status_callback(fields.get("CallSid", ""), duration)
    return Response(status_code=204)


class _StarletteTwilioSocket:
    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._closed = False

    async def receive_text(self) -> str | None:
        if self._closed:
            return None
        try:
            return await self._ws.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            self._closed = True
            return None

    async def send_text(self, text: str) -> None:
        if self._closed:
            return
        try:
            await self._ws.send_text(text)
        except (WebSocketDisconnect, RuntimeError):
            self._closed = True

    async def close(self, code: int = CLOSE_NORMAL) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._ws.close(code)
        except RuntimeError:
            return


@router.websocket(MEDIA_PATH)
async def inbound_media(websocket: WebSocket) -> None:
    line = _line(websocket.app)
    await websocket.accept()
    await line.bridge(_StarletteTwilioSocket(websocket)).run()


__all__ = ["STATE_ATTR", "router"]
