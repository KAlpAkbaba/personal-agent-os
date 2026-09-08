"""3D Creation REST surface (docs/M25_CREATIVE_3D_SPEC.md §6) for the Cockpit's "3B
Sahne" panel.

- GET  /v1/scenes                    the scenes as rows (tool, project/scene, state,
                                     object count) — a read of ``scenes``, no ledger row
                                     per poll (the panel refreshes; the voice family
                                     keeps its own ledger row).
- GET  /v1/scenes/{id}                one scene, with its last inspection.
- GET  /v1/scenes/{id}/render         the last render PNG, from the object store,
                                     owner-gated (never a bare-token route like the M22
                                     artifact fetch — a scene render has no cross-device
                                     handoff need).
- POST /v1/scenes/{id}/render         the SAME ``SceneService.render`` the voice tool
                                     calls (``tools_scene``), on the SAME device port
                                     every family holds — the Cockpit's "Render al" and
                                     "Render al." by voice can never disagree about what
                                     a render is.
- POST /v1/scenes/{id}/inspect        the SAME ``SceneService.inspect``.

Owner-gated at the router level (``require_owner_session``) — a Blender/Unity process on
the owner's machine must never be one unauthenticated HTTP call away. A receipt that
refused answers 422 with the receipt's own error class and sentence; an unknown id
answers 404; an executed receipt answers 200 with the read-back.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select

from app.actions.receipt import EXECUTION_EXECUTED
from app.creative3d.models import SceneRow
from app.creative3d.service import SceneService
from app.identity.dependencies import require_owner_session

router = APIRouter(
    prefix="/v1/scenes", tags=["scenes"], dependencies=[Depends(require_owner_session)]
)


def _service(request: Request) -> SceneService:
    return request.app.state.creative3d_service


def _artifacts(request: Request) -> Any:
    return request.app.state.artifacts


def _row_dict(row: SceneRow) -> dict[str, Any]:
    objects = len((row.inspection_json or {}).get("objects") or [])
    return {
        "id": str(row.id),
        "tool": row.tool,
        "project": row.project,
        "scene": row.scene,
        "state": row.state,
        "objects": objects,
        "has_render": bool(row.render_object_key),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("")
async def list_scenes(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> list[dict[str, Any]]:
        with artifacts.session() as db:
            rows = (
                db.execute(select(SceneRow).order_by(SceneRow.created_at.desc()).limit(50))
                .scalars()
                .all()
            )
            return [_row_dict(r) for r in rows]

    return {"scenes": await asyncio.to_thread(load)}


@router.get("/{scene_id}")
async def get_scene(request: Request, scene_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> dict[str, Any] | None:
        with artifacts.session() as db:
            row = db.get(SceneRow, scene_id)
            if row is None:
                return None
            out = _row_dict(row)
            out["inspection"] = row.inspection_json
            out["compare"] = row.compare_json
            return out

    result = await asyncio.to_thread(load)
    if result is None:
        raise HTTPException(status_code=404)
    return result


@router.get("/{scene_id}/render")
async def get_render(request: Request, scene_id: uuid.UUID) -> Response:
    artifacts = _artifacts(request)

    def load() -> bytes | None:
        with artifacts.session() as db:
            row = db.get(SceneRow, scene_id)
            if row is None or row.render_object_key is None:
                return None
            try:
                return artifacts.store.get(row.render_object_key)
            except KeyError:
                return None

    data = await asyncio.to_thread(load)
    if data is None:
        raise HTTPException(status_code=404)
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "no-store", "Content-Length": str(len(data))},
    )


def _respond(receipt: dict[str, Any], scene_id: uuid.UUID) -> dict[str, Any]:
    """An executed receipt is the read-back; a refusal is the receipt's own error
    class and sentence as a 422; an unknown id is a 404. Never a 200 for something
    that did not happen (the same rule ``app.appfactory.routes._respond``
    documents)."""
    error_class = receipt.get("error_class")
    execution = receipt.get("execution_status")
    if execution != EXECUTION_EXECUTED:
        status_code = 404 if error_class == "not_found" else 422
        raise HTTPException(
            status_code=status_code,
            detail={
                "code": error_class or execution or "refused",
                "message": receipt.get("speech"),
            },
        )
    return {
        "scene_id": str(scene_id),
        "state": receipt.get("state"),
        "objects": receipt.get("objects"),
        "inspection": receipt.get("inspection"),
        "compare": receipt.get("compare"),
        "speech": receipt.get("speech"),
        "error_class": error_class,
    }


async def _act(request: Request, scene_id: uuid.UUID, action: str) -> dict[str, Any]:
    service = _service(request)
    artifacts = _artifacts(request)
    device_action = getattr(request.app.state, "device_action", None)
    owner_session_id = str(request.state.owner_session.session_id)

    def do() -> dict[str, Any]:
        with artifacts.session() as db:
            if db.get(SceneRow, scene_id) is None:
                return {
                    "execution_status": "refused",
                    "error_class": "not_found",
                    "speech": "Böyle bir sahne yok.",
                }
            if action == "render":
                return service.render(
                    db, device_action, target=str(scene_id), session_id=f"rest:{owner_session_id}"
                )
            return service.inspect(
                db, device_action, target=str(scene_id), session_id=f"rest:{owner_session_id}"
            )

    return _respond(await asyncio.to_thread(do), scene_id)


@router.post("/{scene_id}/render")
async def render_scene(request: Request, scene_id: uuid.UUID) -> dict[str, Any]:
    return await _act(request, scene_id, "render")


@router.post("/{scene_id}/inspect")
async def inspect_scene(request: Request, scene_id: uuid.UUID) -> dict[str, Any]:
    return await _act(request, scene_id, "inspect")


__all__ = ["router"]
