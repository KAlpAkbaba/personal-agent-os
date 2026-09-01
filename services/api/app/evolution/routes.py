"""Evolution REST surface (M7): /v1/evolution.

- GET  /capabilities            list the machine-readable registry
- GET  /capabilities/{id}       one capability (+ whether it is dispatchable)
- POST /gaps                    run gap detection for a request and record it
- GET  /gaps                    list recorded gaps with their decision trails
- POST /gaps/{id}/resolve       run the self-extension pipeline for one gap
- GET  /gaps/{id}/audit         the nine auditability answers for one evolution
- GET  /skill-versions          candidate/registered/rejected skill versions

Auth posture: the SAME as the rest of the API — no per-route authentication
today, inherited from the standing single-owner loopback posture and the M6
security addendum's hard gate ("these endpoints must be owner-authenticated
before running outside a trusted loopback/private network"). That is documented,
not invented here: this router adds no new exposure model, and the generation
path is additionally bounded by the composition-first gate, the sandbox policy
and the strict token shapes on everything that reaches generated source.

All inputs are bounded; DB and subprocess work runs in ``asyncio.to_thread``
like every other module; logs carry ids and classes only.
"""

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.evolution.audit import build_audit
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.gaps import CapabilityRequest
from app.evolution.pipeline import EvolutionPipeline
from app.evolution.runtime import EvolutionRuntime
from app.identity.dependencies import require_owner_session
from app.logging import get_logger, trace_id_var

logger = get_logger("app.evolution.routes")

# M9/ADR-0027: owner authentication is applied at the router, so a new
# endpoint in this module is protected by default rather than by memory.
# these endpoints create and promote code,
# and this module has no surface that must stay reachable unauthenticated.
router = APIRouter(
    prefix="/v1/evolution",
    dependencies=[Depends(require_owner_session)],
)

_HTTP_STATUS = {
    EvolutionErrorClass.VALIDATION_ERROR: 422,
    EvolutionErrorClass.NOT_FOUND: 404,
    EvolutionErrorClass.CAPABILITY_MISSING: 404,
    EvolutionErrorClass.GENERATION_REFUSED: 409,
    EvolutionErrorClass.PRODUCT_CHANGE_REQUIRED: 409,
    EvolutionErrorClass.SANDBOX_VIOLATION: 403,
    EvolutionErrorClass.GENERATOR_NOT_CONFIGURED: 503,
    EvolutionErrorClass.GENERATION_FAILED: 422,
    EvolutionErrorClass.EVALUATION_FAILED: 409,
    EvolutionErrorClass.REVIEW_REJECTED: 409,
    EvolutionErrorClass.REGISTRATION_REFUSED: 409,
    EvolutionErrorClass.DISPATCH_FAILED: 500,
    EvolutionErrorClass.SUPPLY_CHAIN_REJECTED: 409,
    EvolutionErrorClass.DEPENDENCY_UNAVAILABLE: 424,
    EvolutionErrorClass.RESOURCE_BUDGET_EXCEEDED: 409,
    EvolutionErrorClass.RECURSION_LIMIT_EXCEEDED: 409,
    EvolutionErrorClass.PERMISSION_DENIED: 403,
    EvolutionErrorClass.LIFECYCLE_VIOLATION: 409,
    EvolutionErrorClass.NOT_SUPERIOR: 409,
    EvolutionErrorClass.INTERNAL_BUG: 500,
}

MAX_REQUEST_TEXT = 4000


def _runtime(request: Request) -> EvolutionRuntime:
    return request.app.state.evolution


def _http_error(exc: EvolutionError) -> HTTPException:
    return HTTPException(
        status_code=_HTTP_STATUS.get(exc.error_class, 500),
        detail={"error_class": str(exc.error_class), "message": exc.message},
    )


async def _call(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except EvolutionError as exc:
        raise _http_error(exc) from exc


# ------------------------------------------------------------- capabilities


@router.get("/capabilities")
async def list_capabilities(
    request: Request,
    status: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    runtime = _runtime(request)
    capabilities = await _call(
        runtime.registry.list_capabilities, status=status, limit=limit
    )
    return {"capabilities": capabilities}


@router.get("/capabilities/{capability_id}")
async def get_capability(request: Request, capability_id: str) -> dict[str, Any]:
    runtime = _runtime(request)
    if len(capability_id) > 128:
        raise HTTPException(status_code=422, detail={"message": "capability_id too long"})
    capability = await _call(runtime.registry.get_capability, capability_id)
    if capability is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_class": str(EvolutionErrorClass.NOT_FOUND),
                "message": f"capability {capability_id!r} not found",
            },
        )
    resolved = await _call(runtime.registry.resolve, capability_id)
    capability["dispatchable"] = resolved is not None
    return capability


