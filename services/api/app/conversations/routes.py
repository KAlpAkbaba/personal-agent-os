"""``/v1/conversations``: the owner starts, stops, reads, searches and forgets conversations.

POST starts one (``mode`` manual|home), ``/{id}/stop`` stops it, ``/{id}/segments`` adds a line
(``content`` + an optional derived ``embedding`` + ``is_owner``; a body with a key naming audio
in any case or as part of the key - ``Audio``, ``audio_data``, ``pcm16`` - is refused, so is a
``content`` carrying the sound itself (a ``data:`` URL, a long base64 run, an audio magic), and a
line with an embedding is checked against the owner's enrolled voice profile),
``/{id}/speakers/{n}/name`` is 'bu Ahmet', ``/people`` lists the named people,
``/people/{id}/consent`` is 'Ahmet izin verdi', DELETE ``/people/{id}`` deletes a person and
their profile, GET ``?q=`` searches, DELETE ``/{id}`` and DELETE forget ('unut'), and
``/settings`` holds the standing 'evde dinle'. Under the owner session. A refusal is
``{detail: {code, message}}`` with a Turkish message; the rules live in the service.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from app.conversations import service
from app.conversations.service import ConversationView, PersonView, SegmentView
from app.identity.dependencies import require_owner_session
from app.logging import get_logger
from app.voice.service import load_owner_profile
from app.voice.speaker import OwnerProfile

router = APIRouter(dependencies=[Depends(require_owner_session)])

logger = get_logger("app.conversations")

#: A line is text. A key containing any of these (any case) means a client tried to send the
#: sound itself: 'audio', 'Audio', 'audio_data', 'rawAudio', 'pcm16', 'voice_wav', ...
AUDIO_KEY_PARTS = (
    "audio",
    "pcm",
    "wav",
    "sample",
    "recording",
    "sound",
    "mp3",
    "ogg",
    "opus",
    "webm",
    "flac",
)


#: ...and the sound may also ride inside the line itself (test team, 2026-10-06: a
#: ``data:audio/wav;base64,...`` content answered 201): a ``data:`` URL, a base64 run no
#: sentence has (200+ characters, no space), or a base64 run opening with an audio file's
#: magic - RIFF, ID3, OggS, fLaC, EBML (webm/mkv), #!AMR, and an m4a/mp4/3gp ``ftyp`` box
#: (``GZ0eX``: four bytes in, after a box size that is a multiple of 4) - long enough to be
#: bytes, not a word. Inspector, first pass: the ``base64`` CLI / MIME shape wraps at 76 (PEM
#: at 64) so no line reaches 200 - two wrapped lines of 40+ base64 characters and a third
#: line's start are a block no sentence has (a sentence line has spaces). Both run shapes
#: start only where a run starts, so a line of 199-character runs is scanned once, not
#: once per character (11 ms -> under 1 ms a 4000-character line).
EMBEDDED_AUDIO = re.compile(
    r"(?i:data:\s*[\w.+-]+/[\w.+-]+\s*[;,])"
    r"|(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{200,}"
    r"|(?<![A-Za-z0-9+/_-])(?:[A-Za-z0-9+/_-]{40,}={0,2}[ \t]*\r?\n[ \t]*){2}[A-Za-z0-9+/_-]{8}"
    r"|(?:UklGR|SUQz|T2dnU|ZkxhQ|GkXfo|IyFBTV|GZ0eX)[A-Za-z0-9+/_-]{8,}"
)


def _names_audio(key: object) -> bool:
    folded = str(key).lower()
    return any(part in folded for part in AUDIO_KEY_PARTS)


def _carries_audio(content: object) -> bool:
    return isinstance(content, str) and EMBEDDED_AUDIO.search(content) is not None


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
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001
        # KVKK: a database error's text carries the statement's parameters - the owner's
        # conversation itself. Neither the answer nor the log carries more than the class.
        logger.warning(
            "conversation_store_failed", path=request.url.path, error=type(error).__name__
        )
        if isinstance(error, IntegrityError):
            raise HTTPException(
                409,
                {"code": "store_conflict", "message": "Aynı anda iki yazım çakıştı; yeniden dene."},
            ) from None
        raise HTTPException(
            500, {"code": "store_failed", "message": "Konuşma kaydedilemedi; yeniden dene."}
        ) from None


@router.get("/v1/conversations")
async def list_conversations(request: Request, q: str | None = None) -> dict[str, Any]:
    return await _run(
        request,
        lambda db, live, cipher: {
            "items": [_conversation(v, segments=False) for v in service.list_conversations(db, q=q)]
        },
    )


#: The partial unique index that lets one conversation be open (migration
#: ``conversation_one_open``). Two devices starting at once both pass the service's check; the
#: loser's insert fails on this index and is the same 'already open' as the check's refusal.
ONE_OPEN_INDEX = "uq_conversations_one_open"


def _violates_one_open(error: IntegrityError) -> bool:
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == ONE_OPEN_INDEX


@router.post("/v1/conversations", status_code=201)
async def start_conversation(request: Request) -> dict[str, Any]:
    payload = await _payload(request)

    def start(db, live, cipher):  # noqa: ANN001, ANN202
        try:
            view = service.start_conversation(
                db, live, mode=str(payload.get("mode") or "manual"), title=payload.get("title")
            )
        except IntegrityError as error:
            if not _violates_one_open(error):
                raise
            raise service.ConversationRefused(
                "already_open", "Zaten yazdığım bir konuşma var; önce onu bitir."
            ) from None
        return _conversation(view, segments=False)

    return await _run(request, start)


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
    if any(_names_audio(name) for name in payload) or _carries_audio(payload.get("content")):
        raise HTTPException(
            422,
            {
                "code": "audio_refused",
                "message": "Ses kaydı alınmaz; yalnızca yazıya dökülmüş metin.",
            },
        )
    is_owner = payload.get("is_owner")
    if is_owner is not None and not isinstance(is_owner, bool):
        raise HTTPException(
            422, {"code": "is_owner_invalid", "message": "'is_owner' doğru ya da yanlış olur."}
        )
    embedding = payload.get("embedding")
    # The owner's enrolled voice decides 'Sen' for a line his device did not flag.
    owner_profile = (
        await _owner_profile(request) if embedding is not None and not is_owner else None
    )

    def work(db, live, cipher):  # noqa: ANN001, ANN202
        result = service.add_segment(
            db,
            live,
            cipher,
            key,
            text=payload.get("content"),
            embedding=embedding,
            is_owner=is_owner,
            owner_profile=owner_profile,
        )
        return {**_segment(result.segment), "ask_who": result.ask_who}

    return await _run(request, work)


async def _owner_profile(request: Request) -> OwnerProfile | None:
    """The owner's enrolled voice profile (``/v1/voice/speaker/enroll``), or None.

    An unreadable profile (object store down, other secret) only means his voice is not
    recognised on this line - the line is still written, under 'Konuşmacı N'."""
    voice = request.app.state.voice

    def read() -> OwnerProfile | None:
        with voice.session() as session:
            return load_owner_profile(session, voice.store, voice.cipher)

    try:
        return await asyncio.to_thread(read)
    except Exception as error:  # noqa: BLE001
        logger.warning("conversation_owner_profile_unreadable", error=type(error).__name__)
        return None


@router.post("/v1/conversations/{conversation_id}/speakers/{speaker_no}/name")
async def name_speaker(conversation_id: str, speaker_no: int, request: Request) -> dict[str, Any]:
    key = _key(conversation_id)
    payload = await _payload(request)

    def work(db, live, cipher):  # noqa: ANN001, ANN202
        result = service.name_speaker(db, live, cipher, key, speaker_no, payload.get("name"))
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
