"""Self-healing REST surface (M6): /v1/selfhealing.

- POST /incidents/ingest   accepts a Recovery Supervisor incident report
                           (outbox drain or direct POST); fingerprint dedup.
- GET  /incidents          list incidents.
- GET  /releases           list release records.
- POST /pipeline/run       run the engineering pipeline for an open/recovered
                           incident (reproduce -> regression -> patch ->
                           review -> staging -> promote).

All workspace paths accepted from the caller are restricted to the configured
selfhealing workspace root (single-owner surface, but a forged path must never
make the API deploy from or to an arbitrary directory). Logs carry ids and
classes only. DB/subprocess work runs in asyncio.to_thread like the other
modules.
"""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.identity.dependencies import require_owner_session
from app.logging import get_logger, trace_id_var
from app.selfhealing.errors import SelfHealingError, SelfHealingErrorClass
from app.selfhealing.monitoring import draft_from_supervisor_report
from app.selfhealing.pipeline import SelfHealingPipeline, SupervisorDeployer
from app.selfhealing.runtime import SelfHealingRuntime

logger = get_logger("app.selfhealing.routes")

# M9/ADR-0027: owner authentication is applied at the router, so a new
# endpoint in this module is protected by default rather than by memory.
# incident ingest feeds the autonomous fix pipeline and pipeline/run executes it,
# and this module has no surface that must stay reachable unauthenticated.
router = APIRouter(
    prefix="/v1/selfhealing",
    dependencies=[Depends(require_owner_session)],
)

_HTTP_STATUS = {
    SelfHealingErrorClass.VALIDATION_ERROR: 422,
    SelfHealingErrorClass.NOT_FOUND: 404,
    SelfHealingErrorClass.BACKEND_NOT_CONFIGURED: 503,
    SelfHealingErrorClass.PATCH_DERIVATION_FAILED: 422,
    SelfHealingErrorClass.REVIEW_REJECTED: 409,
    SelfHealingErrorClass.REPRODUCTION_FAILED: 409,
    SelfHealingErrorClass.PIPELINE_STAGE_FAILED: 409,
    SelfHealingErrorClass.INTERNAL_BUG: 500,
}


def _runtime(request: Request) -> SelfHealingRuntime:
    return request.app.state.selfhealing


def _http_error(exc: SelfHealingError) -> HTTPException:
    return HTTPException(
        status_code=_HTTP_STATUS.get(exc.error_class, 500),
        detail={"error_class": str(exc.error_class), "message": exc.message},
    )


