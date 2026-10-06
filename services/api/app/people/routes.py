"""``/v1/people`` and a finished conversation's follow-ups (conversation-followups).

POST ``/v1/conversations/{id}/followups`` takes the follow-ups of a finished conversation (once)
and answers the ONE batched question; POST ``/v1/conversations/{id}/followups/answer`` with
``{"tamam": true|false}`` is the owner's answer - only ``true`` writes the proposed items into the
calendar. GET ``/v1/people/cards`` lists the cards with their open promises, GET
``/v1/people/recall?name=`` is 'Ahmet'e ne söz vermiştim', GET ``/v1/people/last-talk?name=`` is
'Ayşe ile en son ne konuştuk'. Under the owner session.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.conversations import followups
from app.identity.dependencies import require_owner_session
from app.people import service as people

router = APIRouter(dependencies=[Depends(require_owner_session)])


def _stamp(value: datetime | None) -> str | None:
    return people.local(value).isoformat() if value is not None else None


def _extractor(request: Request) -> followups.FollowupExtractor:
    extractor = getattr(request.app.state, "followup_extractor", None)
    if extractor is None:
        extractor = followups.build_followup_extractor(request.app.state.settings)
        request.app.state.followup_extractor = extractor
    return extractor


def _refused(exc: followups.FollowupRefused) -> HTTPException:
    status = 404 if exc.code == "not_found" else 409
    return HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message})


@router.post("/v1/conversations/{cid}/followups")
async def take_followups(cid: uuid.UUID, request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts
    extractor = _extractor(request)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            try:
                batch = followups.process_conversation(db, cid, extractor)
            except followups.FollowupRefused as exc:
                raise _refused(exc) from exc
            db.commit()
            return {
                "conversation_id": str(batch.conversation_id),
                "created": batch.created,
                "proposed": batch.proposed,
                "dropped": batch.dropped,
                "already": batch.already,
                "error": batch.error,
                "question": batch.question,
                "items": batch.items,
            }

    return await asyncio.to_thread(run)


@router.post("/v1/conversations/{cid}/followups/answer")
async def answer_followups(cid: uuid.UUID, request: Request) -> dict[str, Any]:
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("tamam"), bool):
        raise HTTPException(
            status_code=422,
            detail={"code": "answer_invalid", "message": "Cevap 'tamam' ya da 'hayır' olur."},
        )
    accepted = body["tamam"]
    artifacts = request.app.state.artifacts
    calendar = request.app.state.calendar_service
    settings = request.app.state.settings
    owner_session_id = str(request.state.owner_session.session_id)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            answer = followups.answer_followups(
                db,
                cid,
                accepted=accepted,
                calendar=calendar,
                host_flag_enabled=bool(settings.calendar_write_enabled),
                session_id=owner_session_id,
            )
            db.commit()
            return {
                "conversation_id": str(answer.conversation_id),
                "written": answer.written,
                "declined": answer.declined,
                "failed": answer.failed,
                "speech": answer.speech,
            }

    return await asyncio.to_thread(run)


@router.get("/v1/people/cards")
async def list_cards(request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            cards = []
            for card in people.list_cards(db):
                cards.append(
                    {
                        "id": str(card.id),
                        "name": card.name,
                        "relation": card.relation,
                        "last_talk_at": _stamp(card.last_talk_at),
                        "last_conversation_id": (
                            str(card.last_conversation_id) if card.last_conversation_id else None
                        ),
                        "last_topic": card.last_topic,
                        "open_promises": [
                            followups.item_dict(r, card.name)
                            for r in people.open_promises(db, card.id)
                        ],
                    }
                )
            return {"cards": cards}

    return await asyncio.to_thread(run)


@router.get("/v1/people/recall")
async def recall(name: str, request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            view = people.recall_promises(db, name)
            return {
                "name": view.name,
                "found": view.found,
                "speech": view.speech,
                "promises": [
                    {
                        "direction": p.direction,
                        "what": p.what,
                        "quote": p.quote,
                        "spoken_at": _stamp(p.spoken_at),
                        "due": _stamp(p.due_at),
                        "conversation_id": str(p.conversation_id) if p.conversation_id else None,
                        "segment": p.segment_seq,
                    }
                    for p in view.promises
                ],
            }

    return await asyncio.to_thread(run)


@router.get("/v1/people/last-talk")
async def last_talk(name: str, request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            view = people.last_talk(db, name)
            return {
                "name": view.name,
                "found": view.found,
                "speech": view.speech,
                "at": _stamp(view.at),
                "conversation_id": str(view.conversation_id) if view.conversation_id else None,
                "topic": view.topic,
            }

    return await asyncio.to_thread(run)
