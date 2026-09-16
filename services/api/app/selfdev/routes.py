"""``/v1/selfdev``: the self-development queue's REST surface (B35 req 581, 583, 609, 615,
621). Every route requires an owner session: the Cockpit approves here, the owner assigns
here, and the dev-machine worker claims and finishes here with the owner's token.

Approve/reject mint an :class:`OwnerCapability` from the SESSION the dependency verified -
never from a body field - exactly as ``/v1/evolution/opportunities/{id}/approve`` does.
Approval is a recorded decision and not a promotion (req 624).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.evolution.authority import AuthorityError, mint_owner_capability
from app.identity.dependencies import require_owner_session
from app.logging import get_logger
from app.selfdev.models import DEFECT_KIND_BUG, SOURCE_OWNER_REST, STATES
from app.selfdev.service import (
    ERROR_DEFECT_NOT_FOUND,
    ERROR_INVALID_DEFECT,
    ERROR_NOT_AWAITING_OWNER,
    ERROR_NOT_CLAIMABLE,
    ERROR_OPPORTUNITY_ALREADY_QUEUED,
    ERROR_SELFDEV_DISABLED,
    SelfDevService,
    owner_error,
)

logger = get_logger("app.selfdev.routes")

SELFDEV_ROUTES_VERSION = 1

router = APIRouter(
    prefix="/v1/selfdev", tags=["selfdev"], dependencies=[Depends(require_owner_session)]
)

MAX_LIST = 100


class DefectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    evidence: str = Field(default="", max_length=8000)
    kind: str = Field(default=DEFECT_KIND_BUG, pattern="^(bug|feature)$")
    scope: list[str] = Field(default_factory=list, max_length=32)
    failing_test: str | None = Field(default=None, max_length=400)
    priority: int = Field(default=2, ge=0, le=3)


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=2000)


class ClaimBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=120)


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=120)


class FinishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record: dict[str, Any]


class CIBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = Field(pattern="^(success|failure|pending|none)$")
    detail: str = Field(default="", max_length=4000)


def _artifacts(request: Request) -> Any:
    return request.app.state.artifacts


def _service(request: Request) -> SelfDevService:
    return request.app.state.selfdev_service


def _evolution(request: Request) -> Any:
    return getattr(request.app.state, "evolution", None)


def _status_for(code: str) -> int:
    if code == ERROR_DEFECT_NOT_FOUND:
        return 404
    if code in (ERROR_NOT_AWAITING_OWNER, ERROR_NOT_CLAIMABLE, ERROR_OPPORTUNITY_ALREADY_QUEUED):
        return 409
    if code == ERROR_SELFDEV_DISABLED:
        return 503
    return 422


async def _run(fn: Any, *args: Any, where: str, **kwargs: Any) -> Any:
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except LookupError as exc:
        code = str(exc) or ERROR_DEFECT_NOT_FOUND
        not_found = owner_error(code, exc, where=where)
        raise HTTPException(status_code=_status_for(code), detail=not_found) from exc
    except ValueError as exc:
        code = str(exc) if str(exc) in _KNOWN else ERROR_INVALID_DEFECT
        refused = owner_error(code, exc, where=where)
        raise HTTPException(status_code=_status_for(code), detail=refused) from exc
    except AuthorityError as exc:
        reason = str(exc.details.get("reason") or "") if hasattr(exc, "details") else ""
        code = ERROR_SELFDEV_DISABLED if reason == ERROR_SELFDEV_DISABLED else "not_authorized"
        denied = owner_error(code, exc, where=where)
        raise HTTPException(
            status_code=_status_for(code) if code != "not_authorized" else 403, detail=denied
        ) from exc


_KNOWN = frozenset(
    {
        ERROR_INVALID_DEFECT,
        ERROR_NOT_AWAITING_OWNER,
        ERROR_NOT_CLAIMABLE,
        ERROR_OPPORTUNITY_ALREADY_QUEUED,
    }
)


@router.post("/defects", status_code=201)
async def create_defect(request: Request, body: DefectBody) -> dict[str, Any]:
    """Req 581: a defect (or a feature) from the product surface, not a hand-written file."""
    artifacts = _artifacts(request)
    service = _service(request)
    owner_session_id = str(request.state.owner_session.session_id)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            row = service.intake(
                db,
                title=body.title,
                evidence=body.evidence,
                source=SOURCE_OWNER_REST,
                kind=body.kind,
                scope=body.scope,
                failing_test=body.failing_test,
                session_id=owner_session_id,
                priority=body.priority,
            )
            return {"defect": service.entry(row)}

    return await _run(run, where="selfdev.create_defect")


@router.post("/defects/from-opportunity/{opportunity_id}", status_code=201)
async def create_from_opportunity(request: Request, opportunity_id: uuid.UUID) -> dict[str, Any]:
    """Req 583: the bridge. The opportunity is read through the evolution service (the one
    backlog), turned into a defect, queued once."""
    artifacts = _artifacts(request)
    service = _service(request)
    evolution = _evolution(request)
    owner_session_id = str(request.state.owner_session.session_id)

    def run() -> dict[str, Any]:
        if evolution is None:
            raise LookupError(ERROR_DEFECT_NOT_FOUND)
        try:
            opportunity = evolution.evolution_service.get(opportunity_id)
        except Exception as exc:  # noqa: BLE001 - an unknown opportunity is "not found"
            raise LookupError(ERROR_DEFECT_NOT_FOUND) from exc
        with artifacts.session() as db:
            row = service.intake_opportunity(db, opportunity, session_id=owner_session_id)
            return {"defect": service.entry(row)}

    return await _run(run, where="selfdev.from_opportunity")


@router.get("/defects")
async def list_defects(
    request: Request, state: str | None = None, limit: int = 50
) -> dict[str, Any]:
    artifacts = _artifacts(request)
    service = _service(request)
    if state is not None and state not in STATES:
        raise HTTPException(
            status_code=422,
            detail=owner_error(ERROR_INVALID_DEFECT, ValueError(state), where="selfdev.list"),
        )
    bounded = min(max(limit, 1), MAX_LIST)

    def load() -> dict[str, Any]:
        with artifacts.session() as db:
            return {
                "defects": [service.entry(r) for r in service.list(db, state=state, limit=bounded)]
            }

    return await asyncio.to_thread(load)


@router.get("/defects/pending")
async def list_pending(request: Request) -> dict[str, Any]:
    """Req 609/621: the candidates awaiting the owner, newest first."""
    artifacts = _artifacts(request)
    service = _service(request)

    def load() -> dict[str, Any]:
        with artifacts.session() as db:
            return {"pending": [service.entry(r) for r in service.pending(db)]}

    return await asyncio.to_thread(load)


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    """Req 615/618-620: the queue's counts and every bound beside what it has consumed."""
    artifacts = _artifacts(request)
    service = _service(request)

    def load() -> dict[str, Any]:
        with artifacts.session() as db:
            return service.status(db)

    return await asyncio.to_thread(load)


