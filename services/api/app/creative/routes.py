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
