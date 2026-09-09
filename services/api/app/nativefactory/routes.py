"""Native Application Factory REST surface (docs/M28_NATIVE_APP_FACTORY_SPEC.md §4, §6)
for the Cockpit's "Yerel Uygulamalar" panel.

- ``GET  /v1/native``              the builds as rows — a read of ``native_builds``, no
                                   ledger row per poll (the "the panel refreshes, voice
                                   keeps its own ledger row" rule ``app.creative.routes``
                                   and ``app.creative3d.routes`` both document).
- ``GET  /v1/native/{id}``         one build, with what the independent reader found.
- ``GET  /v1/native/{id}/artifact`` the produced file itself, owner-gated.
- ``GET  /v1/native/toolchain``    what this machine can actually build, measured now.

Owner-gated at the router level. Two things this surface deliberately does NOT do:

* **It does not start a build.** A build compiles code and writes files under an
  authorised root; it belongs to the executive graph and the voice path, where it is
  bounded, resumable and attributable. A REST verb that kicked off a twenty-minute
  compile from a panel refresh would be a fifth way to start work nobody was watching.
* **It does not report a state the row does not hold.** Every field below is read from
  the row. `verified` in this response means a reader read the file — the same thing it
  means everywhere else in the milestone.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse

from app.identity.dependencies import require_owner_session
from app.nativefactory import service as native_service
from app.nativefactory.models import NativeBuildRow
from app.nativefactory.stacks import detect

router = APIRouter(
    prefix="/v1/native", tags=["native"], dependencies=[Depends(require_owner_session)]
)

#: A panel asks for a page, not a history. The same bound the other factories' lists use.
MAX_LIST = 100


def _sessions(request: Request) -> Any:
    """The same session source `app/creative/routes.py` and its neighbours read through."""
    return request.app.state.artifacts


def _row_dict(row: NativeBuildRow) -> dict[str, Any]:
    """The wire shape. Only what the row holds — nothing computed, nothing hoped."""
    artifact = row.artifact_json or {}
    return {
        "id": str(row.id),
        "slug": row.slug,
        "display_name": row.display_name,
        "stack": row.stack,
        "template": row.template,
        "target": row.target,
        "version": row.version,
        "state": row.state,
        "attempt": row.attempt,
        "artifact": {
            "name": Path(str(row.artifact_path)).name if row.artifact_path else None,
            "size_bytes": artifact.get("size_bytes"),
            "sha256": artifact.get("sha256"),
            "version": artifact.get("version"),
            "architecture": artifact.get("architecture"),
            "subsystem": artifact.get("subsystem"),
            "identity": artifact.get("identity"),
        }
        if artifact
        else None,
        "verdict": row.verdict_json,
        "tests": row.tests_json,
        "error_class": row.error_class,
        "error_message": row.error_message,
        # The sentence the owner would hear, composed from this same row - so the panel
        # and the voice can never say different things about one build.
        "speech": native_service.receipt_for(row),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("")
async def list_builds(request: Request, limit: int = 50) -> dict[str, Any]:
    artifacts = _sessions(request)
    bounded = min(max(limit, 1), MAX_LIST)

    def load() -> list[dict[str, Any]]:
        with artifacts.session() as db:
            return [_row_dict(row) for row in native_service.list_builds(db, limit=bounded)]

    return {"builds": await asyncio.to_thread(load)}


@router.get("/toolchain")
async def toolchain(request: Request) -> dict[str, Any]:
    """What this machine can build, MEASURED now rather than remembered.

    The panel shows this so an owner looking at an `unavailable` row can see the reason in
    the same place, instead of being told a target failed and left to guess why.
    """
    del request
    # Measured off the event loop: `detect()` shells out to `dotnet --version`.
    facts = await asyncio.to_thread(detect)
    return {
        "facts": facts.as_dict(),
        "can": {
            "windows": facts.can_build_windows,
            "msix": facts.can_package_msix,
            "android": facts.can_build_android,
            # Not a capability that could become true here, so it is stated as a fact
            # rather than a flag someone might try to flip.
            "ios": False,
        },
    }


@router.get("/{build_id}")
async def get_build(build_id: uuid.UUID, request: Request) -> dict[str, Any]:
    artifacts = _sessions(request)

    def load() -> dict[str, Any] | None:
        with artifacts.session() as db:
            row = native_service.get_build(db, build_id)
            return _row_dict(row) if row is not None else None

    found = await asyncio.to_thread(load)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown native build")
    return found


@router.get("/{build_id}/artifact")
async def get_artifact(build_id: uuid.UUID, request: Request) -> Response:
    """The produced file. Owner-gated, never a bare-token route.

    A build with no artefact answers 409 rather than 404: the build exists and the file
    does not, and telling those apart is the difference between "I never made that" and
    "I made it and it did not produce anything".
    """
    artifacts = _sessions(request)

    def load() -> tuple[str | None, str | None, str] | None:
        with artifacts.session() as db:
            row = native_service.get_build(db, build_id)
            if row is None:
                return None
            return (row.artifact_path, row.error_class, native_service.receipt_for(row))

    found = await asyncio.to_thread(load)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown native build")
    artifact_path, error_class, speech = found
    if not artifact_path:
        raise HTTPException(
            status_code=409,
            detail={"error_class": error_class or "no_artifact", "message": speech},
        )
    path = Path(artifact_path)
    if not path.exists():
        raise HTTPException(
            status_code=410,
            detail={
                "error_class": "artifact_gone",
                "message": "Üretilen dosya artık yerinde değil efendim.",
            },
        )
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


__all__ = ["MAX_LIST", "router"]
