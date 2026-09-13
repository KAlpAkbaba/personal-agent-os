"""B11 req 368/377: the inbox and the delivery history, over HTTP.

Owner-gated like every other read surface. These read the ``notifications`` table and nothing
else - no provider is consulted, which is the point: the old inbox asked the fake push
transport what it remembered, so it was empty in production and lost on every restart.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.identity.dependencies import require_owner_session
from app.notifications import service as notifications
from app.notifications.models import NotificationRow

router = APIRouter(prefix="/v1/notifications", dependencies=[Depends(require_owner_session)])


def _session_factory(request: Request):
    return request.app.state.artifacts.session


def _as_dict(row: NotificationRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "priority": row.priority,
        "group_key": row.group_key or None,
        "created_at": _iso(row.created_at),
        "delivered_at": _iso(row.delivered_at),
        "delivered_via": row.delivered_via,
        "read_at": _iso(row.read_at),
        "data": dict(row.data_json or {}),
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    from datetime import UTC

    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


@router.get("")
async def list_notifications(
    request: Request,
    unread: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """The durable inbox, newest first. Superseded members of a group are left out - the
    newest one is the whole of what that group currently says."""
    with _session_factory(request)() as db:
        rows = notifications.inbox(db, unread_only=unread, limit=limit)
        unread_count = len(notifications.inbox(db, unread_only=True, limit=200))
        return {
            "notifications": [_as_dict(row) for row in rows],
            "unread": unread_count,
        }


@router.post("/{notification_id}/read")
async def mark_read(request: Request, notification_id: uuid.UUID) -> dict[str, Any]:
    with _session_factory(request)() as db:
        row = notifications.mark_read(db, notification_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown notification")
        return _as_dict(row)


@router.get("/history")
async def delivery_history(
    request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> dict[str, Any]:
    """req 377. Everything recorded, INCLUDING what no channel carried - "we never reached
    you about this" is the part of a delivery history that matters."""
    with _session_factory(request)() as db:
        return {"history": notifications.history(db, limit=limit)}


__all__ = ["router"]