@router.get("/defects/{defect_id}")
async def get_defect(request: Request, defect_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)
    service = _service(request)

    def load() -> dict[str, Any]:
        with artifacts.session() as db:
            return {"defect": service.entry(service.get(db, defect_id))}

    return await _run(load, where="selfdev.get")


@router.post("/defects/{defect_id}/approve")
async def approve_defect(
    request: Request,
    defect_id: uuid.UUID,
    body: DecisionBody,
    session=Depends(require_owner_session),  # noqa: B008 - FastAPI dependency
) -> dict[str, Any]:
    """The owner's yes, from the verified session. Recorded; promotes nothing (req 624)."""
    artifacts = _artifacts(request)
    service = _service(request)
    capability = mint_owner_capability(session)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            row = service.approve(db, defect_id, capability, note=body.note)
            return {"defect": service.entry(row), "promoted": False}

    return await _run(run, where="selfdev.approve")


@router.post("/defects/{defect_id}/reject")
async def reject_defect(
    request: Request,
    defect_id: uuid.UUID,
    body: DecisionBody,
    session=Depends(require_owner_session),  # noqa: B008 - FastAPI dependency
) -> dict[str, Any]:
    artifacts = _artifacts(request)
    service = _service(request)
    capability = mint_owner_capability(session)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            row = service.reject(db, defect_id, capability, note=body.note)
            return {"defect": service.entry(row), "promoted": False}

    return await _run(run, where="selfdev.reject")


# ------------------------------------------------------------------ the worker's side


@router.post("/worker/claim")
async def worker_claim(request: Request, body: ClaimBody) -> dict[str, Any]:
    """Req 615/620: the worker asks for the next defect; a refusal is a named reason."""
    artifacts = _artifacts(request)
    service = _service(request)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            service.expire_stale_claims(db)
            verdict = service.claim(db, worker_id=body.worker_id)
            return {
                "defect": service.entry(verdict.row) if verdict.row is not None else None,
                "reason": verdict.reason,
                "budget": service.budget_report(db),
            }

    return await asyncio.to_thread(run)


@router.post("/defects/{defect_id}/start")
async def worker_start(request: Request, defect_id: uuid.UUID, body: StartBody) -> dict[str, Any]:
    artifacts = _artifacts(request)
    service = _service(request)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            return {"defect": service.entry(service.start(db, defect_id, run_id=body.run_id))}

    return await _run(run, where="selfdev.start")


@router.post("/defects/{defect_id}/finish")
async def worker_finish(request: Request, defect_id: uuid.UUID, body: FinishBody) -> dict[str, Any]:
    """The engine's record lands on the row; the promotion class is consumed (req 585)."""
    artifacts = _artifacts(request)
    service = _service(request)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            return {"defect": service.entry(service.finish(db, defect_id, body.record))}

    return await _run(run, where="selfdev.finish")


@router.post("/defects/{defect_id}/ci")
async def worker_ci(request: Request, defect_id: uuid.UUID, body: CIBody) -> dict[str, Any]:
    """Req 603: CI's verdict on a waiting candidate; a red one opens one bounded follow-up."""
    artifacts = _artifacts(request)
    service = _service(request)

    def run() -> dict[str, Any]:
        with artifacts.session() as db:
            follow_up = service.reconcile_ci(db, defect_id, state=body.state, detail=body.detail)
            row = service.get(db, defect_id)
            return {
                "defect": service.entry(row),
                "follow_up": service.entry(follow_up) if follow_up is not None else None,
            }

    return await _run(run, where="selfdev.ci")
