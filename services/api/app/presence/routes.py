"""Presence + Active Eye REST surface (M18_HOLOGRAPHIC_CORE_SPEC.md §1, §2).

- POST /v1/presence/observations     ingest one structured observation
- GET  /v1/presence/state            the current assertion (never a fact — see app.presence.states)
- GET  /v1/presence/policy           the fusion + greeting thresholds, inspectable
- POST /v1/presence/eye/enable       resume perception (owner action)
- POST /v1/presence/eye/disable      stop perception immediately (owner action)
- POST /v1/presence/greeting/evaluate  the greeting decision, given real history
- POST /v1/presence/greeting/delivered a greeting was narrated; start the cooldown

Owner-gated like every other surface (research, artifacts, memory, ledger,
goals, world model). This is deliberate even for camera-sourced observations
originating from the owner's own local-perception client: **perception is
never authentication** (spec §2) — a device that can post an observation must
already hold a real owner session, exactly like every other write surface,
never a lesser or presence-derived credential.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.artifacts.runtime import ArtifactRuntime
from app.identity.dependencies import require_owner_session
from app.presence import service as presence_service
from app.presence.engine import DEFAULT_POLICY, get_engine
from app.presence.eye import disable_eye, enable_eye, is_eye_enabled
from app.presence.greeting import DEFAULT_GREETING_POLICY
from app.presence.observations import (
    ACTIVITY_LEVELS,
    AWAKE_STATES,
    OBSERVATION_FIELDS,
    POSTURES,
    SOURCES,
    ObservationRejected,
)

router = APIRouter(
    prefix="/v1/presence", tags=["presence"], dependencies=[Depends(require_owner_session)]
)


def _artifacts(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


class EyeActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=200)


@router.get("/policy")
async def get_presence_policy() -> dict[str, Any]:
    """The closed vocabulary and every tunable threshold — inspectable and
    testable rather than scattered conditionals (task brief; spec §1)."""
    policy = DEFAULT_POLICY
    greeting_policy = DEFAULT_GREETING_POLICY
    return {
        "observation_fields": sorted(OBSERVATION_FIELDS),
        "activity_levels": list(ACTIVITY_LEVELS),
        "postures": list(POSTURES),
        "awake_states": list(AWAKE_STATES),
        "sources": list(SOURCES),
        "fusion_policy": {
            "ttl_s": dict(policy.ttl_s),
            "min_observations": policy.min_observations,
            "min_window_s": policy.min_window_s,
            "min_sustain_s": policy.min_sustain_s,
            "likely_asleep_after_s": policy.likely_asleep_after_s,
            "conflict_disagreement_threshold": policy.conflict_disagreement_threshold,
            "conflict_confidence_multiplier": policy.conflict_confidence_multiplier,
            "min_confidence": policy.min_confidence,
        },
        "greeting_policy": {
            "min_rest_duration_s": greeting_policy.min_rest_duration_s,
            "min_awake_duration_s": greeting_policy.min_awake_duration_s,
            "cooldown_s": greeting_policy.cooldown_s,
            "plausible_hours": sorted(greeting_policy.plausible_hours),
            "timezone": str(greeting_policy.timezone),
        },
    }


@router.get("/state")
async def get_presence_state(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def read() -> bool:
        with artifacts.session() as session:
            return is_eye_enabled(session)

    eye_enabled = await asyncio.to_thread(read)
    assertion = get_engine().current()
    return {
        "assertion": assertion.as_dict() if assertion else None,
        "eye_enabled": eye_enabled,
    }


@router.post("/observations", status_code=201)
async def post_observation(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def write() -> tuple[dict[str, Any], dict[str, Any], bool]:
        with artifacts.session() as session:
            observation, assertion, changed = presence_service.ingest_observation(session, body)
            return observation.as_dict(), assertion.as_dict(), changed

    try:
        observation_dict, assertion_dict, changed = await asyncio.to_thread(write)
    except ObservationRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except presence_service.EyeDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"observation": observation_dict, "assertion": assertion_dict, "changed": changed}


@router.post("/eye/enable")
async def post_eye_enable(request: Request, body: EyeActionRequest | None = None) -> dict[str, Any]:
    artifacts = _artifacts(request)
    reason = body.reason if body else ""

    def write() -> None:
        with artifacts.session() as session:
            enable_eye(session, reason=reason)

    await asyncio.to_thread(write)
    return {"eye_enabled": True}


@router.post("/eye/disable")
async def post_eye_disable(
    request: Request, body: EyeActionRequest | None = None
) -> dict[str, Any]:
    artifacts = _artifacts(request)
    reason = body.reason if body else ""

    def write() -> None:
        with artifacts.session() as session:
            disable_eye(session, reason=reason)

    await asyncio.to_thread(write)
    return {"eye_enabled": False}


@router.post("/greeting/evaluate")
async def post_evaluate_greeting(request: Request) -> dict[str, Any]:
    """The decision only. Evaluating does not start the cooldown, because evaluating
    is not greeting - see app.presence.service.evaluate_greeting_now."""
    artifacts = _artifacts(request)

    def run() -> dict[str, Any]:
        with artifacts.session() as session:
            decision = presence_service.evaluate_greeting_now(session)
            return decision.as_dict()

    return await asyncio.to_thread(run)


@router.post("/greeting/delivered")
async def post_greeting_delivered(request: Request) -> dict[str, Any]:
    """Report that a greeting was actually narrated, which starts the cooldown.

    Separate from the evaluation on purpose: only the thing that spoke knows whether
    the owner heard anything, and a cooldown begun by an evaluation would suppress the
    real greeting for the whole window.
    """
    artifacts = _artifacts(request)

    def run() -> dict[str, Any]:
        with artifacts.session() as session:
            decision = presence_service.evaluate_greeting_now(session)
            if not decision.should_greet:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "greeting_not_currently_warranted",
                        "reason": decision.reason,
                    },
                )
            presence_service.record_greeting_delivered(session, decision)
            return decision.as_dict()

    return await asyncio.to_thread(run)


__all__ = ["router"]
