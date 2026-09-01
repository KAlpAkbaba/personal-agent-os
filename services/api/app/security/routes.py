"""Security REST surface (M8): /v1/security.

- POST   /assets                     enroll an owner-authorized asset
- GET    /assets                     list enrolled assets
- GET    /assets/{ref}               one asset
- PATCH  /assets/{ref}               scope change (never kind/locator)
- POST   /assets/{ref}/revoke        revoke authorization (terminal)
- POST   /assets/{ref}/suspend       suspend authorization (reversible)
- POST   /assessments                run a scope-checked assessment
- GET    /assessments                assessment history
- GET    /assessments/{id}           one assessment + its findings
- POST   /assessments/{id}/artifact  publish the security report artifact
- GET    /findings                   findings across assessments
- POST   /findings/{id}/remediate    propose / dry-run / apply / revert a fix
- GET    /audit                      the append-only authorization event log
- POST   /scope/check                read-only "would this be allowed?" preview

Auth posture: the SAME as the rest of the API — no per-route authentication
today, inherited from the standing single-owner loopback posture (M6 security
addendum: these endpoints must be owner-authenticated before running outside a
trusted loopback/private network). That is documented, not invented here; this
router adds no new exposure model. Note that no route here can widen scope by
accident: enrollment is an explicit owner action that demands authorization
evidence, and every execution path is gated by `scope.py` regardless of which
endpoint reached it.

All inputs are bounded; DB and filesystem work runs in ``asyncio.to_thread``
like every other module; logs carry ids, classes and reasons only.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.identity.dependencies import require_owner_session
from app.logging import get_logger, trace_id_var
from app.security.artifacts import publish_assessment_artifact
from app.security.errors import SecurityError, SecurityErrorClass
from app.security.models import (
    ASSET_KINDS,
    ENVIRONMENTS,
    TESTING_CLASS_CONFIGURATION_AUDIT,
)
from app.security.remediation import MODE_PROPOSE, MODES
from app.security.runtime import SecurityRuntime

logger = get_logger("app.security.routes")

# M9/ADR-0027: owner authentication is applied at the router, so a new
# endpoint in this module is protected by default rather than by memory.
# asset enrollment defines what the agent may touch,
# and this module has no surface that must stay reachable unauthenticated.
router = APIRouter(
    prefix="/v1/security",
    dependencies=[Depends(require_owner_session)],
)

_HTTP_STATUS = {
    SecurityErrorClass.VALIDATION_ERROR: 422,
    SecurityErrorClass.NOT_FOUND: 404,
    SecurityErrorClass.ALREADY_ENROLLED: 409,
    # 403: the owner's stored scope does not cover this. Deliberately NOT 404
    # — pretending the target does not exist would hide the refusal from the
    # owner, and the refusal is the product behaviour here.
    SecurityErrorClass.OUT_OF_SCOPE: 403,
    SecurityErrorClass.CONSTRAINT_VIOLATION: 409,
    SecurityErrorClass.REMEDIATION_NOT_AUTOMATABLE: 409,
    SecurityErrorClass.COLLECTOR_ROOT_VIOLATION: 403,
    SecurityErrorClass.TARGET_UNAVAILABLE: 424,
    SecurityErrorClass.INTERNAL_BUG: 500,
}

# Module-level singleton: ruff B008 forbids a call in an argument default.
_OPTIONAL_UUID_QUERY = Query(default=None)

MAX_REF = 128
MAX_TARGET = 512
MAX_PATH = 512
MAX_REASON = 512


def _runtime(request: Request) -> SecurityRuntime:
    return request.app.state.security


def _http_error(exc: SecurityError) -> HTTPException:
    return HTTPException(
        status_code=_HTTP_STATUS.get(exc.error_class, 500),
        detail={
            "error_class": str(exc.error_class),
            "message": exc.message,
            "details": exc.details,
        },
    )


async def _call(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except SecurityError as exc:
        raise _http_error(exc) from exc


# ------------------------------------------------------------------- assets


class EnrollBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_ref: str = Field(max_length=64)
    name: str = Field(max_length=256)
    kind: str = Field(max_length=32)
    locator: str = Field(max_length=MAX_PATH)
    environment: str = Field(default="lab", max_length=16)
    allowed_testing: dict[str, bool] = Field(default_factory=dict)
    allowed_permissions: dict[str, list[str]] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    # Required by the registry: an asset is not enrollable without recorded
    # authorization evidence.
    evidence: dict[str, Any]
    valid_from: datetime | None = None
    valid_until: datetime | None = None


@router.post("/assets")
async def enroll_asset(
    request: Request, body: EnrollBody, response: Response
) -> dict[str, Any]:
    runtime = _runtime(request)
    asset = await _call(
        runtime.registry.enroll,
        asset_ref=body.asset_ref,
        name=body.name,
        kind=body.kind,
        locator=body.locator,
        environment=body.environment,
        allowed_testing=body.allowed_testing,
        allowed_permissions=body.allowed_permissions,
        constraints=body.constraints,
        evidence=body.evidence,
        valid_from=body.valid_from,
        valid_until=body.valid_until,
        trace_id=trace_id_var.get(None),
    )
    logger.info("security_asset_enrolled", asset_ref=asset["asset_ref"], kind=asset["kind"])
    response.status_code = 201
    return asset


@router.get("/assets")
async def list_assets(
    request: Request,
    status: str | None = Query(default=None, max_length=16),
    kind: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    runtime = _runtime(request)
    assets = await _call(runtime.registry.list, status=status, kind=kind, limit=limit)
    return {"assets": assets, "kinds": list(ASSET_KINDS), "environments": list(ENVIRONMENTS)}


@router.get("/assets/{ref}")
async def get_asset(request: Request, ref: str) -> dict[str, Any]:
    _reject_long(ref, MAX_REF, "ref")
    runtime = _runtime(request)
    return await _call(runtime.registry.get, ref)


class ScopeChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # `kind` and `locator` are absent on purpose: re-pointing an authorization
    # requires revoke + re-enroll (registry rule 2).
    name: str | None = Field(default=None, max_length=256)
    environment: str | None = Field(default=None, max_length=16)
    allowed_testing: dict[str, bool] | None = None
    allowed_permissions: dict[str, list[str]] | None = None
    constraints: dict[str, Any] | None = None
    valid_until: datetime | None = None
    clear_valid_until: bool = False
    reason: str = Field(default="owner scope change", max_length=MAX_REASON)


@router.patch("/assets/{ref}")
async def change_asset_scope(
    request: Request, ref: str, body: ScopeChangeBody
) -> dict[str, Any]:
    _reject_long(ref, MAX_REF, "ref")
    runtime = _runtime(request)
    return await _call(
        runtime.registry.change_scope,
        ref,
        name=body.name,
        environment=body.environment,
        allowed_testing=body.allowed_testing,
        allowed_permissions=body.allowed_permissions,
        constraints=body.constraints,
        valid_until=body.valid_until,
        clear_valid_until=body.clear_valid_until,
        reason=body.reason,
        trace_id=trace_id_var.get(None),
    )


class StatusChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="owner action", max_length=MAX_REASON)


@router.post("/assets/{ref}/revoke")
async def revoke_asset(request: Request, ref: str, body: StatusChangeBody) -> dict[str, Any]:
    _reject_long(ref, MAX_REF, "ref")
    runtime = _runtime(request)
    return await _call(
        runtime.registry.revoke, ref, reason=body.reason, trace_id=trace_id_var.get(None)
    )


@router.post("/assets/{ref}/suspend")
async def suspend_asset(request: Request, ref: str, body: StatusChangeBody) -> dict[str, Any]:
    _reject_long(ref, MAX_REF, "ref")
    runtime = _runtime(request)
    return await _call(
        runtime.registry.suspend, ref, reason=body.reason, trace_id=trace_id_var.get(None)
    )


# -------------------------------------------------------------- assessments


class AssessmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(max_length=MAX_TARGET)
    testing_class: str = Field(default=TESTING_CLASS_CONFIGURATION_AUDIT, max_length=64)
    # Optional narrowing INSIDE an owner-recorded config root; it can never
    # introduce a new root (assessments.authorized_config_root).
    config_root: str | None = Field(default=None, max_length=MAX_PATH)
    disruption: str = Field(default="none", max_length=16)


@router.post("/assessments")
async def run_assessment(
    request: Request, body: AssessmentBody, response: Response
) -> dict[str, Any]:
    runtime = _runtime(request)
    result = await _call(
        runtime.assessments.run,
        target=body.target,
        testing_class=body.testing_class,
        config_root=body.config_root,
        disruption=body.disruption,
        trace_id=trace_id_var.get(None),
    )
    response.status_code = 201
    return result


@router.get("/assessments")
async def list_assessments(
    request: Request,
    asset_ref: str | None = Query(default=None, max_length=MAX_REF),
    status: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    runtime = _runtime(request)
    rows = await _call(
        runtime.assessments.list, asset_ref=asset_ref, status=status, limit=limit
    )
    return {"assessments": rows}


@router.get("/assessments/{assessment_id}")
async def get_assessment(request: Request, assessment_id: uuid.UUID) -> dict[str, Any]:
    runtime = _runtime(request)
    return await _call(runtime.assessments.get, assessment_id)


def _publish(runtime: SecurityRuntime, assessment_id: uuid.UUID) -> dict[str, Any]:
    with runtime.session() as session:
        return publish_assessment_artifact(
            session, runtime.store, assessment_id=assessment_id
        )


@router.post("/assessments/{assessment_id}/artifact")
async def publish_assessment(
    request: Request, assessment_id: uuid.UUID, response: Response
) -> dict[str, Any]:
    """Turn the assessment into a security artifact (canonical + renders)."""
    runtime = _runtime(request)
    result = await _call(_publish, runtime, assessment_id)
    response.status_code = 201
    return result


# ----------------------------------------------------------------- findings


@router.get("/findings")
async def list_findings(
    request: Request,
    asset_ref: str | None = Query(default=None, max_length=MAX_REF),
    assessment_id: uuid.UUID | None = _OPTIONAL_UUID_QUERY,
    severity: str | None = Query(default=None, max_length=16),
    status: str | None = Query(default=None, max_length=24),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    runtime = _runtime(request)
    rows = await _call(
        runtime.assessments.list_findings,
        asset_ref=asset_ref,
        assessment_id=assessment_id,
        severity=severity,
        status=status,
        limit=limit,
    )
    return {"findings": rows}


class RemediateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Default is PROPOSE: finding a problem never implies permission to change
    # the owner's machine.
    mode: str = Field(default=MODE_PROPOSE, max_length=16)


@router.post("/findings/{finding_id}/remediate")
async def remediate_finding(
    request: Request, finding_id: uuid.UUID, body: RemediateBody
) -> dict[str, Any]:
    if body.mode not in MODES:
        raise HTTPException(
            status_code=422,
            detail={
                "error_class": str(SecurityErrorClass.VALIDATION_ERROR),
                "message": f"mode must be one of {MODES}",
            },
        )
    runtime = _runtime(request)
    return await _call(
        runtime.remediation.remediate,
        finding_id,
        mode=body.mode,
        trace_id=trace_id_var.get(None),
    )


# -------------------------------------------------------------------- audit


@router.get("/audit")
async def list_audit(
    request: Request,
    action: str | None = Query(default=None, max_length=32),
    asset_ref: str | None = Query(default=None, max_length=MAX_REF),
    allowed: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    """The append-only authorization trail: grants, changes AND every refusal."""
    runtime = _runtime(request)
    events = await _call(
        runtime.registry.list_events,
        action=action,
        asset_ref=asset_ref,
        allowed=allowed,
        limit=limit,
    )
    return {"events": events}


# ------------------------------------------------------------- scope preview


class ScopeCheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(max_length=MAX_TARGET)
    testing_class: str = Field(default=TESTING_CLASS_CONFIGURATION_AUDIT, max_length=64)
    disruption: str = Field(default="none", max_length=16)


def _preview(runtime: SecurityRuntime, body: ScopeCheckBody) -> dict[str, Any]:
    with runtime.session() as session:
        decision = runtime.guard.evaluate(
            session,
            target=body.target,
            testing_class=body.testing_class,
            disruption=body.disruption,
            audit=False,
        )
        return decision.to_dict()


@router.post("/scope/check")
async def check_scope(request: Request, body: ScopeCheckBody) -> dict[str, Any]:
    """Read-only: would this be allowed? Runs nothing, audits nothing, and —
    like every other path — cannot enroll anything."""
    runtime = _runtime(request)
    return await _call(_preview, runtime, body)


def _reject_long(value: str, limit: int, field: str) -> None:
    if len(value) > limit:
        raise HTTPException(
            status_code=422,
            detail={
                "error_class": str(SecurityErrorClass.VALIDATION_ERROR),
                "message": f"{field} exceeds {limit} characters",
            },
        )


__all__ = ["router"]
