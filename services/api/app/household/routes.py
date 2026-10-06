"""``/v1/household``: the owner reads and edits the house's stock and the shopping list.

GET reads everything (items, the list, the "soon" items and the sentence the voice would say);
POST ``/items`` sets a level (``name``, ``level`` = var | azaldı | bitti); POST ``/list`` puts an
item on the list (``name``, ``quantity``); DELETE ``/list/{id}`` takes one off; DELETE
``/items/{id}`` forgets an item and its history. Under the owner session. A refusal is
``{detail: {code, message}}`` with a Turkish message. The rules live in ``service``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.household import service
from app.household.models import HouseholdItem
from app.identity.dependencies import require_owner_session

router = APIRouter(dependencies=[Depends(require_owner_session)])


def _stamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()


def _view(row: HouseholdItem) -> dict[str, Any]:
    predicted = service.predicted_out_at(row)
    return {
        "id": str(row.id),
        "name": row.name,
        "level": row.level,
        "on_list": row.on_list,
        "list_quantity": row.list_quantity,
        "usual_quantity": row.usual_quantity,
        "updated_at": _stamp(row.updated_at),
        "depleted_at": _stamp(row.depleted_at),
        "restocked_at": _stamp(row.restocked_at),
        "cycle_days": row.cycle_days,
        "predicted_out_at": _stamp(predicted),
    }


def _refused(error: service.HouseholdRefused) -> HTTPException:
    return HTTPException(422, {"code": "household_refused", "message": error.message})


def _not_found() -> HTTPException:
    return HTTPException(404, {"code": "not_found", "message": "Bu ürün yok; silinmiş olabilir."})


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


def _item_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as error:
        raise _not_found() from error


@router.get("/v1/household")
async def read_household(request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts

    def run() -> dict[str, Any]:
        now = datetime.now(UTC)
        with artifacts.session() as db:
            listed = service.shopping_list(db, now=now)
            return {
                "items": [_view(r) for r in service.list_items(db)],
                "list": [_view(r) for r in listed.items],
                "soon": [_view(r) for r in listed.soon],
                "speech": service.list_speech(listed),
            }

    return await asyncio.to_thread(run)


@router.post("/v1/household/items")
async def set_item_level(request: Request) -> dict[str, Any]:
    payload = await _payload(request)
    artifacts = request.app.state.artifacts

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            change = service.set_level(
                db,
                payload.get("name"),
                payload.get("level"),
                now=datetime.now(UTC),  # type: ignore[arg-type]
            )
            return {"item": _view(change.item), "speech": change.speech}

    try:
        return await asyncio.to_thread(run)
    except service.HouseholdRefused as error:
        raise _refused(error) from error


@router.post("/v1/household/list")
async def add_list_item(request: Request) -> dict[str, Any]:
    payload = await _payload(request)
    artifacts = request.app.state.artifacts

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            change = service.add_to_list(
                db,
                payload.get("name"),  # type: ignore[arg-type]
                quantity=payload.get("quantity"),
                now=datetime.now(UTC),
            )
            return {"item": _view(change.item), "speech": change.speech, "already": change.already}

    try:
        return await asyncio.to_thread(run)
    except service.HouseholdRefused as error:
        raise _refused(error) from error


@router.delete("/v1/household/list/{item_id}")
async def remove_list_item(item_id: str, request: Request) -> dict[str, Any]:
    target = _item_id(item_id)
    artifacts = request.app.state.artifacts

    def run() -> dict[str, Any] | None:
        with artifacts.session() as db:
            row = service.remove_by_id(db, target, now=datetime.now(UTC))
            return None if row is None else _view(row)

    view = await asyncio.to_thread(run)
    if view is None:
        raise _not_found()
    return {"item": view}


@router.delete("/v1/household/items/{item_id}")
async def forget_item(item_id: str, request: Request) -> dict[str, Any]:
    target = _item_id(item_id)
    artifacts = request.app.state.artifacts

    def run() -> int:
        with artifacts.session() as db:
            return service.forget_item(db, target)

    deleted = await asyncio.to_thread(run)
    if not deleted:
        raise _not_found()
    return {"deleted": deleted}
