"""Capability Genesis REST surface (docs/M24_CAPABILITY_GENESIS_SPEC.md §8,
ADR-0087) for the Cockpit's "Yeni Yetenek" panel — the web track wires
exactly these names.

- GET  /v1/genesis/runs               recent runs (capability, state, when,
                                      the error when failed) — a read of
                                      ``genesis_runs``, no ledger row per poll.
- GET  /v1/genesis/runs/{id}          one run.
- POST /v1/genesis/runs/{id}/approve  "Onayla" — ONLY in ``awaiting_approval``;
                                      the owner-authenticated REST act IS the
                                      confirmation (``CONFIRM_SOURCE_REST``,
                                      the same rule ``app.actions.confirmation_gate``
                                      documents for the Cockpit's Approve button).
- POST /v1/genesis/runs/{id}/cancel   "Vazgeç" while active.

Owner-gated at the router level (``require_owner_session``) — the SAME
discipline ``app.appfactory.routes`` follows: a generated capability's
approval must never be one unauthenticated HTTP call away.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.actions.confirmation_gate import CONFIRM_SOURCE_REST, Confirmation
from app.evolution.errors import EvolutionError
from app.genesis.interface import _require_loopback_url as _require_interface_url
from app.genesis.service import GenesisService
from app.identity.dependencies import require_owner_session
from app.logging import get_logger

logger = get_logger("app.genesis.routes")

router = APIRouter(
    prefix="/v1/genesis", tags=["genesis"], dependencies=[Depends(require_owner_session)]
)


def _service(request: Request) -> GenesisService:
    return request.app.state.genesis.service


def _http_error(exc: EvolutionError) -> HTTPException:
    status_code = 404 if str(exc.error_class) == "not_found" else 422
    if str(exc.error_class) == "security_refused":
        status_code = 409
    return HTTPException(
        status_code=status_code,
        detail={"code": str(exc.error_class), "message": exc.message},
    )


@router.get("/runs")
async def list_runs(request: Request) -> dict[str, Any]:
    service = _service(request)
    runs = await asyncio.to_thread(service.list)
    return {"runs": runs}


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    service = _service(request)
    try:
        return await asyncio.to_thread(service.get, run_id)
    except EvolutionError as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/approve")
async def approve_run(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    service = _service(request)
    owner_session_id = str(request.state.owner_session.session_id)
    confirmation = Confirmation(source=CONFIRM_SOURCE_REST, session_id=owner_session_id)
    try:
        return await asyncio.to_thread(service.approve, run_id, confirmation)
    except EvolutionError as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    service = _service(request)
    try:
        return await asyncio.to_thread(service.cancel, run_id)
    except EvolutionError as exc:
        raise _http_error(exc) from exc


# ------------------------------------------------------------------ B36: the front door


class RequestBody(BaseModel):
    """Req 561: a capability request from the product surface. ``interface_url`` may be
    omitted for an interface the owner REGISTERED (the catalogue supplies it)."""

    model_config = ConfigDict(extra="forbid")

    interface_name: str = Field(min_length=1, max_length=32, pattern="^[a-z][a-z0-9_]{0,31}$")
    operation_id: str = Field(min_length=1, max_length=32, pattern="^[a-z][a-z0-9_]{0,31}$")
    interface_url: str | None = Field(default=None, max_length=512)
    arguments: dict[str, Any] = Field(default_factory=dict)
    #: Req 571: build the next version of a capability that already resolves.
    new_version: bool = False


class CatalogueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=32, pattern="^[a-z][a-z0-9_]{0,31}$")
    url: str = Field(min_length=1, max_length=512)
    target_phrases: list[str] = Field(min_length=1, max_length=24)
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=16)
    spec_digest: str | None = Field(default=None, max_length=64)


class DiscoverBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=512)


class RollbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=5, max_length=32, pattern=r"^\d+\.\d+\.\d+$")


class UseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)


def _store(request: Request) -> Any:
    return request.app.state.genesis.catalogue_store


def _catalogue_url(request: Request, name: str) -> str | None:
    for entry in _store(request).catalogue.entries():
        if entry.name == name:
            return entry.url
    return None


@router.post("/runs", status_code=201)
async def request_capability(request: Request, body: RequestBody) -> dict[str, Any]:
    """Req 561/574: the request route. The url comes from the catalogue when the body
    names none; an interface the owner never registered and no url is 422."""
    service = _service(request)
    owner_session_id = str(request.state.owner_session.session_id)
    url = body.interface_url or _catalogue_url(request, body.interface_name)
    if not url:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": f"interface {body.interface_name!r} is not in the catalogue and no "
                "interface_url was given",
            },
        )
    try:
        return await asyncio.to_thread(
            service.request,
            interface_name=body.interface_name,
            interface_url=url,
            operation_id=body.operation_id,
            arguments=body.arguments,
            session_id=owner_session_id,
            new_version=body.new_version,
        )
    except EvolutionError as exc:
        raise _http_error(exc) from exc


@router.get("/catalogue")
async def list_catalogue(request: Request) -> dict[str, Any]:
    """Req 562: what the owner registered, enabled or not."""
    store = _store(request)
    entries = await asyncio.to_thread(store.list)
    return {"entries": entries}


@router.post("/catalogue", status_code=201)
async def register_catalogue(request: Request, body: CatalogueBody) -> dict[str, Any]:
    """Req 563: the registration surface. The url must be researchable under the same
    host rule the run applies (loopback, or an owner-authorized host)."""
    store = _store(request)
    service = _service(request)
    try:
        _require_interface_url(body.url, host_allowed=service.host_allowed)
        entry = await asyncio.to_thread(
            store.register,
            name=body.name,
            url=body.url,
            target_phrases=body.target_phrases,
            operations=body.operations,
            source="owner_rest",
            spec_digest=body.spec_digest,
        )
    except EvolutionError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        # The reason goes to the log; the owner gets a sentence, never the exception.
        logger.info("genesis_catalogue_refused", name=body.name, reason=type(exc).__name__)
        refused = {
            "code": "validation_error",
            "message": "catalogue entry refused: a lowercase name, a url and at least one "
            "spoken phrase are required; operation ids must be lowercase tokens",
        }
        raise HTTPException(status_code=422, detail=refused) from exc
    return {"entry": entry}


@router.post("/catalogue/discover")
async def discover_catalogue(request: Request, body: DiscoverBody) -> dict[str, Any]:
    """Req 564: fetch a description and propose an entry; registers nothing."""
    store = _store(request)
    service = _service(request)
    try:
        _require_interface_url(body.url, host_allowed=service.host_allowed)
        proposal = await asyncio.to_thread(store.discover, body.url)
    except EvolutionError as exc:
        raise _http_error(exc) from exc
    return {"proposal": proposal}


@router.delete("/catalogue/{name}")
async def disable_catalogue(request: Request, name: str) -> dict[str, Any]:
    store = _store(request)
    try:
        entry = await asyncio.to_thread(store.disable, name)
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": f"no catalogue entry {name!r}"}
        ) from exc
    return {"entry": entry}


@router.get("/capabilities/{capability_id}/versions")
async def capability_versions(request: Request, capability_id: str) -> dict[str, Any]:
    """Req 571."""
    service = _service(request)
    try:
        return await asyncio.to_thread(service.versions, capability_id)
    except EvolutionError as exc:
        raise _http_error(exc) from exc


@router.post("/capabilities/{capability_id}/rollback")
async def capability_rollback(
    request: Request, capability_id: str, body: RollbackBody
) -> dict[str, Any]:
    """Req 572."""
    service = _service(request)
    try:
        return {
            "capability": await asyncio.to_thread(service.rollback, capability_id, body.version)
        }
    except EvolutionError as exc:
        raise _http_error(exc) from exc


@router.post("/capabilities/{capability_id}/deactivate")
async def capability_deactivate(request: Request, capability_id: str) -> dict[str, Any]:
    """Req 570."""
    service = _service(request)
    try:
        return {"capability": await asyncio.to_thread(service.deactivate, capability_id)}
    except EvolutionError as exc:
        raise _http_error(exc) from exc


@router.post("/capabilities/{capability_id}/activate")
async def capability_activate(request: Request, capability_id: str) -> dict[str, Any]:
    """Req 570."""
    service = _service(request)
    try:
        return {"capability": await asyncio.to_thread(service.activate, capability_id)}
    except EvolutionError as exc:
        raise _http_error(exc) from exc


@router.post("/capabilities/{capability_id}/use")
async def capability_use(request: Request, capability_id: str, body: UseBody) -> dict[str, Any]:
    """Req 573: the production use of a registered capability through the dispatcher."""
    service = _service(request)
    try:
        return await asyncio.to_thread(service.use, capability_id, body.arguments)
    except EvolutionError as exc:
        raise _http_error(exc) from exc


__all__ = ["router"]
