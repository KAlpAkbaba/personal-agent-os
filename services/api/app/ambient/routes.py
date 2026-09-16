"""Ambient display REST surface (M18.3 spec §3.9, §8.2).

- GET  /v1/ambient/policy         the owner's settings, the live decision, the holdoffs
- PUT  /v1/ambient/policy         change them (owner-gated; starts the command holdoff)
- POST /v1/ambient/test-display   arm the owner's display test

Owner-gated like every other surface. ``GET`` returns the CURRENT DECISION alongside the
policy, not just the settings: "why are my screens still on?" is the question this endpoint
exists to answer, and answering it from the same pure ``decide`` the tick uses means the
explanation can never drift from the behaviour.

Nothing here turns a display off. ``POST /test-display`` arms a moment; the clock issues the
real, receipted ``desktop.display_off`` when it arrives (spec §8.2) — which is also what
gives the owner the ten seconds to take their hand off the keyboard.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.alarms import speech as alarm_speech
from app.ambient import service as ambient_service
from app.ambient.holdoff import get_holdoffs
from app.ambient.policy import decide
from app.artifacts.runtime import ArtifactRuntime
from app.errors import owner_detail
from app.identity.dependencies import require_owner_session

AMBIENT_VERSION = 1

router = APIRouter(
    prefix="/v1/ambient", tags=["ambient"], dependencies=[Depends(require_owner_session)]
)


def _artifacts(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


class PolicyIn(BaseModel):
    """Every field optional: a PUT is a PATCH of the fields the owner named, so a client
    that knows nothing about a setting added later cannot silently reset it."""

    model_config = ConfigDict(extra="forbid")

    auto_off_enabled: bool | None = None
    off_when_away: bool | None = None
    off_when_asleep: bool | None = None
    wake_on_return: bool | None = None
    away_after_s: int | None = Field(default=None, ge=60, le=24 * 3600)
    asleep_after_s: int | None = Field(default=None, ge=60, le=24 * 3600)
    asleep_min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    input_holdoff_s: int | None = Field(default=None, ge=0, le=24 * 3600)
    command_holdoff_s: int | None = Field(default=None, ge=0, le=24 * 3600)
    alarm_holdoff_s: int | None = Field(default=None, ge=0, le=24 * 3600)
    return_holdoff_s: int | None = Field(default=None, ge=0, le=24 * 3600)
    # ADR-0079
    keep_on: bool | None = None
    asleep_after_outside_quiet_s: int | None = Field(default=None, ge=60, le=24 * 3600)
    camera_unknown_grace_s: int | None = Field(default=None, ge=10, le=3600)
    #: {"start": "HH:MM", "end": "HH:MM", "timezone"?: IANA}; validated by the service.
    quiet_hours: dict[str, Any] | None = None
    #: True clears the quiet window (a PATCH cannot say "set this to nothing" otherwise).
    clear_quiet_hours: bool = False


class TestDisplayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay_seconds: int = Field(default=ambient_service.DEFAULT_TEST_DELAY_S, ge=1, le=120)


def _policy_payload(session: Any, *, now: Any = None) -> dict[str, Any]:
    moment = now or ambient_service.utcnow()
    policy = ambient_service.get_policy(session)
    inputs = ambient_service.collect_inputs(session, now=moment)
    decision = decide(inputs, policy, moment)
    return {
        "ambient_version": AMBIENT_VERSION,
        "policy": policy.as_dict(),
        "decision": decision.as_dict(),
        "holdoffs": get_holdoffs().as_dict(now=moment),
        "display": ambient_service.display_state_summary(),
        "pending_display_test_at": (
            ambient_service.pending_display_test().isoformat()
            if ambient_service.pending_display_test()
            else None
        ),
    }


@router.get("/policy")
async def get_policy(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> dict[str, Any]:
        with artifacts.session() as session:
            return _policy_payload(session)

    return await asyncio.to_thread(load)


@router.put("/policy")
async def put_policy(request: Request, body: PolicyIn) -> dict[str, Any]:
    artifacts = _artifacts(request)
    changes = body.model_dump(exclude_none=True)
    if changes.pop("clear_quiet_hours", False):
        changes["quiet_hours"] = {}

    def write() -> tuple[dict[str, Any], dict[str, Any]]:
        with artifacts.session() as session:
            _, applied = ambient_service.set_policy(session, changes, source="rest")
            return _policy_payload(session), applied

    try:
        payload, applied = await asyncio.to_thread(write)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=owner_detail("validation_error")) from exc
    if "auto_off_enabled" in applied:
        speech = (
            alarm_speech.AMBIENT_AUTO_OFF_ON_TR
            if applied["auto_off_enabled"]
            else alarm_speech.AMBIENT_AUTO_OFF_OFF_TR
        )
    else:
        speech = alarm_speech.AMBIENT_POLICY_UPDATED_TR
    return {**payload, "changed": applied, "speech": speech}


@router.get("/explain")
async def explain(request: Request) -> dict[str, Any]:
    """"Ekranları neden kapattın?" as a document (ADR-0079 §12): the same facts the voice
    tool speaks from, from the same ``decide`` the tick uses."""
    artifacts = _artifacts(request)

    def load() -> dict[str, Any]:
        with artifacts.session() as session:
            return ambient_service.explain(session)

    return await asyncio.to_thread(load)


@router.post("/test-display")
async def test_display(request: Request, body: TestDisplayIn | None = None) -> dict[str, Any]:
    """Arm the real display-off the clock will issue (spec §8.2). Production path, bounded."""
    delay = body.delay_seconds if body else ambient_service.DEFAULT_TEST_DELAY_S
    at = ambient_service.schedule_display_test(delay_seconds=delay)
    return {
        "scheduled_at": at.isoformat(),
        "delay_seconds": delay,
        "speech": alarm_speech.AMBIENT_TEST_STARTED_TR.format(seconds=delay),
    }


__all__ = ["AMBIENT_VERSION", "router"]
