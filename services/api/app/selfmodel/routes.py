"""Self Model REST surface (PHASE 6): ``/v1/selfmodel``.

- ``POST /index``                     rebuild the index, return a counter report
- ``GET  /modules``                   list, filterable by kind
- ``GET  /modules/{key}``             module status (the four truths included)
- ``GET  /modules/{key}/problems``    open incidents + failed tests + limits
- ``GET  /search?q=``                 module and symbol search
- ``GET  /policy``                    version and vocabularies

Owner-gated at the router like every other surface, so a new endpoint added
here is protected by default rather than by memory (ADR-0027).

The index root is NEVER taken from the request. ``POST /index`` walks the
checkout this process was deployed from, resolved by
``app.selfmodel.indexer.default_repo_root``; an owner-session caller must not
be able to point a repository walker at an arbitrary directory, the same
reasoning that restricts workspace paths in ``app/selfhealing/routes.py``.

``app/main.py`` is deliberately untouched: the integrator mounts this router.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.identity.dependencies import require_owner_session
from app.logging import get_logger
from app.selfmodel import SELFMODEL_VERSION
from app.selfmodel import query as selfmodel_query
from app.selfmodel.indexer import build_index, default_repo_root
from app.selfmodel.models import (
    EDGE_KINDS,
    MODULE_KINDS,
    PRODUCTION_STATES,
    SYMBOL_KINDS,
    TRUTH_KINDS,
)
from app.selfmodel.progress import PHASES, SUBSYSTEM_SELF_MODEL, IndexProgress
from app.selfmodel.query import REQUIRED_GATES

logger = get_logger("app.selfmodel.routes")

router = APIRouter(prefix="/v1/selfmodel", dependencies=[Depends(require_owner_session)])

MAX_LIMIT = 500
#: A module key is a lookup value, not a path the server opens; it is still
#: bounded so a pathological query cannot become a pathological LIKE.
MAX_KEY_CHARS = 300
MAX_QUERY_CHARS = 200

#: One index run at a time per process. A second concurrent rebuild would do the
#: same work twice and race on the same rows for no benefit.
_index_lock = asyncio.Lock()


def _session_source(request: Request) -> Any:
    """``app.state.artifacts`` is the generic DB session source in this service
    (``app/ledger/routes.py``, ``app/research/routes.py`` do the same)."""
    source = getattr(request.app.state, "artifacts", None)
    if source is None:  # pragma: no cover - set at startup
        raise HTTPException(status_code=503, detail={"error_class": "dependency_unavailable"})
    return source


def _check_key(key: str) -> str:
    if not key or len(key) > MAX_KEY_CHARS:
        raise HTTPException(
            status_code=422,
            detail={"error_class": "validation_error", "message": "module key out of range"},
        )
    return key


@router.get("/policy")
async def get_selfmodel_policy() -> dict[str, Any]:
    """Side-effect free. Lets a caller (the explain engine, an owner script)
    check which self-model contract this Cloud Core speaks before relying on
    a field name."""
    return {
        "selfmodel_version": SELFMODEL_VERSION,
        "module_kinds": sorted(MODULE_KINDS),
        "symbol_kinds": sorted(SYMBOL_KINDS),
        "edge_kinds": sorted(EDGE_KINDS),
        "truth_kinds": sorted(TRUTH_KINDS),
        "production_states": sorted(PRODUCTION_STATES),
        "required_gates": list(REQUIRED_GATES),
        "ui_subsystem": SUBSYSTEM_SELF_MODEL,
        "index_phases": list(PHASES),
    }


@router.post("/index")
async def rebuild_index(request: Request) -> dict[str, Any]:
    """Rebuild the index from this deployment's own checkout.

    Incremental: an unchanged checkout reports ``writes: 0``. Publishes
    THINKING for the ``self_model`` subsystem while it runs, with counters only.
    """
    source = _session_source(request)
    if _index_lock.locked():
        raise HTTPException(
            status_code=409,
            detail={"error_class": "conflict", "message": "an index run is already in progress"},
        )

    async with _index_lock:
        progress = IndexProgress()

        def run() -> dict[str, Any]:
            with source.session() as session:
                report = build_index(session, repo_root=default_repo_root(), progress=progress)
                session.commit()
                return report.to_dict()

        report = await asyncio.to_thread(run)

    return {"report": report, "ui_frames": len(progress.published)}


@router.get("/modules")
async def list_modules(
    request: Request,
    kind: str | None = None,
    limit: int = Query(default=100, ge=1, le=MAX_LIMIT),
) -> dict[str, Any]:
    if kind is not None and kind not in MODULE_KINDS:
        # The rejected value is not echoed back; the caller is told what the
        # closed vocabulary is, which is the only useful half anyway.
        raise HTTPException(
            status_code=422,
            detail={
                "error_class": "validation_error",
                "message": "unknown module kind",
                "module_kinds": sorted(MODULE_KINDS),
            },
        )
    source = _session_source(request)

    def load() -> list[dict[str, Any]]:
        with source.session() as session:
            return selfmodel_query.list_modules(session, kind=kind, limit=limit)

    return {"modules": await asyncio.to_thread(load)}


@router.get("/search")
async def search_index(
    request: Request,
    q: str = Query(min_length=1, max_length=MAX_QUERY_CHARS),
    limit: int = Query(default=25, ge=1, le=MAX_LIMIT),
) -> dict[str, Any]:
    source = _session_source(request)

    def load() -> dict[str, Any]:
        with source.session() as session:
            return selfmodel_query.search(session, q, limit=limit)

    return await asyncio.to_thread(load)


# Declared before the bare ``{module_key:path}`` route: a greedy path converter
# would otherwise swallow the "/problems" suffix into the key.
@router.get("/modules/{module_key:path}/problems")
async def module_problems(request: Request, module_key: str) -> dict[str, Any]:
    source = _session_source(request)
    key = _check_key(module_key)

    def load() -> dict[str, Any]:
        with source.session() as session:
            return selfmodel_query.module_problems(session, key).to_dict()

    answer = await asyncio.to_thread(load)
    if not answer["found"]:
        raise HTTPException(status_code=404, detail=answer)
    return answer


@router.get("/modules/{module_key:path}")
async def module_status(request: Request, module_key: str) -> dict[str, Any]:
    source = _session_source(request)
    key = _check_key(module_key)

    def load() -> dict[str, Any]:
        with source.session() as session:
            return selfmodel_query.module_status(session, key).to_dict()

    answer = await asyncio.to_thread(load)
    if not answer["found"]:
        # 404 carrying the candidates: the caller learns what the index does
        # have instead of being handed a nearest-neighbour guess.
        raise HTTPException(status_code=404, detail=answer)
    return answer


__all__ = ["SELFMODEL_VERSION", "router"]
