"""Mail REST surface for the Cockpit's approval pair (docs/M21_MAIL_CALENDAR_SPEC.md §3).

- GET  /v1/mail/drafts/pending          the prepared drafts awaiting the owner's word
- POST /v1/mail/drafts/{id}/confirm     runs the SAME gate as the voice confirmation
- POST /v1/mail/drafts/{id}/discard

Owner-gated like every other surface (``require_owner_session`` at the router level) — an
EXTERNAL MUTATION must never be one HTTP call away from an unauthenticated caller any more
than "Gönder." is one utterance away from one.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.identity.dependencies import require_owner_session
from app.mail.models import DRAFT_STATE_PREPARED, MailDraftRow
from app.mail.service import MailService

MAIL_ROUTES_VERSION = 1

router = APIRouter(
    prefix="/v1/mail", tags=["mail"], dependencies=[Depends(require_owner_session)]
)


def _artifacts(request: Request) -> Any:
    return request.app.state.artifacts


def _service(request: Request) -> MailService:
    return request.app.state.mail_service


def _draft_dict(row: MailDraftRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "to": list(row.to_json or []),
        "cc": list(row.cc_json or []),
        "subject": row.subject,
        "body": row.body,
        "in_reply_to": row.in_reply_to,
        "state": row.state,
        "read_back_at": row.read_back_at.isoformat() if row.read_back_at else None,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "sent_message_id": row.sent_message_id,
    }


@router.get("/drafts/pending")
async def list_pending_drafts(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> list[dict[str, Any]]:
        with artifacts.session() as db:
            rows = (
                db.execute(
                    select(MailDraftRow)
                    .where(MailDraftRow.state == DRAFT_STATE_PREPARED)
                    .order_by(MailDraftRow.created_at.desc())
                )
                .scalars()
                .all()
            )
            return [_draft_dict(r) for r in rows]

    drafts = await asyncio.to_thread(load)
    return {"drafts": drafts}


@router.post("/drafts/{draft_id}/confirm")
async def confirm_draft(draft_id: uuid.UUID, request: Request) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)
    settings = request.app.state.settings

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(MailDraftRow, draft_id) is None:
                raise HTTPException(status_code=404, detail="draft not found")
            return service.send(
                db, draft_id=str(draft_id), host_flag_enabled=bool(settings.mail_send_enabled)
            )

    return await asyncio.to_thread(run)


@router.post("/drafts/{draft_id}/discard")
async def discard_draft(draft_id: uuid.UUID, request: Request) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(MailDraftRow, draft_id) is None:
                raise HTTPException(status_code=404, detail="draft not found")
            return service.discard(db, draft_id=str(draft_id))

    return await asyncio.to_thread(run)


__all__ = ["MAIL_ROUTES_VERSION", "router"]
