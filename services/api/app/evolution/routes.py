"""Evolution REST surface (M7): /v1/evolution.

- GET  /capabilities            list the machine-readable registry
- GET  /capabilities/{id}       one capability (+ whether it is dispatchable)
- POST /gaps                    run gap detection for a request and record it
- GET  /gaps                    list recorded gaps with their decision trails
- POST /gaps/{id}/resolve       run the self-extension pipeline for one gap
- GET  /gaps/{id}/audit         the nine auditability answers for one evolution
- GET  /skill-versions          candidate/registered/rejected skill versions

Evolution backlog (the Evolution Engine's own work queue):

- GET  /opportunities           the scored backlog, best composite first
- POST /opportunities           create one FROM EVIDENCE (refuses without any)
- POST /opportunities/{id}/advance   one lifecycle transition
- POST /opportunities/{id}/approve   the human authority gate
- GET  /shadow-ready            what is built, tested and waiting on the owner
- GET  /policy                  lifecycle, weights, grants, root policies

The approve route is the only way into ``OWNER_APPROVED``, and it works by
minting an ``OwnerCapability`` from the *verified* session object that
``require_owner_session`` returns — not from a field in the request body. The
production-side transitions (``qualifying``/``live``/``rolled_back``) mint a
``ProductionAuthority`` from that same capability. Engine-internal callers hold
a lab authority, never a session, so neither path is reachable from them.

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
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.evolution.audit import build_audit
from app.evolution.authority import (
    Grant,
    ProductionAuthority,
    mint_owner_capability,
)
from app.evolution.backlog import ActorKind, OpportunityStatus
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.gaps import CapabilityRequest
from app.evolution.pipeline import EvolutionPipeline
from app.evolution.runtime import EvolutionRuntime
from app.evolution.scoring import SCORE_FIELDS
from app.evolution.service import PRODUCTION_SIDE_STATUSES, EvolutionService
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
    capabilities = await _call(runtime.registry.list_capabilities, status=status, limit=limit)
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
async def detect_gap(request: Request, body: GapDetectBody, response: Response) -> dict[str, Any]:
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


# ------------------------------------------------------------- opportunities

MAX_TITLE = 200
MAX_STATEMENT = 8000


def _service(request: Request) -> EvolutionService:
    return _runtime(request).evolution_service


class EvidenceRefBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(max_length=32)
    ref: str = Field(max_length=256)
    note: str | None = Field(default=None, max_length=256)


class ScoresBody(BaseModel):
    """The six declared scoring inputs. All required, all in [0, 1]."""

    model_config = ConfigDict(extra="forbid")

    owner_relevance: float = Field(ge=0.0, le=1.0)
    expected_utility: float = Field(ge=0.0, le=1.0)
    recurrence: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    engineering_cost: float = Field(ge=0.0, le=1.0)
    operational_risk: float = Field(ge=0.0, le=1.0)


class CreateOpportunityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    statement: str = Field(min_length=1, max_length=MAX_STATEMENT)
    #: Never optional and never empty: an opportunity exists because something
    #: happened, and the something is named here.
    evidence_refs: list[EvidenceRefBody] = Field(min_length=1, max_length=32)
    scores: ScoresBody
    source: str = Field(max_length=32)
    source_ref: str = Field(max_length=256)
    detail: dict[str, Any] | None = None


class AdvanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(max_length=24)
    actor: str = Field(max_length=16)
    reason: str | None = Field(default=None, max_length=512)
    workspace_ref: str | None = Field(default=None, max_length=512)
    candidate_ref: str | None = Field(default=None, max_length=512)


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=512)


#: One repository path: no commas, no whitespace, no quotes. A comma-joined list arriving
#: as ONE "path" matched no risk rule and derived tier 2 for a tier-3 change on production
#: (2026-09-07, the first real footprint over REST): a malformed footprint is refused, never
#: assessed.
_PATH_PATTERN = r"^[A-Za-z0-9_./@+~-]{1,512}$"


class FootprintBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changed_paths: list[str] = Field(min_length=1, max_length=512)

    @field_validator("changed_paths")
    @classmethod
    def _paths_are_single_paths(cls, value: list[str]) -> list[str]:
        import re

        for path in value:
            if not re.match(_PATH_PATTERN, path):
                raise ValueError(
                    f"not a single repository path: {path[:80]!r} (one path per item; "
                    "no commas, no whitespace)"
                )
        return value


class AuthorizeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_high_risk: bool = False
    note: str | None = Field(default=None, max_length=512)


@router.get("/opportunities")
async def list_opportunities(
    request: Request,
    status: str | None = Query(default=None, max_length=24),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    service = _service(request)
    opportunities = await _call(service.list_opportunities, status=status, limit=limit)
    return {"opportunities": opportunities}


@router.post("/opportunities")
async def create_opportunity(
    request: Request, body: CreateOpportunityBody, response: Response
) -> dict[str, Any]:
    service = _service(request)
    opportunity = await _call(
        service.create_from_evidence,
        title=body.title,
        statement=body.statement,
        evidence_refs=[ref.model_dump(mode="json") for ref in body.evidence_refs],
        scores=body.scores.model_dump(mode="json"),
        source=body.source,
        source_ref=body.source_ref,
        detail=body.detail,
    )
    logger.info(
        "evolution_opportunity_created",
        opportunity_id=opportunity["opportunity_id"],
        source=opportunity["source"],
    )
    response.status_code = 201
    return opportunity


@router.get("/opportunities/{opportunity_id}")
async def get_opportunity(request: Request, opportunity_id: uuid.UUID) -> dict[str, Any]:
    return await _call(_service(request).get, opportunity_id)


@router.post("/opportunities/{opportunity_id}/advance")
async def advance_opportunity(
    request: Request,
    opportunity_id: uuid.UUID,
    body: AdvanceBody,
    session=Depends(require_owner_session),  # noqa: B008 - FastAPI dependency
) -> dict[str, Any]:
    """One lifecycle transition.

    ``owner_approved`` is refused here on purpose — it has its own endpoint.
    Production-side targets mint a production authority from THIS request's
    verified owner session; there is no body field that can substitute for it.
    """
    service = _service(request)
    production_authority = None
    try:
        target = OpportunityStatus(body.target)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error_class": str(EvolutionErrorClass.VALIDATION_ERROR),
                "message": "unknown target status",
                "expected": [str(s) for s in OpportunityStatus],
            },
        ) from exc
    if target in PRODUCTION_SIDE_STATUSES:
        capability = mint_owner_capability(session)
        production_authority = ProductionAuthority.for_owner(
            capability, {Grant.DEPLOY, Grant.WRITE_PRODUCTION_DB}
        )
    return await _call(
        service.advance,
        opportunity_id,
        target=body.target,
        actor=body.actor,
        reason=body.reason,
        workspace_ref=body.workspace_ref,
        candidate_ref=body.candidate_ref,
        production_authority=production_authority,
    )


@router.post("/opportunities/{opportunity_id}/approve")
async def approve_opportunity(
    request: Request,
    opportunity_id: uuid.UUID,
    body: ApproveBody,
    session=Depends(require_owner_session),  # noqa: B008 - FastAPI dependency
) -> dict[str, Any]:
    """The human authority gate.

    The capability is derived from the session object the authentication
    dependency produced, so "the owner approved this" means a bearer token was
    verified in this request — not that a caller sent ``actor: owner``.
    """
    service = _service(request)
    capability = mint_owner_capability(session)
    approved = await _call(service.approve, opportunity_id, capability, note=body.note)
    logger.info("evolution_opportunity_approved", opportunity_id=str(opportunity_id))
    return approved


@router.post("/opportunities/{opportunity_id}/footprint")
async def record_footprint(
    request: Request,
    opportunity_id: uuid.UUID,
    body: FootprintBody,
) -> dict[str, Any]:
    """Attach the candidate's DERIVED risk tier from the paths it touches (M18 §5).

    Lab-scoped: the tier is computed from the path list and never accepted by hand.
    Refused once the candidate has crossed the owner's line (the footprint the owner
    reviews must be the one the tier came from).
    """
    service = _service(request)
    updated = await _call(
        service.record_release_footprint, opportunity_id, changed_paths=body.changed_paths
    )
    logger.info(
        "evolution_release_footprint_recorded",
        opportunity_id=str(opportunity_id),
        risk_tier=(updated.get("detail") or {}).get("risk_tier"),
    )
    return updated


@router.post("/opportunities/{opportunity_id}/authorize")
async def authorize_opportunity(
    request: Request,
    opportunity_id: uuid.UUID,
    body: AuthorizeBody,
    session=Depends(require_owner_session),  # noqa: B008 - FastAPI dependency
) -> dict[str, Any]:
    """The owner action that completes the explicit chain into production:
    ``owner_approval_required -> owner_authorized`` (ADR-0055 §5).

    Like approve, the capability is derived from the verified owner session, never
    from the body. A tier 3+ candidate refuses the first call and names the tier and
    its reasons; only a second, deliberate call with ``confirm_high_risk`` proceeds.
    Until M18.4's final gap closure this step existed in the service alone, so no client
    could carry a tier-3 candidate past the owner's line - found by the first real
    lifecycle on production (ADR-0081 addendum 3).
    """
    service = _service(request)
    capability = mint_owner_capability(session)
    authorized = await _call(
        service.authorize,
        opportunity_id,
        capability,
        confirm_high_risk=body.confirm_high_risk,
        note=body.note,
    )
    logger.info(
        "evolution_opportunity_authorized",
        opportunity_id=str(opportunity_id),
        second_confirmation=body.confirm_high_risk,
    )
    return authorized


@router.get("/shadow-ready")
async def shadow_ready(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> dict[str, Any]:
    """Built, tested, benchmarked, reviewed — and deployed nowhere."""
    return await _call(_service(request).pending_owner_actions, limit=limit)


@router.get("/policy")
async def evolution_policy(request: Request) -> dict[str, Any]:
    """The engine's declared contract, including what it may never do."""
    policy = await _call(_service(request).policy)
    policy["scoring"]["inputs"] = list(SCORE_FIELDS)
    policy["actors"] = [str(a) for a in ActorKind]
    return policy


