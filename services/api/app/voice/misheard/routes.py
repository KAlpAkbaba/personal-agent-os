"""``/v1/voice/misheard``: the owner reads, answers and forgets the misheard notebook.

GET lists what is not yet expired, newest first (it purges first, so reading the notebook is
one of the three things that delete an expired row); POST ``/{id}/meaning`` writes the
owner's answer on one row; DELETE ``/{id}`` forgets one row; DELETE forgets every row
("defteri unut"). Under the owner session. A refusal is ``{detail: {code, message}}`` with a
Turkish message. The rules live in ``app.voice.misheard.service``, not here.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.identity.dependencies import require_owner_session
from app.voice.misheard import service
from app.voice.misheard.models import MEANT_WIDTH, MisheardUtterance

router = APIRouter(dependencies=[Depends(require_owner_session)])


def _stamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()


def _view(row: MisheardUtterance) -> dict[str, Any]:
    """Exactly the CONTRACT's columns."""
    return {
        "id": str(row.id),
        "heard_at": _stamp(row.heard_at),
        "sentence": row.sentence,
        "mode": row.mode,
        "engine": row.engine,
        "device_id": str(row.device_id) if row.device_id else None,
        "band": row.band,
        "confidence": row.confidence,
        "reason": row.reason,
        "resolved_intent": row.resolved_intent,
        "tool": row.tool,
        "session_id": str(row.session_id),
        "meant": row.meant,
        "answered_at": _stamp(row.answered_at),
        "expires_at": _stamp(row.expires_at),
    }


def _not_found() -> HTTPException:
    return HTTPException(
        404,
        {"code": "not_found", "message": "Bu cümle defterde yok; silinmiş ya da süresi dolmuş."},
    )


def _item_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as error:
        raise _not_found() from error


def _meant(payload: Any) -> str:
    value = payload.get("meant") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(
            422, {"code": "meant_empty", "message": "Ne demek istediğini yaz; anlam boş olamaz."}
        )
    text = value.strip()
    if len(text) > MEANT_WIDTH:
        raise HTTPException(
            422,
            {
                "code": "meant_too_long",
                "message": f"Anlam en çok {MEANT_WIDTH} karakter olabilir.",
            },
        )
    return text


@router.get("/v1/voice/misheard")
async def list_misheard(request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts

    def run() -> dict[str, Any]:
        now = datetime.now(UTC)
        with artifacts.session() as session:
            service.purge(session, now)
            session.commit()
            items = [_view(row) for row in service.list_items(session, now)]
        return {
            "items": items,
            "open": sum(1 for item in items if item["meant"] is None),
            "retention_days": service.RETENTION_DAYS,
        }

    return await asyncio.to_thread(run)


@router.post("/v1/voice/misheard/{item_id}/meaning")
async def answer_misheard(
    item_id: str, request: Request, payload: Annotated[Any, Body()] = None
) -> dict[str, Any]:
    artifacts = request.app.state.artifacts
    row_id = _item_id(item_id)
    meant = _meant(payload)

    def run() -> dict[str, Any]:
        with artifacts.session() as session:
            row = service.answer(session, row_id, meant, datetime.now(UTC))
            if row is None:
                raise _not_found()
            session.commit()
            return _view(row)

    return await asyncio.to_thread(run)


@router.delete("/v1/voice/misheard/{item_id}")
async def forget_misheard(item_id: str, request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts
    row_id = _item_id(item_id)

    def run() -> dict[str, Any]:
        with artifacts.session() as session:
            if not service.forget_one(session, row_id):
                raise _not_found()
            session.commit()
        return {"deleted": 1}

    return await asyncio.to_thread(run)


@router.delete("/v1/voice/misheard")
async def forget_notebook(request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts

    def run() -> dict[str, Any]:
        with artifacts.session() as session:
            deleted = service.forget_all(session)
            session.commit()
        return {"deleted": deleted}

    return await asyncio.to_thread(run)
