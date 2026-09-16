"""``/v1/operator/missions``: the operator mission's REST surface (B39 req 128-130).

Every route requires an owner session. A start plans the sentence, persists the row and
starts the durable workflow; approve/pause/resume/cancel write the row first (the truth)
and then signal the workflow, exactly as ``app.executive.routes`` does for a run.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from temporalio.client import Client

from app.identity.dependencies import require_owner_session
from app.logging import get_logger
from app.operator import mission_service
from app.operator.mission_models import SOURCE_REST
from app.operator.mission_service import MissionServiceError

logger = get_logger("app.operator.mission_routes")

router = APIRouter(
    prefix="/v1/operator/missions",
    tags=["operator"],
    dependencies=[Depends(require_owner_session)],
)


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=600)
    preview: bool | None = None


async def _temporal_client(request: Request) -> Client:
    settings = request.app.state.artifacts.settings
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


def _error(exc: MissionServiceError) -> HTTPException:
    status = 404 if exc.error_class == "not_found" else 422
    return HTTPException(
        status_code=status, detail={"code": exc.error_class, "message": exc.speech}
    )


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "dependency_unavailable",
            "message": mission_service.WORKFLOW_UNAVAILABLE_TR,
        },
    )


def _mission_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "Böyle bir görev bulamadım."}
        ) from exc


@router.get("")
async def list_missions(request: Request, limit: int = 50) -> dict[str, Any]:
    artifacts = request.app.state.artifacts
    with artifacts.session() as db:
        rows = mission_service.list_missions(db, limit=max(1, min(int(limit), 100)))
        return {"missions": [mission_service.mission_dict(r) for r in rows]}


@router.get("/{mission_id}")
async def get_mission(request: Request, mission_id: str) -> dict[str, Any]:
    artifacts = request.app.state.artifacts
    with artifacts.session() as db:
        try:
            row = mission_service.get_mission(db, _mission_id(mission_id))
        except MissionServiceError as exc:
            raise _error(exc) from exc
        return mission_service.mission_dict(row)


@router.post("")
async def start_mission(request: Request, body: StartBody) -> dict[str, Any]:
    artifacts = request.app.state.artifacts
    try:
        client = await _temporal_client(request)
    except Exception as exc:  # noqa: BLE001 - a typed refusal, not an untyped 500
        raise _unavailable() from exc
    owner_session_id = str(request.state.owner_session.session_id)
    with artifacts.session() as db:
        try:
            row = mission_service.start_mission_db(
                db,
                text=body.text,
                preview=body.preview,
                source=SOURCE_REST,
                session_id=owner_session_id,
            )
        except MissionServiceError as exc:
            raise _error(exc) from exc
        try:
            await mission_service.start_mission_workflow(
                client, row.id, task_queue=artifacts.settings.temporal_task_queue
            )
        except Exception as exc:  # noqa: BLE001 - the row is closed, then refused typed
            mission_service.fail_unstarted(db, row.id, detail=f"{type(exc).__name__}: {exc}")
            raise _unavailable() from exc
        db.refresh(row)
        return mission_service.mission_dict(row)


async def _control(request: Request, mission_id: str, action: str) -> dict[str, Any]:
    artifacts = request.app.state.artifacts
    mid = _mission_id(mission_id)
    db_half = {
        "approve": mission_service.approve_db,
        "pause": mission_service.pause_db,
        "resume": mission_service.resume_db,
        "cancel": mission_service.cancel_db,
    }[action]
    signal_half = {
        "approve": mission_service.approve_signal,
        "pause": mission_service.pause_signal,
        "resume": mission_service.resume_signal,
        "cancel": mission_service.cancel_signal,
    }[action]
    with artifacts.session() as db:
        try:
            row = db_half(db, mid)
        except MissionServiceError as exc:
            raise _error(exc) from exc
        out = mission_service.mission_dict(row)
    try:
        client = await _temporal_client(request)
        await signal_half(client, mid)
        out["signalled"] = True
    except Exception:  # noqa: BLE001 - the row already says what the owner asked
        logger.warning("operator_mission_signal_failed", mission_id=str(mid), action=action)
        out["signalled"] = False
    return out


@router.post("/{mission_id}/approve")
async def approve(request: Request, mission_id: str) -> dict[str, Any]:
    return await _control(request, mission_id, "approve")


@router.post("/{mission_id}/pause")
async def pause(request: Request, mission_id: str) -> dict[str, Any]:
    return await _control(request, mission_id, "pause")


@router.post("/{mission_id}/resume")
async def resume(request: Request, mission_id: str) -> dict[str, Any]:
    return await _control(request, mission_id, "resume")


@router.post("/{mission_id}/cancel")
async def cancel(request: Request, mission_id: str) -> dict[str, Any]:
    return await _control(request, mission_id, "cancel")


__all__ = ["router"]