__all__ = ["router"]


# ------------------------------------------------------------ M18.4: the supervisor


class PauseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=200)


@router.get("/supervisor")
async def supervisor_status(request: Request) -> dict[str, Any]:
    """The Evolution Supervisor's owner-facing picture (spec §3.5), from rows alone."""
    runtime = _runtime(request)
    from app.release.version import release_model

    def load() -> dict[str, Any]:
        with runtime.session() as session:
            return runtime.supervisor.status(
                session,
                evolution_service=runtime.evolution_service,
                release=release_model(runtime.settings),
            )

    return await asyncio.to_thread(load)


@router.post("/supervisor/scan")
async def supervisor_scan(request: Request) -> dict[str, Any]:
    """One scan now (owner-gated; the clock's own scans are unaffected)."""
    runtime = _runtime(request)

    def run() -> dict[str, Any]:
        with runtime.session() as session:
            return runtime.supervisor.scan(
                session, evolution_service=runtime.evolution_service, force=True
            ).as_dict()

    return await asyncio.to_thread(run)


@router.post("/supervisor/pause")
async def supervisor_pause(request: Request, body: PauseBody) -> dict[str, Any]:
    runtime = _runtime(request)

    def write() -> dict[str, Any]:
        with runtime.session() as session:
            from app.evolution.supervisor import set_paused

            set_paused(session, paused=True, actor="rest", reason=body.reason)
            return runtime.supervisor.status(session, evolution_service=runtime.evolution_service)

    return await asyncio.to_thread(write)


@router.post("/supervisor/resume")
async def supervisor_resume(request: Request, body: PauseBody) -> dict[str, Any]:
    runtime = _runtime(request)

    def write() -> dict[str, Any]:
        with runtime.session() as session:
            from app.evolution.supervisor import set_paused

            set_paused(session, paused=False, actor="rest", reason=body.reason)
            return runtime.supervisor.status(session, evolution_service=runtime.evolution_service)

    return await asyncio.to_thread(write)