# --------------------------------------------------------------------- gaps


class GapDetectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_capability: str = Field(max_length=128)
    request_text: str = Field(max_length=MAX_REQUEST_TEXT)
    required_inputs: list[str] = Field(default_factory=list, max_length=16)
    required_outputs: list[str] = Field(default_factory=list, max_length=16)
    task_id: uuid.UUID | None = None
    target_component: str | None = Field(default=None, max_length=128)
    change_kind: str | None = Field(default=None, max_length=32)
    # Boundary: an operational capability request may carry the owner policy
    # subsystem's authorization for an asset; self_modification is the only
    # intent the recovery/security-root rule constrains.
    intent: str | None = Field(default=None, max_length=32)
    authorized_asset: str | None = Field(default=None, max_length=64)
    depth: int = Field(default=0, ge=0, le=8)
    origin_gap_id: str | None = Field(default=None, max_length=64)
    # The structured skill spec; every field is re-validated at the choke point
    # in SkillSpec.parse before a single character reaches generated source.
    spec: dict[str, Any] | None = None
    resume_payload: dict[str, Any] | None = None


def _detect(runtime: EvolutionRuntime, body: GapDetectBody, trace_id: str | None) -> dict:
    request = CapabilityRequest.parse(body.model_dump(mode="json"))
    decision = runtime.detector.detect(request)
    gap = runtime.gaps.record(request, decision, trace_id=trace_id)
    gap["decision"] = decision.to_dict()
    return gap


@router.post("/gaps")
async def detect_gap(
    request: Request, body: GapDetectBody, response: Response
) -> dict[str, Any]:
    runtime = _runtime(request)
    trace_id = trace_id_var.get(None)
    gap = await _call(_detect, runtime, body, trace_id)
    logger.info(
        "gap_detected",
        gap_id=gap["id"],
        requested_capability=gap["requested_capability"],
        resolution=gap["resolution"],
    )
    response.status_code = 201
    return gap


@router.get("/gaps")
async def list_gaps(
    request: Request,
    status: str | None = Query(default=None, max_length=16),
    resolution: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    runtime = _runtime(request)
    gaps = await _call(runtime.gaps.list, status=status, resolution=resolution, limit=limit)
    return {"gaps": gaps}


def _resolve(runtime: EvolutionRuntime, gap_id: uuid.UUID) -> dict[str, Any]:
    # Unknown gap surfaces as a typed 404 before any sandbox/work happens.
    runtime.gaps.get(gap_id)
    pipeline = EvolutionPipeline(
        runtime.registry,
        runtime.gaps,
        generator=runtime.generator,
        sandbox=runtime.sandbox,
        skills_root=runtime.skills_root,
        resumer=runtime.resumer,
        reviewer=runtime.reviewer,
    )
    return pipeline.run(gap_id).to_dict()


@router.post("/gaps/{gap_id}/resolve")
async def resolve_gap(request: Request, gap_id: uuid.UUID) -> dict[str, Any]:
    runtime = _runtime(request)
    logger.info("gap_resolve_requested", gap_id=str(gap_id))
    return await _call(_resolve, runtime, gap_id)


@router.get("/gaps/{gap_id}/audit")
async def gap_audit(request: Request, gap_id: uuid.UUID) -> dict[str, Any]:
    """The nine auditability answers for one evolution (M7 Auditability)."""
    runtime = _runtime(request)
    return await _call(build_audit, gap_id, runtime.gaps, runtime.registry)


# ----------------------------------------------------------- skill versions


@router.get("/skill-versions")
async def list_skill_versions(
    request: Request,
    capability_id: str | None = Query(default=None, max_length=128),
    status: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    runtime = _runtime(request)
    versions = await _call(
        runtime.registry.list_skill_versions,
        capability_id=capability_id,
        status=status,
        limit=limit,
    )
    return {"skill_versions": versions}


__all__ = ["router"]
