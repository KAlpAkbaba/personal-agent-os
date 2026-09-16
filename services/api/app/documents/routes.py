"""Managed file mutation REST surface (B34 req 160, 162, 164, 166) for the Cockpit's
Approval Center - the third pending source beside mail drafts and calendar proposals.

- ``GET  /v1/documents/mutations``                 the undo journal, newest first (a read
                                                    of ``file_mutations``, no ledger row).
- ``GET  /v1/documents/mutations/pending``         the proposals awaiting the owner's word;
                                                    listing them here IS the read-back for
                                                    this surface (the mail/calendar rule).
- ``POST /v1/documents/mutations/{id}/confirm``    the SAME gate the spoken "Uygula." runs.
- ``POST /v1/documents/mutations/{id}/discard``
- ``POST /v1/documents/mutations/{id}/undo``       the inverse plan, run and read back.

Owner-gated at the router level: a change to one of the owner's files must never be one
HTTP call away from an unauthenticated caller any more than "Sil." is one utterance away.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.actions.confirmation_gate import CONFIRM_SOURCE_REST, Confirmation
from app.documents.models import MUTATION_STATE_PROPOSED, FileMutationRow
from app.documents.mutations import MutationService
from app.identity.dependencies import require_owner_session

DOCUMENTS_ROUTES_VERSION = 1

router = APIRouter(
    prefix="/v1/documents", tags=["documents"], dependencies=[Depends(require_owner_session)]
)

MAX_LIST = 100


def _artifacts(request: Request) -> Any:
    return request.app.state.artifacts


def _service(request: Request) -> MutationService:
    return request.app.state.document_mutations


def _documents(request: Request) -> Any:
    return request.app.state.document_service


@router.get("/search")
async def search_documents(
    request: Request,
    q: str = Query(min_length=1, max_length=400),
    limit: int = 5,
) -> dict[str, Any]:
    """B37 req 149: semantic search across the indexed documents. The embedder is the
    memory subsystem's - real when the owner configured a provider, the n-gram hash
    otherwise - and the answer says which."""
    artifacts = _artifacts(request)
    service = _documents(request)
    bounded = min(max(limit, 1), 20)

    def load() -> dict[str, Any]:
        with artifacts.session() as db:
            hits = service.search_indexed(db, q, k=bounded)
        embedder = service.embedder
        model_id = getattr(embedder, "model_id", None)
        return {
            "query": q,
            "embedder": {
                "model_id": model_id,
                "semantic": bool(model_id) and model_id != "deterministic-ngram",
            },
            "hits": hits,
        }

    return await asyncio.to_thread(load)


def _device(request: Request) -> Any:
    """The SAME device port the voice tools read (``ToolContext.live["device_action"]``),
    so a Cockpit confirmation and a spoken one reach one device through one path."""
    runtime = getattr(request.app.state, "voice_realtime", None)
    if runtime is not None:
        try:
            live = runtime.live_sources()
        except Exception:  # noqa: BLE001 - a runtime without live sources
            live = {}
        if live.get("device_action") is not None:
            return live["device_action"]
    return getattr(request.app.state, "device_action", None)


@router.get("/mutations")
async def list_mutations(request: Request, limit: int = 50) -> dict[str, Any]:
    artifacts = _artifacts(request)
    service = _service(request)
    bounded = min(max(limit, 1), MAX_LIST)

    def load() -> list[dict[str, Any]]:
        with artifacts.session() as db:
            return [service.entry(row) for row in service.journal(db, limit=bounded)]

    return {"mutations": await asyncio.to_thread(load)}


@router.get("/mutations/pending")
async def list_pending(request: Request) -> dict[str, Any]:
    """The proposals awaiting the owner; presenting one here marks it read back for the
    owner session, exactly as ``mail.drafts.pending`` does."""
    artifacts = _artifacts(request)
    service = _service(request)
    owner_session_id = str(request.state.owner_session.session_id)

    def load() -> list[dict[str, Any]]:
        with artifacts.session() as db:
            rows = service.pending(db)
            now = datetime.now(UTC)
            for row in rows:
                if row.read_back_at is None:
                    row.read_back_at = now
                row.read_back_session_id = row.read_back_session_id or owner_session_id
            db.commit()
            return [service.entry(row) for row in rows]

    return {"pending": await asyncio.to_thread(load)}


@router.post("/mutations/{mutation_id}/confirm")
async def confirm_mutation(mutation_id: uuid.UUID, request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)
    service = _service(request)
    owner_session_id = str(request.state.owner_session.session_id)
    device = _device(request)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(FileMutationRow, mutation_id) is None:
                raise HTTPException(status_code=404, detail="mutation not found")
            return service.apply(
                db,
                device,
                mutation_id=str(mutation_id),
                confirmation=Confirmation(source=CONFIRM_SOURCE_REST, session_id=owner_session_id),
                session_id=owner_session_id,
            )

    return await asyncio.to_thread(run)


@router.post("/mutations/{mutation_id}/discard")
async def discard_mutation(mutation_id: uuid.UUID, request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)
    service = _service(request)
    owner_session_id = str(request.state.owner_session.session_id)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            row = db.get(FileMutationRow, mutation_id)
            if row is None:
                raise HTTPException(status_code=404, detail="mutation not found")
            if row.state != MUTATION_STATE_PROPOSED:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_class": "already_exists",
                        "message": "Bu değişiklik zaten sonuçlanmış efendim.",
                    },
                )
            return service.discard(db, mutation_id=str(mutation_id), session_id=owner_session_id)

    return await asyncio.to_thread(run)


@router.post("/mutations/{mutation_id}/undo")
async def undo_mutation(mutation_id: uuid.UUID, request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)
    service = _service(request)
    owner_session_id = str(request.state.owner_session.session_id)
    device = _device(request)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(FileMutationRow, mutation_id) is None:
                raise HTTPException(status_code=404, detail="mutation not found")
            return service.undo(
                db, device, mutation_id=str(mutation_id), session_id=owner_session_id
            )

    return await asyncio.to_thread(run)


__all__ = ["DOCUMENTS_ROUTES_VERSION", "router"]
