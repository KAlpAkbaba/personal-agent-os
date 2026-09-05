"""World Model REST surface (overnight plan Phase 5): read-only, owner-gated.

- GET /v1/world                full snapshot (facts + uncertainties)
- GET /v1/world/facts?kind=    facts filtered by truth kind (source_truth |
                                installed_truth | runtime_truth | evidence_truth)
- GET /v1/world/uncertainties  what the snapshot could not determine
- GET /v1/world/policy         WORLD_VERSION + the truth kinds this Cloud Core understands

No writes anywhere in this router — assembling a snapshot never mutates
anything (``app.worldmodel.state`` module docstring).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.artifacts.runtime import ArtifactRuntime
from app.identity.dependencies import require_owner_session
from app.worldmodel.state import WORLD_VERSION, TruthKind, WorldSnapshot, assemble_snapshot

router = APIRouter(prefix="/v1/world", dependencies=[Depends(require_owner_session)])


def _artifacts(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


def _broker_runtime(request: Request) -> Any | None:
    return getattr(request.app.state, "broker", None)


def _presence_runtime(request: Request) -> Any | None:
    # Same optional-injection shape as `_broker_runtime`: the process-wide
    # presence engine (app.presence.engine.get_engine()) is the default so
    # production wiring needs nothing extra, but a test may install its own
    # via app.state.presence_engine (app.worldmodel.state module docstring:
    # this module must stay read-only and never import a live singleton at
    # module scope itself).
    injected = getattr(request.app.state, "presence_engine", None)
    if injected is not None:
        return injected
    from app.presence.engine import get_engine

    return get_engine()


async def _build_snapshot(request: Request) -> WorldSnapshot:
    artifacts = _artifacts(request)
    broker_runtime = _broker_runtime(request)
    presence_runtime = _presence_runtime(request)

    def build() -> WorldSnapshot:
        with artifacts.session() as session:
            return assemble_snapshot(
                session,
                settings=artifacts.settings,
                broker_runtime=broker_runtime,
                presence_runtime=presence_runtime,
            )

    return await asyncio.to_thread(build)


@router.get("/policy")
async def get_world_policy() -> dict[str, Any]:
    return {"world_version": WORLD_VERSION, "truth_kinds": [k.value for k in TruthKind]}


@router.get("")
async def get_world(request: Request) -> dict[str, Any]:
    snapshot = await _build_snapshot(request)
    return snapshot.as_dict()


@router.get("/facts")
async def get_world_facts(request: Request, kind: str | None = None) -> dict[str, Any]:
    truth_kind: TruthKind | None = None
    if kind is not None:
        try:
            truth_kind = TruthKind(kind)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"unknown truth kind: {kind!r}") from exc

    snapshot = await _build_snapshot(request)
    facts = snapshot.facts_by_kind(truth_kind) if truth_kind is not None else snapshot.facts
    return {"facts": [f.as_dict() for f in facts]}


@router.get("/uncertainties")
async def get_world_uncertainties(request: Request) -> dict[str, Any]:
    snapshot = await _build_snapshot(request)
    return {"uncertainties": [u.as_dict() for u in snapshot.uncertainties]}
