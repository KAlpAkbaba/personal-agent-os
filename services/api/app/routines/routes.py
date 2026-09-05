"""Routine Engine REST surface (M18).

- GET  /v1/routines/policy         vocabulary + version this Cloud Core understands
- POST /v1/routines                create a routine (armed immediately)
- GET  /v1/routines                list, filterable by status/trigger_kind
- GET  /v1/routines/{routine_id}   one routine
- POST /v1/routines/{routine_id}/cancel
- GET  /v1/routines/{routine_id}/firings   the durable record of every evaluated occurrence
                                            (never silently dropped — task brief)
- POST /v1/routines/evaluate       the explicit "due now" evaluation entry point

Owner-gated like every other surface (research, artifacts, memory, ledger, goals). There is
no route to force-dispatch an action or to override which ``RoutineDispatcher`` runs:
execution wiring is a code-level seam (``app.routines.actions.RoutineDispatcher``), not an
owner-facing knob — an HTTP caller may only ask "is anything due right now?", never "run
this action directly."
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.artifacts.runtime import ArtifactRuntime
from app.identity.dependencies import require_owner_session
from app.routines import service as routines_service
from app.routines.actions import ACTION_KINDS, InvalidActionDescriptor
from app.routines.conditions import CONDITION_KINDS, InvalidCondition, RoutineConditionContext
from app.routines.models import ROUTINE_STATUSES, TRIGGER_KINDS, Routine, RoutineFiring
from app.routines.presence_link import (
    SOURCE_CALLER,
    resolve_greeting_allowed,
    resolve_owner_present,
)
from app.routines.state import IllegalRoutineTransition
from app.routines.triggers import PRESENCE_TRIGGER_EVENTS, InvalidTrigger

router = APIRouter(prefix="/v1/routines", dependencies=[Depends(require_owner_session)])

#: Bumped whenever this surface's shape or vocabulary changes in a way a caller must know
#: about (mirrors app.goals.routes.GOALS_VERSION / app.ledger.routes.LEDGER_VERSION).
ROUTINES_VERSION = 1

_VALIDATION_ERRORS = (InvalidTrigger, InvalidCondition, InvalidActionDescriptor, ValueError)


def _artifacts(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _routine_dict(routine: Routine) -> dict[str, Any]:
    return {
        "routine_id": str(routine.routine_id),
        "name": routine.name,
        "status": routine.status,
        "trigger_kind": routine.trigger_kind,
        "trigger": routine.trigger_json,
        "conditions": routine.conditions_json,
        "actions": routine.actions_json,
        "created_at": _iso(routine.created_at),
        "updated_at": _iso(routine.updated_at),
        "armed_at": _iso(routine.armed_at),
        "cancelled_at": _iso(routine.cancelled_at),
        "cancel_reason": routine.cancel_reason,
        "source": routine.source,
        "source_ref": routine.source_ref,
        "detail_json": routine.detail_json,
    }


def _firing_dict(firing: RoutineFiring) -> dict[str, Any]:
    return {
        "firing_id": str(firing.firing_id),
        "routine_id": str(firing.routine_id),
        "occurrence_key": firing.occurrence_key,
        "status": firing.status,
        "conditions_result": firing.conditions_result,
        "actions_snapshot": firing.actions_snapshot,
        "skip_reason": firing.skip_reason,
        "occurred_at": _iso(firing.occurred_at),
        "created_at": _iso(firing.created_at),
    }


# ------------------------------------------------------------------------------ requests


class ConditionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, value: str) -> str:
        if value not in CONDITION_KINDS:
            raise ValueError(f"unknown condition kind: {value!r}; must be one of {CONDITION_KINDS}")
        return value


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, value: str) -> str:
        if value not in ACTION_KINDS:
            raise ValueError(f"unknown action kind: {value!r}; must be one of {ACTION_KINDS}")
        return value


class CreateRoutineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    trigger_kind: str
    trigger: dict[str, Any] = Field(default_factory=dict)
    conditions: list[ConditionIn] = Field(default_factory=list)
    actions: list[ActionIn] = Field(default_factory=list)
    source: str = "owner"
    source_ref: str | None = Field(default=None, max_length=256)
    detail_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("trigger_kind")
    @classmethod
    def _valid_trigger_kind(cls, value: str) -> str:
        if value not in TRIGGER_KINDS:
            raise ValueError(f"unknown trigger_kind: {value!r}; must be one of {TRIGGER_KINDS}")
        return value


class CancelRoutineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class ConditionContextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_present: bool | None = None
    quiet_hours_active: bool | None = None
    display_state: str | None = None
    active_task_present: bool | None = None
    policy_permissions: dict[str, bool] = Field(default_factory=dict)

    def to_context(
        self, *, greeting_allowed: bool | None = None, greeting_reason: str = "not_evaluated"
    ) -> RoutineConditionContext:
        """Build the evaluation context, resolving presence from the engine that knows it.

        A caller may still assert ``owner_present`` - some genuinely know something the
        camera does not - but the assertion is labelled as theirs. When they say nothing,
        the Presence Engine answers, and "stale" resolves to unknown rather than to a
        boolean (app.routines.presence_link).
        """
        if self.owner_present is None:
            owner_present, source = resolve_owner_present()
        else:
            owner_present, source = self.owner_present, SOURCE_CALLER
        return RoutineConditionContext(
            owner_present=owner_present,
            owner_present_source=source,
            quiet_hours_active=self.quiet_hours_active,
            display_state=self.display_state,
            active_task_present=self.active_task_present,
            policy_permissions=dict(self.policy_permissions),
            greeting_allowed=greeting_allowed,
            greeting_reason=greeting_reason,
        )


class EvaluateDueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: ConditionContextIn | None = None


# ---------------------------------------------------------------------------------- routes


@router.get("/policy")
async def get_routines_policy() -> dict[str, Any]:
    return {
        "routines_version": ROUTINES_VERSION,
        "trigger_kinds": list(TRIGGER_KINDS),
        "condition_kinds": list(CONDITION_KINDS),
        "action_kinds": list(ACTION_KINDS),
        "statuses": list(ROUTINE_STATUSES),
        "presence_trigger_events": list(PRESENCE_TRIGGER_EVENTS),
    }


@router.post("", status_code=201)
async def create_routine(request: Request, body: CreateRoutineRequest) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def write() -> Routine:
        with artifacts.session() as session:
            return routines_service.create_routine(
                session,
                name=body.name,
                trigger_kind=body.trigger_kind,
                trigger=body.trigger,
                conditions=[c.model_dump() for c in body.conditions],
                actions=[a.model_dump() for a in body.actions],
                source=body.source,
                source_ref=body.source_ref,
                detail_json=body.detail_json,
            )

    try:
        routine = await asyncio.to_thread(write)
    except _VALIDATION_ERRORS as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _routine_dict(routine)


@router.get("")
async def list_routines(
    request: Request, status: str | None = None, trigger_kind: str | None = None, limit: int = 100
) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> list[Routine]:
        with artifacts.session() as session:
            return routines_service.list_routines(
                session, status=status, trigger_kind=trigger_kind, limit=limit
            )

    rows = await asyncio.to_thread(load)
    return {"routines": [_routine_dict(r) for r in rows]}


@router.get("/{routine_id}")
async def get_routine(request: Request, routine_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> Routine | None:
        with artifacts.session() as session:
            return routines_service.get_routine(session, routine_id)

    routine = await asyncio.to_thread(load)
    if routine is None:
        raise HTTPException(status_code=404, detail="routine not found")
    return _routine_dict(routine)


@router.post("/{routine_id}/cancel")
async def cancel_routine(
    request: Request, routine_id: uuid.UUID, body: CancelRoutineRequest | None = None
) -> dict[str, Any]:
    artifacts = _artifacts(request)
    reason = body.reason if body is not None else None

    def write() -> Routine:
        with artifacts.session() as session:
            return routines_service.cancel_routine(session, routine_id, reason=reason)

    try:
        routine = await asyncio.to_thread(write)
    except routines_service.RoutineNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IllegalRoutineTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _routine_dict(routine)


@router.get("/{routine_id}/firings")
async def list_firings(request: Request, routine_id: uuid.UUID, limit: int = 100) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> Routine | None:
        with artifacts.session() as session:
            routine = routines_service.get_routine(session, routine_id)
            if routine is None:
                return None
            firings = routines_service.list_firings(session, routine_id, limit=limit)
            return firings

    result = await asyncio.to_thread(load)
    if result is None:
        raise HTTPException(status_code=404, detail="routine not found")
    return {"firings": [_firing_dict(f) for f in result]}


@router.post("/evaluate")
async def evaluate_due(request: Request, body: EvaluateDueRequest | None = None) -> dict[str, Any]:
    """The explicit "due now" entry point (task brief: never a background timer)."""
    artifacts = _artifacts(request)
    context_in = body.context if body and body.context else ConditionContextIn()

    def run() -> routines_service.EvaluateDueResult:
        with artifacts.session() as session:
            # The greeting policy needs the ledger (its cooldown is read from the
            # delivery record), so it is resolved here rather than in `to_context`.
            # Evaluating it has no side effect - deliberately: this endpoint is called
            # on every tick, including the ticks where nothing fires.
            greeting_allowed, greeting_reason = resolve_greeting_allowed(session)
            context = context_in.to_context(
                greeting_allowed=greeting_allowed, greeting_reason=greeting_reason
            )
            return routines_service.evaluate_due(session, context=context)

    result = await asyncio.to_thread(run)
    return result.as_dict()


__all__ = ["ROUTINES_VERSION", "router"]
