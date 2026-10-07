"""``/v1/watches``: the owner lists, makes and forgets watches.

GET lists, POST makes one (``url``, ``condition``, ``label``, ``every_hours`` = 6,
``selector``), DELETE ``/{id}`` removes one, DELETE forgets every watch and every reading.
Under the owner session. A refusal is ``{detail: {code, message}}`` with a Turkish message.
The rules live in ``app.watch.service``, not here.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.identity.dependencies import require_owner_session
from app.watch import service
from app.watch.service import WatchView

router = APIRouter(dependencies=[Depends(require_owner_session)])


def _stamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()


def _view(view: WatchView) -> dict[str, Any]:
    return {
        "id": view.id,
        "label": view.label,
        "url": view.url,
        "condition": view.condition,
        "every_hours": view.every_hours,
        "selector": view.selector,
        "created_at": _stamp(view.created_at),
        "last_read_at": _stamp(view.last_read_at),
        "last_outcome": view.last_outcome,
        "last_value": view.last_value,
        "consecutive_failures": view.consecutive_failures,
    }


def _not_found() -> HTTPException:
    return HTTPException(404, {"code": "not_found", "message": "Bu nöbet yok; silinmiş olabilir."})


async def _payload(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        payload = json.loads(raw) if raw.strip() else None
    except ValueError as error:
        raise HTTPException(
            422, {"code": "body_invalid", "message": "İstek okunamadı; JSON olarak gönder."}
        ) from error
    if not isinstance(payload, dict):
        raise HTTPException(
            422, {"code": "body_invalid", "message": "İstek bir JSON nesnesi olmalı."}
        )
    return payload


#: The fields the owner writes; a NUL (or any other control character) in one was kept
#: (test-team finding, three runs) and would be read back into a briefing.
_TEXT_FIELDS = ("label", "url", "condition", "selector")


def _refuse_control_characters(payload: dict[str, Any]) -> None:
    for field in _TEXT_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise HTTPException(
                422,
                {
                    "code": "control_character",
                    "message": "Nöbetin adında ya da adresinde okunamayan bir karakter var; "
                    "düz yazıyla yaz.",
                },
            )


@router.get("/v1/watches")
async def list_watches(request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts

    def run() -> dict[str, Any]:
        with artifacts.session() as session:
            return {"items": [_view(v) for v in service.list_watches(session)]}

    return await asyncio.to_thread(run)


@router.post("/v1/watches", status_code=201)
async def create_watch(request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts
    payload = await _payload(request)
    _refuse_control_characters(payload)

    def run() -> dict[str, Any]:
        with artifacts.session() as session:
            view = service.create_watch(
                session,
                url=payload.get("url"),  # type: ignore[arg-type]
                condition=payload.get("condition"),  # type: ignore[arg-type]
                label=payload.get("label"),  # type: ignore[arg-type]
                every_hours=payload.get("every_hours", 6),  # type: ignore[arg-type]
                selector=payload.get("selector"),
            )
            session.commit()
            return _view(view)

    try:
        return await asyncio.to_thread(run)
    except service.WatchRefused as refused:
        raise HTTPException(422, {"code": refused.code, "message": refused.reason_tr}) from None


@router.delete("/v1/watches/{watch_id}")
async def remove_watch(watch_id: str, request: Request) -> dict[str, int]:
    artifacts = request.app.state.artifacts
    try:
        key = uuid.UUID(watch_id)
    except ValueError as error:
        raise _not_found() from error

    def run() -> bool:
        with artifacts.session() as session:
            removed = service.remove_watch(session, key)
            session.commit()
            return removed

    if not await asyncio.to_thread(run):
        raise _not_found()
    return {"deleted": 1}


@router.delete("/v1/watches")
async def forget_watches(request: Request) -> dict[str, int]:
    artifacts = request.app.state.artifacts

    def run() -> int:
        with artifacts.session() as session:
            removed = service.forget_all(session)
            session.commit()
            return removed

    return {"deleted": await asyncio.to_thread(run)}