async def _call(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except SelfHealingError as exc:
        raise _http_error(exc) from exc


# An incident report is a small structured envelope; anything larger is
# either a bug or an attempt to bloat the incidents table (M6 review).
MAX_INCIDENT_REPORT_BYTES = 64 * 1024


class IngestBody(BaseModel):
    # The supervisor report is a versioned envelope validated in
    # monitoring.draft_from_supervisor_report; unknown extra keys are refused
    # at the schema check there, while pydantic only bounds the shape here.
    model_config = ConfigDict(extra="allow")

    schema_: str = Field(alias="schema", max_length=128)
    fingerprint_material: dict[str, Any]

    @model_validator(mode="after")
    def _bounded_report(self) -> "IngestBody":
        size = len(
            json.dumps(self.model_dump(by_alias=True), separators=(",", ":"), default=str).encode()
        )
        if size > MAX_INCIDENT_REPORT_BYTES:
            raise ValueError(
                f"incident report too large: {size} bytes (max {MAX_INCIDENT_REPORT_BYTES})"
            )
        return self


@router.post("/incidents/ingest")
async def ingest_incident(request: Request, body: IngestBody, response: Response) -> dict[str, Any]:
    runtime = _runtime(request)
    report = body.model_dump(by_alias=True)
    draft = await _call(draft_from_supervisor_report, report)
    trace_id = trace_id_var.get(None)
    result = await _call(runtime.service.ingest_incident, draft, trace_id=trace_id)
    response.status_code = 201 if result.created else 200
    return {
        "incident_id": str(result.incident_id),
        "fingerprint": result.fingerprint,
        "occurrence_count": result.occurrence_count,
        "created": result.created,
        "status": result.status,
    }


@router.get("/incidents")
async def list_incidents(
    request: Request,
    component: str | None = Query(default=None, max_length=128),
    status: str | None = Query(default=None, max_length=24),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    runtime = _runtime(request)
    incidents = await _call(
        runtime.service.list_incidents, component=component, status=status, limit=limit
    )
    return {"incidents": incidents}


@router.get("/releases")
async def list_releases(
    request: Request,
    component: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    runtime = _runtime(request)
    releases = await _call(runtime.service.list_releases, component=component, limit=limit)
    return {"releases": releases}


class PipelineRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: uuid.UUID
    component: str = Field(default="browser-agent-demo", max_length=128)
    staging_workspace: str = Field(max_length=1024)
    production_workspace: str = Field(max_length=1024)
    staging_port: int = Field(ge=1024, le=65535)
    production_port: int = Field(ge=1024, le=65535)
    max_cycles: int = Field(default=3, ge=1, le=20)
    failure_threshold: int = Field(default=2, ge=2, le=10)


def _require_under_root(path_value: str, root: Path, label: str) -> Path:
    resolved = Path(path_value).resolve()
    if not resolved.is_relative_to(root):
        raise SelfHealingError(
            SelfHealingErrorClass.VALIDATION_ERROR,
            f"{label} must live under the selfhealing workspace root",
            details={"root": str(root)},
        )
    return resolved


def build_pipeline(
    runtime: SelfHealingRuntime,
    *,
    component: str,
    staging_workspace: str,
    production_workspace: str,
    staging_port: int,
    production_port: int,
    max_cycles: int = 3,
    failure_threshold: int = 2,
    on_step: Any = None,
) -> SelfHealingPipeline:
    """The ONE way a request becomes a pipeline: workspaces restricted to the configured
    root, the runtime's own supervisor/target scripts and coding backend. The M18.4 closed
    loop (``app.evolution.closed_loop``) builds through here too, with its step observer."""
    staging_ws = _require_under_root(staging_workspace, runtime.workspace_root, "staging_workspace")
    production_ws = _require_under_root(
        production_workspace, runtime.workspace_root, "production_workspace"
    )
    deployer = SupervisorDeployer(
        supervisor_script=runtime.supervisor_script,
        target_service_script=runtime.target_service_script,
        staging_workspace=staging_ws,
        staging_port=staging_port,
        production_workspace=production_ws,
        production_port=production_port,
        component=component,
        max_cycles=max_cycles,
        failure_threshold=failure_threshold,
    )
    return SelfHealingPipeline(
        runtime.service,
        runtime.backend,
        deployer,
        allowed_roots=[runtime.workspace_root],
        on_step=on_step,
    )


def _run_pipeline(runtime: SelfHealingRuntime, body: PipelineRunBody) -> dict[str, Any]:
    # Unknown incident surfaces as a 404 (typed NOT_FOUND) before any work.
    runtime.service.get_incident(body.incident_id)
    pipeline = build_pipeline(
        runtime,
        component=body.component,
        staging_workspace=body.staging_workspace,
        production_workspace=body.production_workspace,
        staging_port=body.staging_port,
        production_port=body.production_port,
        max_cycles=body.max_cycles,
        failure_threshold=body.failure_threshold,
    )
    return pipeline.run(body.incident_id).to_dict()


@router.post("/pipeline/run")
async def run_pipeline(request: Request, body: PipelineRunBody) -> dict[str, Any]:
    runtime = _runtime(request)
    logger.info("pipeline_run_requested", incident_id=str(body.incident_id))
    return await _call(_run_pipeline, runtime, body)


# --------------------------------------------------- M18.4: the closed loop


class HealBody(BaseModel):
    """The same workspace/port contract as POST /pipeline/run: the loop runs the REAL
    pipeline on a staging and a production workspace under the self-healing root."""

    model_config = ConfigDict(extra="forbid")

    component: str = Field(default="browser-agent-demo", max_length=128)
    staging_workspace: str = Field(max_length=1024)
    production_workspace: str = Field(max_length=1024)
    staging_port: int = Field(ge=1024, le=65535)
    production_port: int = Field(ge=1024, le=65535)
    max_cycles: int = Field(default=3, ge=1, le=20)
    failure_threshold: int = Field(default=2, ge=2, le=10)


_EVOLUTION_HTTP_STATUS = {
    "validation_error": 422,
    "not_found": 404,
    "authority_error": 403,
    "illegal_transition": 409,
}


@router.post("/opportunities/{opportunity_id}/heal")
async def heal_opportunity(
    request: Request, opportunity_id: uuid.UUID, body: HealBody
) -> dict[str, Any]:
    """Drive ONE incident-born opportunity through the real self-healing pipeline, moving
    it through its lifecycle as each gate lands (docs/M18_4_SELF_EVOLUTION_SPEC.md §5,
    ADR-0081). Ends at owner_approval_required (LIVE is the owner's) or parked with the
    failing gate in the reason. Lives here, not under /v1/evolution: the lab may not import
    the deployer (the authority wall's import guard), so the side that deploys hosts the
    loop and reads the lab's service."""
    from app.evolution.errors import EvolutionError
    from app.selfhealing.closed_loop import ClosedLoop

    runtime = _runtime(request)
    evolution = request.app.state.evolution

    def run() -> dict[str, Any]:
        loop = ClosedLoop(
            evolution_service=evolution.evolution_service,
            pipeline_factory=lambda on_step: build_pipeline(
                runtime,
                component=body.component,
                staging_workspace=body.staging_workspace,
                production_workspace=body.production_workspace,
                staging_port=body.staging_port,
                production_port=body.production_port,
                max_cycles=body.max_cycles,
                failure_threshold=body.failure_threshold,
                on_step=on_step,
            ),
        )
        return loop.heal(opportunity_id).as_dict()

    try:
        return await asyncio.to_thread(run)
    except SelfHealingError as exc:
        raise _http_error(exc) from exc
    except EvolutionError as exc:
        raise HTTPException(
            status_code=_EVOLUTION_HTTP_STATUS.get(str(exc.error_class), 500),
            detail={"error_class": str(exc.error_class), "message": str(exc)},
        ) from exc


__all__ = ["build_pipeline", "router"]
