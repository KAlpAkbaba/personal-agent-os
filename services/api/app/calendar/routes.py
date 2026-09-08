"""Calendar REST surface for the Cockpit's approval pair (docs/M21_MAIL_CALENDAR_SPEC.md §3).

- GET  /v1/calendar/proposals/pending          the prepared proposals awaiting the owner
- POST /v1/calendar/proposals/{id}/confirm     runs the SAME gate as the voice confirmation
- POST /v1/calendar/proposals/{id}/discard

Owner-gated like every other surface (``require_owner_session`` at the router level).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.calendar.models import PROPOSAL_STATE_PREPARED, CalendarProposalRow
from app.calendar.service import CalendarService
from app.identity.dependencies import require_owner_session

CALENDAR_ROUTES_VERSION = 1

router = APIRouter(
    prefix="/v1/calendar", tags=["calendar"], dependencies=[Depends(require_owner_session)]
)


def _artifacts(request: Request) -> Any:
    return request.app.state.artifacts


def _service(request: Request) -> CalendarService:
    return request.app.state.calendar_service


def _proposal_dict(row: CalendarProposalRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "event_uid": row.event_uid,
        "summary": row.summary,
        "start": row.start.isoformat(),
        "end": row.end.isoformat(),
        "location": row.location,
        "conflicts": list(row.conflicts_json or []),
        "state": row.state,
        "read_back_at": row.read_back_at.isoformat() if row.read_back_at else None,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "committed_event_uid": row.committed_event_uid,
    }


@router.get("/proposals/pending")
async def list_pending_proposals(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> list[dict[str, Any]]:
        with artifacts.session() as db:
            rows = (
                db.execute(
                    select(CalendarProposalRow)
                    .where(CalendarProposalRow.state == PROPOSAL_STATE_PREPARED)
                    .order_by(CalendarProposalRow.created_at.desc())
                )
                .scalars()
                .all()
            )
            return [_proposal_dict(r) for r in rows]

    proposals = await asyncio.to_thread(load)
    return {"proposals": proposals}


@router.post("/proposals/{proposal_id}/confirm")
async def confirm_proposal(proposal_id: uuid.UUID, request: Request) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)
    settings = request.app.state.settings

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(CalendarProposalRow, proposal_id) is None:
                raise HTTPException(status_code=404, detail="proposal not found")
            return service.commit(
                db,
                proposal_id=str(proposal_id),
                host_flag_enabled=bool(settings.calendar_write_enabled),
            )

    return await asyncio.to_thread(run)


@router.post("/proposals/{proposal_id}/discard")
async def discard_proposal(proposal_id: uuid.UUID, request: Request) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(CalendarProposalRow, proposal_id) is None:
                raise HTTPException(status_code=404, detail="proposal not found")
            return service.discard(db, proposal_id=str(proposal_id))

    return await asyncio.to_thread(run)


__all__ = ["CALENDAR_ROUTES_VERSION", "router"]
