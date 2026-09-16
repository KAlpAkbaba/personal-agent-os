"""Creative Tools Operator REST surface (docs/M27_CREATIVE_TOOLS_SPEC.md §6) for the
Cockpit's "Yaratıcı" panel.

- GET  /v1/creative                  the runs as rows (tool, name, state) — a read of
                                     ``creative_runs``, no ledger row per poll (the same
                                     "the panel refreshes, voice keeps its own ledger
                                     row" rule ``app.creative3d.routes`` documents).
- GET  /v1/creative/{id}              one run, with its last inspection and comparison.
- GET  /v1/creative/{id}/output       the last produced file, from the object store,
                                     owner-gated (never a bare-token route — a Paint
                                     round trip has no cross-device handoff need, the
                                     same reasoning ``app.creative3d.routes`` gives its
                                     own render route).

Owner-gated at the router level (``require_owner_session``). A receipt that refused
answers 422 with the receipt's own error class and sentence; an unknown id answers 404.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.creative.models import CreativeRunRow
from app.identity.dependencies import require_owner_session

router = APIRouter(
    prefix="/v1/creative", tags=["creative"], dependencies=[Depends(require_owner_session)]
)


def _service(request: Request) -> Any:
    return request.app.state.creative_service


def _artifacts(request: Request) -> Any:
    return request.app.state.artifacts


def _row_dict(row: CreativeRunRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "tool": row.tool,
        "name": row.name,
        "state": row.state,
        "rounds": row.round_count,
        "has_output": bool(row.output_object_key),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# `/runs` FIRST, and before `/{run_id}`: FastAPI matches in declaration order, so a literal
# segment declared after a path parameter is swallowed by it. That is exactly what happened -
# the Cockpit asked for `/v1/creative/runs`, `run_id: uuid.UUID` refused "runs" as a UUID, and
# the panel served HTTP 422 for every owner, for ever (B03 req 510/715). The bare prefix stays
# as an alias so nothing that already worked stops working.
@router.get("/runs")
@router.get("")
async def list_creative_runs(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> list[dict[str, Any]]:
        with artifacts.session() as db:
            rows = (
                db.execute(
                    select(CreativeRunRow).order_by(CreativeRunRow.created_at.desc()).limit(50)
                )
                .scalars()
                .all()
            )
            return [_row_dict(r) for r in rows]

    return {"runs": await asyncio.to_thread(load)}


# ------------------------------------------------------------ B43: the lifecycle


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=1000)
    name: str = Field(min_length=1, max_length=64)
    width: int = Field(default=1024, ge=16, le=8192)
    height: int = Field(default=1024, ge=16, le=8192)
    expectation: str | None = Field(default=None, max_length=200)
    tool: str = "paint"


class DeliverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application: str | None = Field(default=None, max_length=32)


class DriveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: list[dict[str, Any]] = Field(min_length=1, max_length=12)
    path: str | None = Field(default=None, max_length=1024)


class EnhanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = "auto"


def _respond(receipt: dict[str, Any]) -> dict[str, Any]:
    """An executed receipt is the read-back; a refusal is a 422 with its own error class
    and sentence; a clarification is a 409 (the route cannot ask back)."""
    if receipt.get("status") == "needs_clarification":
        raise HTTPException(
            status_code=409,
            detail={"code": "needs_clarification", "message": receipt.get("speech")},
        )
    if receipt.get("execution_status") not in ("executed", "noop"):
        raise HTTPException(
            status_code=422,
            detail={
                "code": receipt.get("error_class") or "refused",
                "message": receipt.get("speech"),
            },
        )
    return receipt


@router.post("/generate", status_code=201)
async def generate_creative(request: Request, body: GenerateRequest) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)
    owner = str(request.state.owner_session.session_id)

    def do() -> dict[str, Any]:
        with artifacts.session() as db:
            return service.generate(
                db,
                prompt=body.prompt,
                name=body.name,
                width=body.width,
                height=body.height,
                tool=body.tool,
                expectation=body.expectation,
                session_id=f"rest:{owner}",
            )

    return _respond(await asyncio.to_thread(do))


async def _run_action(
    request: Request, run_id: uuid.UUID, action: str, **kwargs: Any
) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)
    owner = str(request.state.owner_session.session_id)

    def do() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(CreativeRunRow, run_id) is None:
                raise HTTPException(status_code=404)
            method = getattr(service, action)
            return method(db, target=str(run_id), session_id=f"rest:{owner}", **kwargs)

    return _respond(await asyncio.to_thread(do))


@router.post("/{run_id}/undo")
async def undo_creative(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    return await _run_action(request, run_id, "undo")


@router.post("/{run_id}/redo")
async def redo_creative(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    return await _run_action(request, run_id, "redo")


@router.post("/{run_id}/enhance")
async def enhance_creative(
    request: Request, run_id: uuid.UUID, body: EnhanceRequest | None = None
) -> dict[str, Any]:
    return await _run_action(request, run_id, "enhance", kind=(body.kind if body else "auto"))


@router.get("/{run_id}/history")
async def creative_history(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)

    def load() -> dict[str, Any] | None:
        with artifacts.session() as db:
            # A named run that does not exist is a 404 - never the focused or newest run
            # the voice resolver would fall back to.
            if db.get(CreativeRunRow, run_id) is None:
                return None
            return service.history(db, target=str(run_id))

    out = await asyncio.to_thread(load)
    if out is None:
        raise HTTPException(status_code=404)
    return out


@router.post("/{run_id}/deliver")
async def deliver_creative(
    request: Request, run_id: uuid.UUID, body: DeliverRequest | None = None
) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)
    device_action = getattr(request.app.state, "device_action", None)
    owner = str(request.state.owner_session.session_id)
    base_url = getattr(artifacts.settings, "artifact_download_origin", "") or ""

    def do() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(CreativeRunRow, run_id) is None:
                raise HTTPException(status_code=404)
            return service.deliver(
                db,
                device_action,
                target=str(run_id),
                base_url=base_url,
                session_id=f"rest:{owner}",
                application=(body.application if body else None),
                actor_kind="owner_rest",
            )

    return _respond(await asyncio.to_thread(do))


@router.post("/{run_id}/drive")
async def drive_creative(request: Request, run_id: uuid.UUID, body: DriveRequest) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)
    device_action = getattr(request.app.state, "device_action", None)
    owner = str(request.state.owner_session.session_id)

    def do() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(CreativeRunRow, run_id) is None:
                raise HTTPException(status_code=404)
            return service.drive(
                db,
                device_action,
                target=str(run_id),
                actions=body.actions,
                path=body.path,
                session_id=f"rest:{owner}",
            )

    return _respond(await asyncio.to_thread(do))


@router.get("/{run_id}")
async def get_creative_run(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> dict[str, Any] | None:
        with artifacts.session() as db:
            row = db.get(CreativeRunRow, run_id)
            if row is None:
                return None
            out = _row_dict(row)
            out["inspection"] = row.inspection_json
            out["compare"] = row.compare_json
            out["rounds"] = row.rounds_json
            return out

    result = await asyncio.to_thread(load)
    if result is None:
        raise HTTPException(status_code=404)
    return result


@router.get("/{run_id}/output")
async def get_creative_output(request: Request, run_id: uuid.UUID) -> Response:
    artifacts = _artifacts(request)

    def load() -> tuple[bytes, str] | None:
        with artifacts.session() as db:
            row = db.get(CreativeRunRow, run_id)
            if row is None or row.output_object_key is None:
                return None
            try:
                data = artifacts.store.get(row.output_object_key)
            except KeyError:
                return None
            content_type = {
                "png": "image/png",
                "jpg": "image/jpeg",
                "pdf": "application/pdf",
                "svg": "image/svg+xml",
            }.get(
                row.output_name.rsplit(".", 1)[-1] if row.output_name else "png",
                "application/octet-stream",
            )
            return data, content_type

    result = await asyncio.to_thread(load)
    if result is None:
        raise HTTPException(status_code=404)
    data, content_type = result
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "no-store", "Content-Length": str(len(data))},
    )


__all__ = ["router"]
