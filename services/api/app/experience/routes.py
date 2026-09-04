"""Experience REST surface: /v1/experience.

Owner-gated exactly like app.ledger.routes and app.memory.routes (single
owner authority model, ADR-0027): every route requires
``require_owner_session`` at the router level.

NOT registered in ``app.main`` by this package — the integrator wires this
router in alongside the ``experience_lessons`` migration (task brief).

DB work reuses ``request.app.state.artifacts`` (the same generic SQLAlchemy
session source ``app.ledger.routes``/``app.research.routes`` already use —
the ledger and the experience tables live in the same canonical PostgreSQL
database) and ``request.app.state.memory.embedder`` (so the SAME embedder
model already configured for memory retrieval is used here, never a second
one). All DB work runs in ``asyncio.to_thread`` like every other module.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.experience import compiler as experience_compiler
from app.experience import engine as experience_engine
from app.experience.compiler import (
    AUTO_PROMOTE_SCORE_THRESHOLD,
    RECURRENCE_SATURATION,
    W_CONFIDENCE,
    W_GENERALIZABILITY,
    W_OWNER_RELEVANCE,
    W_RECURRENCE,
    W_RISK_PENALTY,
)
from app.experience.engine import SEMANTIC_MIN_CORROBORATION
from app.experience.models import (
    LESSON_STATUSES,
    STATUS_CANDIDATE,
    STATUS_PROMOTED,
    STATUS_REJECTED,
    ExperienceLessonRow,
)
from app.identity.dependencies import require_owner_session
from app.logging import get_logger
from app.memory import service as memory_service
from app.memory.errors import MemoryErrorClass, MemorySubsystemError
from app.memory.types import MemoryClass

logger = get_logger("app.experience.routes")

router = APIRouter(prefix="/v1/experience", dependencies=[Depends(require_owner_session)])

MAX_LESSON_LIMIT = 200

_HTTP_STATUS = {
    MemoryErrorClass.SECRET_REJECTED: 422,
    MemoryErrorClass.VALIDATION_ERROR: 422,
    MemoryErrorClass.NOT_FOUND: 404,
    MemoryErrorClass.EXPLICIT_PROTECTED: 403,
    MemoryErrorClass.BACKEND_NOT_IMPLEMENTED: 501,
    MemoryErrorClass.INTERNAL_BUG: 500,
}


def _artifacts_session(request: Request):
    return request.app.state.artifacts.session()


def _embedder(request: Request):
    return request.app.state.memory.embedder


def _http_error(exc: MemorySubsystemError) -> HTTPException:
    return HTTPException(
        status_code=_HTTP_STATUS.get(exc.error_class, 500),
        detail={"error_class": str(exc.error_class), "message": exc.message},
    )


async def _run(fn):
    try:
        return await asyncio.to_thread(fn)
    except MemorySubsystemError as exc:
        raise _http_error(exc) from exc


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _lesson_payload(row: ExperienceLessonRow) -> dict[str, Any]:
    return {
        "lesson_id": str(row.lesson_id),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "title": row.title,
        "statement": row.statement,
        "incident_refs": row.incident_refs,
        "evidence_refs": row.evidence_refs,
        "root_cause": row.root_cause,
        "resolution": row.resolution,
        "scope": row.scope,
        "recurrence": row.recurrence,
        "confidence": row.confidence,
        "generalizability": row.generalizability,
        "owner_relevance": row.owner_relevance,
        "risk_overgeneralization": row.risk_overgeneralization,
        "score": row.score,
        "status": row.status,
        "promoted_memory_id": str(row.promoted_memory_id) if row.promoted_memory_id else None,
        "source": row.source,
        "source_ref": row.source_ref,
        "detail": row.detail_json,
    }


# ------------------------------------------------------------------- ingest


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    since: datetime | None = None


_DEFAULT_INGEST_REQUEST = IngestRequest()


@router.post("/ingest")
async def ingest(request: Request, body: IngestRequest = _DEFAULT_INGEST_REQUEST) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        with _artifacts_session(request) as session:
            report = experience_engine.ingest(
                session, embedder=_embedder(request), since=body.since
            )
            return report.as_dict()

    report = await _run(run)
    logger.info("experience_ingested", **{k: v for k, v in report.items() if k != "errors"})
    return {"report": report}


# ------------------------------------------------------------------- compile


@router.post("/compile")
async def compile_lessons(request: Request) -> dict[str, Any]:
    def run() -> list[dict[str, Any]]:
        with _artifacts_session(request) as session:
            experience_compiler.compile_lessons(session, embedder=_embedder(request))
            rows = (
                session.execute(
                    select(ExperienceLessonRow).order_by(ExperienceLessonRow.score.desc())
                )
                .scalars()
                .all()
            )
            return [_lesson_payload(row) for row in rows]

    lessons = await _run(run)
    logger.info("experience_compiled", count=len(lessons))
    return {"lessons": lessons, "count": len(lessons)}


# ------------------------------------------------------------------- lessons


@router.get("/lessons")
async def list_lessons(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_LESSON_LIMIT),
) -> dict[str, Any]:
    if status is not None and status not in LESSON_STATUSES:
        raise HTTPException(status_code=422, detail={"message": f"unknown status {status!r}"})

    def run() -> list[dict[str, Any]]:
        with _artifacts_session(request) as session:
            stmt = select(ExperienceLessonRow)
            if status is not None:
                stmt = stmt.where(ExperienceLessonRow.status == status)
            stmt = stmt.order_by(ExperienceLessonRow.score.desc()).limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [_lesson_payload(row) for row in rows]

    lessons = await _run(run)
    return {"lessons": lessons, "count": len(lessons)}


def _get_lesson(session: Session, lesson_id: uuid.UUID) -> ExperienceLessonRow:
    row = session.get(ExperienceLessonRow, lesson_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"message": f"unknown lesson {lesson_id}"})
    return row


@router.post("/lessons/{lesson_id}/promote", status_code=201)
async def promote_lesson(request: Request, lesson_id: uuid.UUID) -> dict[str, Any]:
    """Owner-authorized promotion: the ONE path in this package that calls
    ``app.memory.service.remember_explicit`` (Actor.OWNER, durable
    immediately, confidence 1.0) — legitimate here because reaching this
    route at all already required an owner session (router-level dependency),
    unlike the compiler's own auto-write (see app.experience.compiler
    ``_auto_write_memory``), which is a system inference and never claims
    owner authority."""

    def run() -> dict[str, Any]:
        with _artifacts_session(request) as session:
            row = _get_lesson(session, lesson_id)
            if row.status != STATUS_CANDIDATE:
                raise HTTPException(
                    status_code=409,
                    detail={"message": f"lesson {lesson_id} is already {row.status}"},
                )
            result = memory_service.remember_explicit(
                session,
                _embedder(request),
                text=row.statement,
                memory_class=MemoryClass.PROCEDURAL,
                key=f"experience.lesson.{row.source_ref}",
                value={
                    "lesson_id": str(row.lesson_id),
                    "root_cause": row.root_cause,
                    "resolution": row.resolution,
                    "scope": row.scope,
                },
                source={
                    "kind": "owner_promotion",
                    "origin": "experience_routes",
                    "lesson_id": str(row.lesson_id),
                    "incident_refs": list(row.incident_refs or []),
                    "evidence_refs": list(row.evidence_refs or []),
                },
            )
            row.status = STATUS_PROMOTED
            row.promoted_memory_id = result.memory_id
            row.detail_json = {
                **(row.detail_json or {}),
                "promotion": {"kind": "owner", "actor": "owner", "stage": result.stage},
            }
            session.commit()
            return _lesson_payload(row)

    payload = await _run(run)
    logger.info("experience_lesson_promoted", lesson_id=str(lesson_id))
    return payload


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=512)


_DEFAULT_REJECT_REQUEST = RejectRequest()


@router.post("/lessons/{lesson_id}/reject")
async def reject_lesson(
    request: Request, lesson_id: uuid.UUID, body: RejectRequest = _DEFAULT_REJECT_REQUEST
) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        with _artifacts_session(request) as session:
            row = _get_lesson(session, lesson_id)
            if row.status != STATUS_CANDIDATE:
                raise HTTPException(
                    status_code=409,
                    detail={"message": f"lesson {lesson_id} is already {row.status}"},
                )
            row.status = STATUS_REJECTED
            row.detail_json = {**(row.detail_json or {}), "rejection_reason": body.reason}
            session.commit()
            return _lesson_payload(row)

    payload = await _run(run)
    logger.info("experience_lesson_rejected", lesson_id=str(lesson_id))
    return payload


# -------------------------------------------------------------------- policy


@router.get("/policy")
async def get_policy() -> dict[str, Any]:
    """Side-effect free (mirrors GET /v1/ledger/policy / /v1/research/policy):
    an owner script can probe thresholds/weights before relying on this
    surface's scoring being unchanged."""
    from app.experience import EXPERIENCE_VERSION

    return {
        "experience_version": EXPERIENCE_VERSION,
        "lesson_statuses": sorted(LESSON_STATUSES),
        "thresholds": {
            "auto_promote_score": AUTO_PROMOTE_SCORE_THRESHOLD,
            "recurrence_saturation": RECURRENCE_SATURATION,
            "semantic_min_corroboration": SEMANTIC_MIN_CORROBORATION,
        },
        "weights": {
            "generalizability": W_GENERALIZABILITY,
            "confidence": W_CONFIDENCE,
            "recurrence": W_RECURRENCE,
            "owner_relevance": W_OWNER_RELEVANCE,
            "risk_overgeneralization_penalty": W_RISK_PENALTY,
        },
    }


__all__ = ["router"]
