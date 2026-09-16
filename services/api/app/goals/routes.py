"""Goal Engine REST surface (overnight plan Phase 4).

- GET  /v1/goals/policy           GOALS_VERSION + the closed vocabulary this Cloud Core understands
- GET  /v1/goals                  list, filterable by status/horizon/parent_goal_id
- POST /v1/goals                  create a goal (or subgoal, via parent_goal_id)
- GET  /v1/goals/{goal_id}        one goal
- POST /v1/goals/{goal_id}/status update-status (never sets owner_action — see below)
- POST /v1/goals/{goal_id}/approve the ONLY route that may clear the owner-approval gate
- POST /v1/goals/{goal_id}/evaluate re-derive success criteria from evidence, maybe complete

Owner-gated like every other surface (research, artifacts, memory, ledger).
``/status`` deliberately never passes ``owner_action=True`` to
``app.goals.service.transition_status`` — even though every caller here
already holds an authenticated owner session — because the gate exists to
stop the Cognitive Core's OWN automation from walking a gated goal out of
``waiting_owner`` on its own; only a genuine ``/approve`` call may do that
(``app.goals.service`` module docstring).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.artifacts.runtime import ArtifactRuntime
from app.errors import owner_detail
from app.goals import service as goals_service
from app.goals.models import GOAL_HORIZONS, GOAL_STATUSES, Goal
from app.goals.service import CHECK_KINDS
from app.identity.dependencies import require_owner_session

router = APIRouter(prefix="/v1/goals", dependencies=[Depends(require_owner_session)])

#: Bumped whenever this surface's shape or vocabulary changes in a way a
#: caller must know about (mirrors app.ledger.routes.LEDGER_VERSION).
GOALS_VERSION = 1


def _artifacts(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    from datetime import UTC

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _goal_dict(goal: Goal) -> dict[str, Any]:
    return {
        "goal_id": str(goal.goal_id),
        "parent_goal_id": str(goal.parent_goal_id) if goal.parent_goal_id else None,
        "title": goal.title,
        "intent": goal.intent,
        "status": goal.status,
        "priority": goal.priority,
        "horizon": goal.horizon,
        "deadline": _iso(goal.deadline),
        "success_criteria": goal.success_criteria,
        "blockers": goal.blockers,
        "depends_on": goal.depends_on,
        "requires_owner_approval": goal.requires_owner_approval,
        "approved_at": _iso(goal.approved_at),
        "evidence_refs": goal.evidence_refs,
        "created_at": _iso(goal.created_at),
        "updated_at": _iso(goal.updated_at),
        "source": goal.source,
        "source_ref": goal.source_ref,
        "detail_json": goal.detail_json,
    }


class SuccessCriterionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    statement: str = Field(min_length=1, max_length=2000)
    check_kind: str = "manual_evidence"
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("check_kind")
    @classmethod
    def _valid_check_kind(cls, value: str) -> str:
        if value not in CHECK_KINDS:
            raise ValueError(f"unknown check_kind: {value!r}; must be one of {CHECK_KINDS}")
        return value


class CreateGoalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    intent: str = Field(min_length=1, max_length=4000)
    parent_goal_id: uuid.UUID | None = None
    priority: int = 0
    horizon: str = "someday"
    deadline: datetime | None = None
    success_criteria: list[SuccessCriterionIn] = Field(default_factory=list)
    requires_owner_approval: bool = False
    source: str = "owner"
    source_ref: str | None = Field(default=None, max_length=256)
    detail_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("horizon")
    @classmethod
    def _valid_horizon(cls, value: str) -> str:
        if value not in GOAL_HORIZONS:
            raise ValueError(f"unknown horizon: {value!r}; must be one of {GOAL_HORIZONS}")
        return value


class UpdateGoalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    intent: str | None = Field(default=None, min_length=1, max_length=4000)
    priority: int | None = None
    horizon: str | None = None
    deadline: datetime | None = None
    requires_owner_approval: bool | None = None
    detail_json: dict[str, Any] | None = None

    @field_validator("horizon")
    @classmethod
    def _valid_horizon(cls, value: str | None) -> str | None:
        if value is not None and value not in GOAL_HORIZONS:
            raise ValueError(f"unknown horizon: {value!r}; must be one of {GOAL_HORIZONS}")
        return value


class UpdateStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str) -> str:
        if value not in GOAL_STATUSES:
            raise ValueError(f"unknown status: {value!r}; must be one of {GOAL_STATUSES}")
        return value


class AddDependencyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depends_on_goal_id: uuid.UUID


@router.get("/policy")
async def get_goals_policy() -> dict[str, Any]:
    return {
        "goals_version": GOALS_VERSION,
        "statuses": list(GOAL_STATUSES),
        "horizons": list(GOAL_HORIZONS),
        "check_kinds": list(CHECK_KINDS),
    }


@router.get("")
async def list_goals(
    request: Request,
    status: str | None = None,
    horizon: str | None = None,
    parent_goal_id: uuid.UUID | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> list[Goal]:
        with artifacts.session() as session:
            return goals_service.list_goals(
                session, status=status, horizon=horizon, parent_goal_id=parent_goal_id, limit=limit
            )

    rows = await asyncio.to_thread(load)
    return {"goals": [_goal_dict(g) for g in rows]}


@router.post("", status_code=201)
async def create_goal(request: Request, body: CreateGoalRequest) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def write() -> Goal:
        with artifacts.session() as session:
            return goals_service.create_goal(
                session,
                title=body.title,
                intent=body.intent,
                parent_goal_id=body.parent_goal_id,
                priority=body.priority,
                horizon=body.horizon,
                deadline=body.deadline,
                success_criteria=[c.model_dump() for c in body.success_criteria],
                requires_owner_approval=body.requires_owner_approval,
                source=body.source,
                source_ref=body.source_ref,
                detail_json=body.detail_json,
            )

    goal = await asyncio.to_thread(write)
    return _goal_dict(goal)


@router.get("/{goal_id}")
async def get_goal(request: Request, goal_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> Goal | None:
        with artifacts.session() as session:
            return goals_service.get_goal(session, goal_id)

    goal = await asyncio.to_thread(load)
    if goal is None:
        raise HTTPException(status_code=404, detail="goal not found")
    return _goal_dict(goal)


@router.patch("/{goal_id}")
async def update_goal(
    request: Request, goal_id: uuid.UUID, body: UpdateGoalRequest
) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def write() -> Goal:
        with artifacts.session() as session:
            return goals_service.update_goal(
                session,
                goal_id,
                title=body.title,
                intent=body.intent,
                priority=body.priority,
                horizon=body.horizon,
                deadline=body.deadline,
                requires_owner_approval=body.requires_owner_approval,
                detail_json=body.detail_json,
            )

    try:
        goal = await asyncio.to_thread(write)
    except goals_service.GoalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=owner_detail("not_found")) from exc
    return _goal_dict(goal)


@router.post("/{goal_id}/status")
async def update_status(
    request: Request, goal_id: uuid.UUID, body: UpdateStatusRequest
) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def write() -> Goal:
        with artifacts.session() as session:
            # owner_action is NEVER True here (module docstring): this route is
            # for ordinary lifecycle moves, not for clearing the approval gate.
            return goals_service.transition_status(
                session, goal_id, body.status, owner_action=False, reason=body.reason
            )

    try:
        goal = await asyncio.to_thread(write)
    except goals_service.GoalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=owner_detail("not_found")) from exc
    except goals_service.OwnerApprovalRequiredError as exc:
        raise HTTPException(
            status_code=403,
            detail=owner_detail(
                "permission_denied",
                specific="Bu adım için senin onayın gerekiyor efendim.",
            ),
        ) from exc
    except ValueError as exc:
        # covers app.goals.state.IllegalGoalTransition (a ValueError subclass)
        raise HTTPException(status_code=409, detail=owner_detail("lifecycle_violation")) from exc
    return _goal_dict(goal)


@router.post("/{goal_id}/approve")
async def approve_goal(request: Request, goal_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def write() -> Goal:
        with artifacts.session() as session:
            return goals_service.approve(session, goal_id)

    try:
        goal = await asyncio.to_thread(write)
    except goals_service.GoalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=owner_detail("not_found")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=owner_detail("lifecycle_violation")) from exc
    return _goal_dict(goal)


@router.post("/{goal_id}/evaluate")
async def evaluate_goal(request: Request, goal_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def run() -> tuple[dict[str, Any], Goal]:
        with artifacts.session() as session:
            result = goals_service.evaluate_and_maybe_complete(session, goal_id)
            goal = goals_service.get_goal(session, goal_id)
            if goal is None:
                raise goals_service.GoalNotFoundError(f"unknown goal: {goal_id}")
            return result.as_dict(), goal

    try:
        result, goal = await asyncio.to_thread(run)
    except goals_service.GoalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=owner_detail("not_found")) from exc
    return {**result, "goal": _goal_dict(goal)}


@router.post("/{goal_id}/dependencies")
async def add_dependency(
    request: Request, goal_id: uuid.UUID, body: AddDependencyRequest
) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def write() -> Goal:
        with artifacts.session() as session:
            return goals_service.add_dependency(session, goal_id, body.depends_on_goal_id)

    try:
        goal = await asyncio.to_thread(write)
    except goals_service.GoalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=owner_detail("not_found")) from exc
    except goals_service.GoalCycleError as exc:
        raise HTTPException(
            status_code=409,
            detail=owner_detail(
                "constraint_violation",
                specific="Bu bağlantı hedefleri bir döngüye sokuyor; kurmadım.",
            ),
        ) from exc
    return _goal_dict(goal)
