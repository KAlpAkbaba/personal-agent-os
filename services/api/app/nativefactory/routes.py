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

from app.errors.owner import log_and_detail
from app.identity.dependencies import require_owner_session
from app.nativefactory import service as native_service
from app.nativefactory.models import NativeBuildRow
from app.nativefactory.stacks import detect

#: B33 req 456: "the file is gone" - here AND on the device (or the device cannot be asked).
#: A module constant so the owner-language dictionary test sees the class declared.
ERROR_ARTIFACT_GONE = "artifact_gone"

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
            "name": native_service.artifact_file_name(row.artifact_path),
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
        # B49 (req 479): the reason stated beside the fact - an iOS app needs macOS and
        # Xcode, which this product's machines do not have.
        "ios_reason": "macos_required",
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
    if path.exists():
        return FileResponse(path, media_type="application/octet-stream", filename=path.name)

    # B33 req 456: a device build's artefact lives on the DEVICE (a Windows path a Linux
    # Cloud Core cannot stat). Until this batch that was a 410 in production, every time.
    # The bytes are pulled off the device in bounded chunks and hash-verified; 410 is now
    # only what it says - the device does not have it either.
    def pull() -> tuple[bytes, str, str] | HTTPException:
        from app.executive.activities import get_device_action
        from app.nativefactory.device_lifecycle import ArtifactPullError, pull_artifact

        try:
            device = get_device_action()
        except Exception as exc:  # noqa: BLE001 - no device port on this process
            # The exception goes to the log; the owner reads a sentence (app.errors).
            no_port = log_and_detail(
                ERROR_ARTIFACT_GONE,
                exc,
                specific="Üretilen dosya burada yok ve cihaza ulaşılamıyor efendim.",
                details={"reason": "device_port_unavailable"},
                where="native artifact pull: device port",
            )
            return HTTPException(status_code=410, detail=no_port)
        with artifacts.session() as db:
            row = native_service.get_build(db, build_id)
            if row is None:
                return HTTPException(status_code=404, detail="unknown native build")
            try:
                return pull_artifact(device, row)
            except ArtifactPullError as exc:
                # Only a read that DID reach the device and came back wrong is a gateway
                # failure; a device that has no such file, or no device to ask, is "gone".
                broken = exc.error_class in ("artifact_mismatch", "too_large")
                not_pulled = log_and_detail(
                    exc.error_class if broken else ERROR_ARTIFACT_GONE,
                    exc,
                    specific=(
                        "Dosyayı cihazdan çekemedim efendim: gelen parçalar cihazın "
                        "söylediği özetle uyuşmadı ya da dosya sınırın üstünde."
                        if broken
                        else "Üretilen dosya cihazda da yerinde değil efendim."
                    ),
                    details={"reason": exc.error_class},
                    where="native artifact pull",
                )
                return HTTPException(status_code=502 if broken else 410, detail=not_pulled)

    pulled = await asyncio.to_thread(pull)
    if isinstance(pulled, HTTPException):
        raise pulled
    blob, name, sha256 = pulled
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Artifact-Sha256": sha256,
            "X-Artifact-Source": "device",
        },
    )


__all__ = ["MAX_LIST", "router"]
