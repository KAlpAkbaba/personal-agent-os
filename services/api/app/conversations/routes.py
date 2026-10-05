"""``/v1/conversations``: the owner starts, stops, reads, searches and forgets conversations.

POST starts one (``mode`` manual|home), ``/{id}/stop`` stops it, ``/{id}/segments`` adds a line
(``content`` + an optional derived ``embedding`` + ``is_owner``; a body naming audio is
refused), ``/{id}/speakers/{n}/name`` is 'bu Ahmet', ``/people`` lists the named people,
``/people/{id}/consent`` is 'Ahmet izin verdi', DELETE ``/people/{id}`` deletes a person and
their profile, GET ``?q=`` searches, DELETE ``/{id}`` and DELETE forget ('unut'), and
``/settings`` holds the standing 'evde dinle'. Under the owner session. A refusal is
``{detail: {code, message}}`` with a Turkish message; the rules live in the service.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.conversations import service
from app.conversations.service import ConversationView, PersonView, SegmentView
from app.identity.dependencies import require_owner_session

router = APIRouter(dependencies=[Depends(require_owner_session)])

#: A line is text. Any of these keys means a client tried to send the sound itself.
AUDIO_KEYS = ("audio", "audio_b64", "pcm", "wav", "samples", "recording")


def _stamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()


def _segment(view: SegmentView) -> dict[str, Any]:
    return {
        "id": view.id,
        "seq": view.seq,
        "spoken_at": _stamp(view.spoken_at),
        "text": view.text,
        "speaker": view.speaker,
        "is_owner": view.is_owner,
        "speaker_no": view.speaker_no,
        "person_id": view.person_id,
    }


def _conversation(view: ConversationView, *, segments: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(view.id),
        "mode": view.mode,
        "title": view.title,
        "started_at": _stamp(view.started_at),
        "ended_at": _stamp(view.ended_at),
        "segment_count": view.segment_count,
        "preview": view.preview,
    }
    if segments:
        body["segments"] = [_segment(s) for s in view.segments]
    return body


def _person(view: PersonView) -> dict[str, Any]:
    return {
        "id": str(view.id),
        "name": view.name,
        "created_at": _stamp(view.created_at),
        "consent": view.consent_at is not None,
        "consent_at": _stamp(view.consent_at),
        "consent_note": view.consent_note,
        "has_profile": view.has_profile,
    }


def _refused(refused: service.ConversationRefused) -> HTTPException:
    status = (
        404
        if refused.code == "not_found"
        else 409
        if refused.code
        in (
            "already_open",
            "closed",
        )
        else 422
    )
    return HTTPException(status, {"code": refused.code, "message": refused.message})


def _key(raw: str, what: str = "Bu konuşma") -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as error:
        raise HTTPException(
            404, {"code": "not_found", "message": f"{what} yok; silinmiş olabilir."}
        ) from error


async def _payload(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError as error:
        raise HTTPException(
            422, {"code": "body_invalid", "message": "İstek okunamadı; JSON olarak gönder."}
        ) from error
    if not isinstance(payload, dict):
        raise HTTPException(
            422, {"code": "body_invalid", "message": "İstek bir JSON nesnesi olmalı."}
        )
    return payload


async def _run(request: Request, work):  # noqa: ANN001, ANN202
    artifacts = request.app.state.artifacts
    live = request.app.state.conversation_live
    cipher = request.app.state.conversation_cipher

    def run() -> Any:
        with artifacts.session() as session:
            result = work(session, live, cipher)
            session.commit()
            return result

    try:
        return await asyncio.to_thread(run)
    except service.ConversationRefused as refused:
        raise _refused(refused) from None


@router.get("/v1/conversations")
async def list_conversations(request: Request, q: str | None = None) -> dict[str, Any]:
    return await _run(
        request,
        lambda db, live, cipher: {
            "items": [_conversation(v, segments=False) for v in service.list_conversations(db, q=q)]
        },
    )


@router.post("/v1/conversations", status_code=201)
async def start_conversation(request: Request) -> dict[str, Any]:
    payload = await _payload(request)
    return await _run(
        request,
        lambda db, live, cipher: _conversation(
            service.start_conversation(
                db, live, mode=str(payload.get("mode") or "manual"), title=payload.get("title")
            ),
            segments=False,
        ),
    )


@router.get("/v1/conversations/people")
async def list_people(request: Request) -> dict[str, Any]:
    return await _run(
        request, lambda db, live, cipher: {"items": [_person(p) for p in service.list_people(db)]}
    )


@router.post("/v1/conversations/people/{person_id}/consent")
async def record_consent(person_id: str, request: Request) -> dict[str, Any]:
    key = _key(person_id, "Bu kişi")
    payload = await _payload(request)
    return await _run(
        request,
        lambda db, live, cipher: _person(
            service.record_consent(db, live, cipher, person_id=key, note=payload.get("note"))
        ),
    )


@router.delete("/v1/conversations/people/{person_id}")
async def delete_person(person_id: str, request: Request) -> dict[str, int]:
    key = _key(person_id, "Bu kişi")
    removed = await _run(request, lambda db, live, cipher: service.delete_person(db, live, key))
    if not removed:
        raise HTTPException(
            404, {"code": "not_found", "message": "Bu kişi yok; silinmiş olabilir."}
        )
    return {"deleted": 1}


@router.get("/v1/conversations/settings")
async def get_settings(request: Request) -> dict[str, bool]:
    return await _run(
        request, lambda db, live, cipher: {"home_listen": service.get_home_listen(db)}
    )


@router.put("/v1/conversations/settings")
async def put_settings(request: Request) -> dict[str, bool]:
    payload = await _payload(request)
    if not isinstance(payload.get("home_listen"), bool):
        raise HTTPException(
            422, {"code": "home_listen_invalid", "message": "'evde dinle' açık ya da kapalı olur."}
        )
    return await _run(
        request,
        lambda db, live, cipher: {
            "home_listen": service.set_home_listen(db, payload["home_listen"])
        },
    )


@router.get("/v1/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
    key = _key(conversation_id)
    return await _run(
        request,
        lambda db, live, cipher: _conversation(service.get_conversation(db, key), segments=True),
    )


@router.post("/v1/conversations/{conversation_id}/stop")
async def stop_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
    key = _key(conversation_id)
    return await _run(
        request,
        lambda db, live, cipher: _conversation(
            service.stop_conversation(db, live, key), segments=False
        ),
    )


@router.post("/v1/conversations/{conversation_id}/segments", status_code=201)
async def add_segment(conversation_id: str, request: Request) -> dict[str, Any]:
    key = _key(conversation_id)
    payload = await _payload(request)
    if any(name in payload for name in AUDIO_KEYS):
        raise HTTPException(
            422,
            {
                "code": "audio_refused",
                "message": "Ses kaydı alınmaz; yalnızca yazıya dökülmüş metin.",
            },
        )
    embedding = payload.get("embedding")
    if embedding is not None and (
        not isinstance(embedding, list)
        or not embedding
        or len(embedding) > 4096
        or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in embedding)
    ):
        raise HTTPException(
            422, {"code": "embedding_invalid", "message": "Ses izi bir sayı listesi olmalı."}
        )
    is_owner = payload.get("is_owner")

    def work(db, live, cipher):  # noqa: ANN001, ANN202
        result = service.add_segment(
            db,
            live,
            cipher,
            key,
            text=str(payload.get("content") or ""),
            embedding=embedding,
            is_owner=is_owner if isinstance(is_owner, bool) else None,
        )
        return {**_segment(result.segment), "ask_who": result.ask_who}

    return await _run(request, work)


@router.post("/v1/conversations/{conversation_id}/speakers/{speaker_no}/name")
async def name_speaker(conversation_id: str, speaker_no: int, request: Request) -> dict[str, Any]:
    key = _key(conversation_id)
    payload = await _payload(request)

    def work(db, live, cipher):  # noqa: ANN001, ANN202
        result = service.name_speaker(
            db, live, cipher, key, speaker_no, str(payload.get("name") or "")
        )
        return {
            "person": _person(result.person),
            "applied": result.applied,
            "message": result.message,
        }

    return await _run(request, work)


@router.delete("/v1/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request) -> dict[str, int]:
    key = _key(conversation_id)
    removed = await _run(
        request, lambda db, live, cipher: service.delete_conversation(db, live, key)
    )
    if not removed:
        raise HTTPException(
            404, {"code": "not_found", "message": "Bu konuşma yok; silinmiş olabilir."}
        )
    return {"deleted": 1}


@router.delete("/v1/conversations")
async def forget_conversations(request: Request) -> dict[str, int]:
    return {"deleted": await _run(request, lambda db, live, cipher: service.forget_all(db, live))}
