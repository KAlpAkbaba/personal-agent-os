"""Mail REST surface for the Cockpit's approval pair (docs/M21_MAIL_CALENDAR_SPEC.md §3).

- GET  /v1/mail/drafts/pending          the prepared drafts awaiting the owner's word —
                                         the owner-session listing itself IS the read-back
                                         for this surface (ADR-0084 addendum 2): a row the
                                         Cockpit presents transitions ``prepared`` ->
                                         ``read_back`` right here, the same explicit act
                                         ``mail.read_draft`` performs for voice.
- POST /v1/mail/drafts/{id}/confirm     runs the SAME gate as the voice confirmation —
                                         the owner's own authenticated act IS the
                                         confirmation on this surface, no turn to check.
- POST /v1/mail/drafts/{id}/discard

Owner-gated like every other surface (``require_owner_session`` at the router level) — an
EXTERNAL MUTATION must never be one HTTP call away from an unauthenticated caller any more
than "Gönder." is one utterance away from one.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.actions.confirmation_gate import CONFIRM_SOURCE_REST, Confirmation
from app.identity.dependencies import require_owner_session
from app.mail.models import DRAFT_STATE_PREPARED, DRAFT_STATE_READ_BACK, MailDraftRow
from app.mail.service import MailService

MAIL_ROUTES_VERSION = 1

router = APIRouter(prefix="/v1/mail", tags=["mail"], dependencies=[Depends(require_owner_session)])


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
    owner_session_id = str(request.state.owner_session.session_id)

    def load() -> list[dict[str, Any]]:
        with artifacts.session() as db:
            rows = (
                db.execute(
                    select(MailDraftRow)
                    .where(MailDraftRow.state.in_((DRAFT_STATE_PREPARED, DRAFT_STATE_READ_BACK)))
                    .order_by(MailDraftRow.created_at.desc())
                )
                .scalars()
                .all()
            )
            # The Cockpit's own listing IS the read-back for this surface (module
            # docstring, ADR-0084 addendum 2) — a row still ``prepared`` moves to
            # ``read_back`` right here, bound to THIS owner session, no voice turn.
            now = datetime.now(UTC)
            for row in rows:
                if row.state == DRAFT_STATE_PREPARED:
                    row.state = DRAFT_STATE_READ_BACK
                    row.read_back_at = now
                    row.read_back_session_id = f"{CONFIRM_SOURCE_REST}:{owner_session_id}"
                    row.read_back_turn = None
                    row.updated_at = now
            db.commit()
            return [_draft_dict(r) for r in rows]

    drafts = await asyncio.to_thread(load)
    return {"drafts": drafts}


@router.post("/drafts/{draft_id}/confirm")
async def confirm_draft(draft_id: uuid.UUID, request: Request) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)
    settings = request.app.state.settings
    owner_session_id = str(request.state.owner_session.session_id)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(MailDraftRow, draft_id) is None:
                raise HTTPException(status_code=404, detail="draft not found")
            confirmation = Confirmation(source=CONFIRM_SOURCE_REST, session_id=owner_session_id)
            return service.send(
                db,
                draft_id=str(draft_id),
                host_flag_enabled=bool(settings.mail_send_enabled),
                session_id=owner_session_id,
                confirmation=confirmation,
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
