"""REST surface for the measurement recordings (team/plans/measure-recordings-api-adr.md).

- GET    /v1/voice/measurement                                   sentences, rules, recordings
- PUT    /v1/voice/measurement/recordings/{place}/{index}        one reading ("tekrar" replaces)
- GET    /v1/voice/measurement/recordings/{place}/{index}/audio  the WAV bytes
- DELETE /v1/voice/measurement/recordings/{place}/{index}        one reading gone
- DELETE /v1/voice/measurement/recordings                        every reading gone ("sil")
- GET    /v1/voice/measurement/manifest[?place=ev|ofis]          the measuring tool's manifest

All owner-gated. Every refusal is ``{detail: {code, message}}`` with a Turkish message. The
PUT body is JSON with base64 (the API has no multipart parser); it is read under a byte bound
and refused before it is parsed or decoded when it is over it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.identity.dependencies import require_owner_session
from app.voice.measurement import service
from app.voice.stt_compare import OWNER_SENTENCES

router = APIRouter(
    prefix="/v1/voice/measurement",
    tags=["voice"],
    dependencies=[Depends(require_owner_session)],
)

#: The base64 of the largest recording, plus room for the transcript and the capture settings.
MAX_BODY_BYTES = service.MAX_BASE64_CHARS + 16_384


def _refuse(refusal: service.Refusal) -> HTTPException:
    return HTTPException(refusal.status, {"code": refusal.code, "message": refusal.message})


async def _run[T](request: Request, call: Callable[[service.Recordings], T]) -> T:
    def work() -> T:
        try:
            store = request.app.state.artifacts.store
        except Exception as exc:  # noqa: BLE001 - a store that cannot be built is a store fault
            raise service.StoreUnavailable("build") from exc
        return call(service.Recordings(store))

    try:
        return await asyncio.to_thread(work)
    except service.Refusal as refusal:
        raise _refuse(refusal) from refusal
    except service.StoreUnavailable as fault:
        raise HTTPException(
            503,
            {
                "code": "store_unavailable",
                "message": "Kayıt deposuna şu an ulaşılamıyor; biraz sonra yeniden dene.",
            },
        ) from fault


async def _bounded_json(request: Request) -> dict[str, Any]:
    too_large = service.Refusal(
        "audio_too_large",
        f"Kayıt çok büyük; en çok {service.MAX_AUDIO_BYTES} bayt olabilir.",
        status=413,
    )
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _refuse(too_large)
    received = bytearray()
    async for chunk in request.stream():
        received += chunk
        if len(received) > MAX_BODY_BYTES:
            raise _refuse(too_large)
    try:
        body = json.loads(received)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise _refuse(service.Refusal("invalid_body", "İstek gövdesi bir JSON nesnesi olmalı."))
    return body


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("")
async def get_measurement(request: Request) -> dict[str, Any]:
    def load(recordings: service.Recordings) -> list[dict[str, Any]]:
        now = _now()
        recordings.purge(now)
        return recordings.list_items(now)

    return {
        "sentences": [
            {"index": index, "text": sentence}
            for index, sentence in enumerate(OWNER_SENTENCES, start=1)
        ],
        "places": list(service.PLACES),
        "retention_days": service.RETENTION_DAYS,
        "max_seconds": service.MAX_SECONDS,
        "recordings": await _run(request, load),
    }


@router.get("/manifest")
async def get_manifest(request: Request, place: str | None = None) -> dict[str, Any]:
    return await _run(request, lambda recordings: recordings.manifest(_now(), place))


@router.put("/recordings/{place}/{index}")
async def put_recording(place: str, index: str, request: Request) -> dict[str, Any]:
    body = await _bounded_json(request)
    return await _run(
        request,
        lambda recordings: recordings.save(
            place,
            index,
            audio_wav_base64=body.get("audio_wav_base64"),
            browser_transcript=body.get("browser_transcript"),
            browser_engine=body.get("browser_engine"),
            capture=body.get("capture"),
            now=_now(),
        ),
    )


@router.get("/recordings/{place}/{index}/audio")
async def get_audio(place: str, index: str, request: Request) -> Response:
    audio = await _run(request, lambda recordings: recordings.read_audio(place, index, _now()))
    return Response(content=audio, media_type="audio/wav")


@router.delete("/recordings/{place}/{index}")
async def delete_recording(place: str, index: str, request: Request) -> dict[str, int]:
    return {"deleted": await _run(request, lambda recordings: recordings.delete_one(place, index))}


@router.delete("/recordings")
async def delete_recordings(request: Request) -> dict[str, int]:
    return {"deleted": await _run(request, lambda recordings: recordings.delete_all())}
