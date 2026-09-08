"""Capability Genesis REST surface (docs/M24_CAPABILITY_GENESIS_SPEC.md §8,
ADR-0087) for the Cockpit's "Yeni Yetenek" panel — the web track wires
exactly these names.

- GET  /v1/genesis/runs               recent runs (capability, state, when,
                                      the error when failed) — a read of
                                      ``genesis_runs``, no ledger row per poll.
- GET  /v1/genesis/runs/{id}          one run.
- POST /v1/genesis/runs/{id}/approve  "Onayla" — ONLY in ``awaiting_approval``;
                                      the owner-authenticated REST act IS the
                                      confirmation (``CONFIRM_SOURCE_REST``,
                                      the same rule ``app.actions.confirmation_gate``
                                      documents for the Cockpit's Approve button).
- POST /v1/genesis/runs/{id}/cancel   "Vazgeç" while active.

Owner-gated at the router level (``require_owner_session``) — the SAME
discipline ``app.appfactory.routes`` follows: a generated capability's
approval must never be one unauthenticated HTTP call away.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.actions.confirmation_gate import CONFIRM_SOURCE_REST, Confirmation
from app.evolution.errors import EvolutionError
from app.genesis.service import GenesisService
from app.identity.dependencies import require_owner_session

router = APIRouter(
    prefix="/v1/genesis", tags=["genesis"], dependencies=[Depends(require_owner_session)]
)


def _service(request: Request) -> GenesisService:
    return request.app.state.genesis.service


def _http_error(exc: EvolutionError) -> HTTPException:
    status_code = 404 if str(exc.error_class) == "not_found" else 422
    return HTTPException(
        status_code=status_code,
        detail={"code": str(exc.error_class), "message": exc.message},
    )


@router.get("/runs")
async def list_runs(request: Request) -> dict[str, Any]:
    service = _service(request)
    runs = await asyncio.to_thread(service.list)
    return {"runs": runs}


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    service = _service(request)
    try:
        return await asyncio.to_thread(service.get, run_id)
    except EvolutionError as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/approve")
async def approve_run(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    service = _service(request)
    owner_session_id = str(request.state.owner_session.session_id)
    confirmation = Confirmation(source=CONFIRM_SOURCE_REST, session_id=owner_session_id)
    try:
        return await asyncio.to_thread(service.approve, run_id, confirmation)
    except EvolutionError as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    service = _service(request)
    try:
        return await asyncio.to_thread(service.cancel, run_id)
    except EvolutionError as exc:
        raise _http_error(exc) from exc


__all__ = ["router"]
