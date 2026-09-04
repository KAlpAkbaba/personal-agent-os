"""Goal Engine service (overnight plan Phase 4).

Same discipline as ``app.artifacts.service`` / ``app.ledger.service``: every
function takes an open ``Session`` and commits before returning, so async
routes run it via ``asyncio.to_thread`` and the Cognitive Core's loop
(``app.goals.cognitive``) can call it synchronously in-process.

Success criteria are only ever satisfied **from evidence** (task instructions):
``evaluate()`` is the one place a criterion's ``satisfied`` flag is written,
and it always recomputes it from something durable — a linked task's real
status, a real ledger event, or evidence_refs a capability actually produced
(recorded via ``record_evidence`` before it can count). Nothing in this module
lets a caller just set ``satisfied=True``.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.goals.models import (
    GOAL_HORIZONS,
    GOAL_STATUS_ACHIEVED,
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_WAITING_OWNER,
    Goal,
    GoalTask,
)
from app.goals.state import assert_goal_transition
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_GOAL_ACHIEVED,
    EVENT_TYPE_GOAL_CREATED,
    EVENT_TYPE_GOAL_STATUS_CHANGED,
    SUBSYSTEM_GOAL,
)
from app.logging import get_logger

logger = get_logger("app.goals.service")


def utcnow() -> datetime:
    return datetime.now(UTC)


class GoalNotFoundError(ValueError):
    pass


class OwnerApprovalRequiredError(ValueError):
    """Raised when a goal that requires owner approval tries to leave
    ``waiting_owner`` without an explicit owner action (task instructions:
    "a goal that needs approval cannot leave waiting_owner without an
    explicit owner action")."""


class GoalCycleError(ValueError):
    """Raised when adding a dependency edge would create a cycle."""


class InvalidCheckKind(ValueError):
    """A success criterion named a check_kind outside the closed set below."""


# ------------------------------------------------------------- success criteria
#
# Closed vocabulary of HOW a success criterion may be checked (mirrors
# app.ledger.vocabulary's "closed, not discovered" discipline). Every kind
# resolves ``satisfied`` from something durable, never from a caller's say-so.

CHECK_KIND_MANUAL_EVIDENCE = "manual_evidence"
CHECK_KIND_TASK_STATUS = "task_status"
CHECK_KIND_LEDGER_EVENT = "ledger_event"

CHECK_KINDS: tuple[str, ...] = (
    CHECK_KIND_MANUAL_EVIDENCE,
    CHECK_KIND_TASK_STATUS,
    CHECK_KIND_LEDGER_EVENT,
)


def _eval_manual_evidence(session: Session, goal: Goal, criterion: dict[str, Any]) -> bool:
    """Satisfied once at least one evidence_ref has been recorded against this
    criterion (via ``record_evidence``) — never on creation, never by a bare
    ``satisfied: true`` a caller tried to pass in."""
    return bool(criterion.get("evidence_refs"))


def _eval_task_status(session: Session, goal: Goal, criterion: dict[str, Any]) -> bool:
    from app.artifacts.models import TASK_STATUS_COMPLETED, TASK_STATUS_READY, Task

    detail = criterion.get("detail") or {}
    role = detail.get("task_role")
    target_statuses = detail.get("target_statuses") or [TASK_STATUS_READY, TASK_STATUS_COMPLETED]
    links = list_goal_tasks(session, goal.goal_id)
    if role is not None:
        links = [gt for gt in links if gt.role == role]
    for link in links:
        task = session.get(Task, link.task_id)
        if task is not None and task.status in target_statuses:
            return True
    return False


def _eval_ledger_event(session: Session, goal: Goal, criterion: dict[str, Any]) -> bool:
    detail = criterion.get("detail") or {}
    event_type = detail.get("event_type")
    if not event_type:
        return False
    rows = ledger_service.query(session, event_types=[event_type], limit=50)
    source_ref_prefix = detail.get("source_ref_prefix")
    if source_ref_prefix:
        rows = [r for r in rows if r.source_ref.startswith(source_ref_prefix)]
    return bool(rows)


_EVALUATORS: dict[str, Callable[[Session, Goal, dict[str, Any]], bool]] = {
    CHECK_KIND_MANUAL_EVIDENCE: _eval_manual_evidence,
    CHECK_KIND_TASK_STATUS: _eval_task_status,
    CHECK_KIND_LEDGER_EVENT: _eval_ledger_event,
}


def _normalize_criterion(raw: dict[str, Any]) -> dict[str, Any]:
    check_kind = raw.get("check_kind", CHECK_KIND_MANUAL_EVIDENCE)
    if check_kind not in CHECK_KINDS:
        raise InvalidCheckKind(f"unknown success criterion check_kind: {check_kind!r}")
    return {
        "id": str(raw.get("id") or uuid.uuid4()),
        "statement": str(raw.get("statement", "")),
        "check_kind": check_kind,
        "satisfied": False,  # only evaluate() may flip this
        "evidence_refs": list(raw.get("evidence_refs") or []),
        "detail": dict(raw.get("detail") or {}),
    }


@dataclasses.dataclass(frozen=True, slots=True)
class EvaluatedCriterion:
    id: str
    statement: str
    check_kind: str
    satisfied: bool
    evidence_refs: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True, slots=True)
class EvaluationResult:
    goal_id: uuid.UUID
    criteria: tuple[EvaluatedCriterion, ...]

    @property
    def all_satisfied(self) -> bool:
        return bool(self.criteria) and all(c.satisfied for c in self.criteria)

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_id": str(self.goal_id),
            "all_satisfied": self.all_satisfied,
            "criteria": [c.as_dict() for c in self.criteria],
        }


# ------------------------------------------------------------------- ledger


def _record_ledger(
    session: Session,
    *,
    event_type: str,
    goal: Goal,
    action: str,
    factual_summary: str,
    source_ref: str,
    detail: dict[str, Any] | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
) -> None:
    """Never fails the caller — the ledger is evidence, not a dependency (same
    rule ``app.voice.realtime_sessions.tools._ledger_note`` follows)."""
    try:
        ledger_service.record(
            session,
            ledger_service.ActivityEvent(
                event_type=event_type,
                subsystem=SUBSYSTEM_GOAL,
                action=action,
                factual_summary=factual_summary,
                source="live",
                source_ref=source_ref,
                occurred_at=utcnow(),
                related_goal_id=goal.goal_id,
                evidence_refs=evidence_refs or [],
                detail_json=detail or {},
            ),
        )
    except Exception:  # noqa: BLE001 - ledger is evidence, never a hard dependency
        logger.warning("goal_ledger_note_failed", event_type=event_type, goal_id=str(goal.goal_id))


# --------------------------------------------------------------------- goals


def create_goal(
    session: Session,
    *,
    title: str,
    intent: str,
    parent_goal_id: uuid.UUID | None = None,
    priority: int = 0,
    horizon: str = "someday",
    deadline: datetime | None = None,
    success_criteria: Sequence[dict[str, Any]] | None = None,
    requires_owner_approval: bool = False,
    source: str = "owner",
    source_ref: str | None = None,
    detail_json: dict[str, Any] | None = None,
) -> Goal:
    """Idempotent on ``(source, source_ref)`` like ``app.ledger.service.record``:
    a second create with the same key returns the existing goal unchanged. A
    caller that does not care about idempotency (the common owner/UI path)
    simply omits ``source_ref`` and gets a fresh, always-unique one."""
    if horizon not in GOAL_HORIZONS:
        raise ValueError(f"unknown goal horizon: {horizon!r}")
    source_ref = source_ref or f"{uuid.uuid4()}"
    existing = session.execute(
        select(Goal).where(Goal.source == source, Goal.source_ref == source_ref)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    criteria = [_normalize_criterion(dict(c)) for c in (success_criteria or [])]
    goal = Goal(
        parent_goal_id=parent_goal_id,
        title=title,
        intent=intent,
        priority=priority,
        horizon=horizon,
        deadline=deadline,
        success_criteria=criteria,
        requires_owner_approval=requires_owner_approval,
        source=source,
        source_ref=source_ref,
        detail_json=dict(detail_json or {}),
    )
    session.add(goal)
    session.commit()
    _record_ledger(
        session,
        event_type=EVENT_TYPE_GOAL_CREATED,
        goal=goal,
        action="goal_created",
        factual_summary=f"Hedef oluşturuldu: {title}",
        source_ref=f"goals:{goal.goal_id}:created",
        detail=dict(detail_json or {}),
    )
    return goal


def get_goal(session: Session, goal_id: uuid.UUID) -> Goal | None:
    return session.get(Goal, goal_id)


def _require_goal(session: Session, goal_id: uuid.UUID) -> Goal:
    goal = get_goal(session, goal_id)
    if goal is None:
        raise GoalNotFoundError(f"unknown goal: {goal_id}")
    return goal


def list_goals(
    session: Session,
    *,
    status: str | None = None,
    horizon: str | None = None,
    parent_goal_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[Goal]:
    stmt = select(Goal)
    if status is not None:
        stmt = stmt.where(Goal.status == status)
    if horizon is not None:
        stmt = stmt.where(Goal.horizon == horizon)
    if parent_goal_id is not None:
        stmt = stmt.where(Goal.parent_goal_id == parent_goal_id)
    stmt = stmt.order_by(Goal.priority.desc(), Goal.created_at.desc())
    stmt = stmt.limit(max(1, min(limit, 200)))
    return list(session.execute(stmt).scalars().all())


def list_subgoals(session: Session, parent_goal_id: uuid.UUID) -> list[Goal]:
    return list_goals(session, parent_goal_id=parent_goal_id, limit=200)


def update_goal(
    session: Session,
    goal_id: uuid.UUID,
    *,
    title: str | None = None,
    intent: str | None = None,
    priority: int | None = None,
    horizon: str | None = None,
    deadline: datetime | None = None,
    requires_owner_approval: bool | None = None,
    detail_json: dict[str, Any] | None = None,
) -> Goal:
    """Mutates everything EXCEPT status and success_criteria satisfaction —
    those go through ``transition_status`` / ``evaluate`` on purpose, so an
    ordinary field edit can never sidestep the state machine or fabricate a
    satisfied criterion."""
    goal = _require_goal(session, goal_id)
    if title is not None:
        goal.title = title
    if intent is not None:
        goal.intent = intent
    if priority is not None:
        goal.priority = priority
    if horizon is not None:
        if horizon not in GOAL_HORIZONS:
            raise ValueError(f"unknown goal horizon: {horizon!r}")
        goal.horizon = horizon
    if deadline is not None:
        goal.deadline = deadline
    if requires_owner_approval is not None:
        goal.requires_owner_approval = requires_owner_approval
    if detail_json is not None:
        goal.detail_json = dict(detail_json)
    goal.updated_at = utcnow()
    session.commit()
    return goal


def transition_status(
    session: Session,
    goal_id: uuid.UUID,
    new_status: str,
    *,
    owner_action: bool = False,
    reason: str | None = None,
) -> Goal:
    """Validates the edge (``app.goals.state``), then enforces the
    owner-approval gate: a goal with ``requires_owner_approval`` cannot leave
    ``waiting_owner`` unless ``owner_action=True`` — set ONLY by
    ``approve()`` / an explicit owner-driven route, never by the Cognitive
    Core's own automatic replanning (which always calls this with the
    default ``owner_action=False``, so it can enter ``waiting_owner`` freely
    but can never let itself back out of a gated one)."""
    goal = _require_goal(session, goal_id)
    assert_goal_transition(goal.status, new_status)
    if (
        goal.status == GOAL_STATUS_WAITING_OWNER
        and new_status != GOAL_STATUS_WAITING_OWNER
        and goal.requires_owner_approval
        and not owner_action
    ):
        raise OwnerApprovalRequiredError(
            f"goal {goal_id} requires an explicit owner action to leave waiting_owner"
        )
    previous = goal.status
    goal.status = new_status
    now = utcnow()
    goal.updated_at = now
    if new_status == GOAL_STATUS_ACTIVE and owner_action and goal.approved_at is None:
        goal.approved_at = now
    session.commit()
    _record_ledger(
        session,
        event_type=EVENT_TYPE_GOAL_STATUS_CHANGED,
        goal=goal,
        action="goal_status_changed",
        factual_summary=f"Hedef durumu değişti: {previous} -> {new_status}.",
        source_ref=f"goals:{goal.goal_id}:status:{new_status}:{now.isoformat()}",
        detail={"previous": previous, "new": new_status, "reason": reason},
    )
    if new_status == GOAL_STATUS_ACHIEVED:
        _record_ledger(
            session,
            event_type=EVENT_TYPE_GOAL_ACHIEVED,
            goal=goal,
            action="goal_achieved",
            factual_summary=f"Hedef başarıldı: {goal.title}",
            source_ref=f"goals:{goal.goal_id}:achieved",
            evidence_refs=list(goal.evidence_refs or []),
        )
    return goal


def approve(session: Session, goal_id: uuid.UUID) -> Goal:
    """The only function in this module that may set ``owner_action=True`` —
    everything downstream of an HTTP ``/approve`` call or an owner voice
    command routes through here."""
    _require_goal(session, goal_id)
    return transition_status(session, goal_id, GOAL_STATUS_ACTIVE, owner_action=True)


def record_blocker(
    session: Session, goal_id: uuid.UUID, *, reason: str, detail: dict[str, Any] | None = None
) -> Goal:
    """Append-only; does not itself change ``status`` — a caller (typically
    the Cognitive Core) decides separately whether a blocker warrants
    ``transition_status(..., GOAL_STATUS_BLOCKED)``."""
    goal = _require_goal(session, goal_id)
    blockers = list(goal.blockers or [])
    blockers.append(
        {"reason": reason, "detail": dict(detail or {}), "recorded_at": utcnow().isoformat()}
    )
    goal.blockers = blockers
    goal.updated_at = utcnow()
    session.commit()
    return goal


def _depends_on_set(goal: Goal) -> set[str]:
    return {str(g) for g in (goal.depends_on or [])}


def add_dependency(session: Session, goal_id: uuid.UUID, depends_on_goal_id: uuid.UUID) -> Goal:
    """``goal_id`` may not proceed until ``depends_on_goal_id`` is resolved.
    Refuses self-dependency and any edge that would create a cycle (a DFS
    from ``depends_on_goal_id`` along its own ``depends_on`` edges must never
    reach ``goal_id``)."""
    if goal_id == depends_on_goal_id:
        raise GoalCycleError(f"goal {goal_id} cannot depend on itself")
    goal = _require_goal(session, goal_id)
    _require_goal(session, depends_on_goal_id)

    visited: set[uuid.UUID] = set()
    stack = [depends_on_goal_id]
    while stack:
        current = stack.pop()
        if current == goal_id:
            raise GoalCycleError(
                f"adding dependency {goal_id} -> {depends_on_goal_id} would create a cycle"
            )
        if current in visited:
            continue
        visited.add(current)
        current_goal = get_goal(session, current)
        if current_goal is None:
            continue
        for dep in _depends_on_set(current_goal):
            try:
                stack.append(uuid.UUID(dep))
            except ValueError:
                continue

    deps = _depends_on_set(goal)
    deps.add(str(depends_on_goal_id))
    goal.depends_on = sorted(deps)
    goal.updated_at = utcnow()
    session.commit()
    return goal


# ---------------------------------------------------------------- goal_tasks


def link_task(
    session: Session, goal_id: uuid.UUID, task_id: uuid.UUID, *, role: str = "primary"
) -> GoalTask:
    """Idempotent on ``(goal_id, task_id)``."""
    _require_goal(session, goal_id)
    existing = session.execute(
        select(GoalTask).where(GoalTask.goal_id == goal_id, GoalTask.task_id == task_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    link = GoalTask(goal_id=goal_id, task_id=task_id, role=role)
    session.add(link)
    session.commit()
    return link


def list_goal_tasks(session: Session, goal_id: uuid.UUID) -> list[GoalTask]:
    return list(
        session.execute(select(GoalTask).where(GoalTask.goal_id == goal_id)).scalars().all()
    )


# ------------------------------------------------------------------ evidence


def record_evidence(
    session: Session, goal_id: uuid.UUID, criterion_id: str, evidence_ref: dict[str, Any]
) -> Goal:
    """Attaches evidence to one success criterion. Does NOT set ``satisfied``
    — only ``evaluate()`` does that, and only by re-deriving it from what is
    actually stored (task instructions: "never by assertion")."""
    goal = _require_goal(session, goal_id)
    criteria = list(goal.success_criteria or [])
    found = False
    updated: list[dict[str, Any]] = []
    for raw in criteria:
        criterion = dict(raw)
        if str(criterion.get("id")) == str(criterion_id):
            found = True
            refs = list(criterion.get("evidence_refs") or [])
            refs.append(dict(evidence_ref))
            criterion["evidence_refs"] = refs
        updated.append(criterion)
    if not found:
        raise ValueError(f"goal {goal_id} has no success criterion {criterion_id!r}")
    goal.success_criteria = updated
    goal.updated_at = utcnow()
    session.commit()
    return goal


def evaluate(session: Session, goal_id: uuid.UUID) -> EvaluationResult:
    """Recomputes ``satisfied`` for every success criterion FROM EVIDENCE —
    never by trusting whatever value was already on the row — and persists
    any change. Safe to call repeatedly (idempotent: unchanged evidence
    yields the same result and no write)."""
    goal = _require_goal(session, goal_id)
    updated: list[dict[str, Any]] = []
    evaluated: list[EvaluatedCriterion] = []
    changed = False
    for raw in goal.success_criteria or []:
        criterion = dict(raw)
        check_kind = criterion.get("check_kind", CHECK_KIND_MANUAL_EVIDENCE)
        evaluator = _EVALUATORS.get(check_kind)
        satisfied = bool(evaluator(session, goal, criterion)) if evaluator is not None else False
        if bool(criterion.get("satisfied")) != satisfied:
            changed = True
        criterion["satisfied"] = satisfied
        updated.append(criterion)
        evaluated.append(
            EvaluatedCriterion(
                id=str(criterion.get("id")),
                statement=str(criterion.get("statement", "")),
                check_kind=check_kind,
                satisfied=satisfied,
                evidence_refs=list(criterion.get("evidence_refs") or []),
            )
        )
    if changed:
        goal.success_criteria = updated
        goal.updated_at = utcnow()
        session.commit()
    return EvaluationResult(goal_id=goal.goal_id, criteria=tuple(evaluated))


def evaluate_and_maybe_complete(session: Session, goal_id: uuid.UUID) -> EvaluationResult:
    """``evaluate()`` plus the one side effect every caller of it wants: an
    ACTIVE goal whose criteria are now all satisfied moves to ACHIEVED. Used
    by both the owner-facing ``POST /v1/goals/{id}/evaluate`` route and the
    Cognitive Core's loop so the "did we just finish?" rule lives in exactly
    one place."""
    result = evaluate(session, goal_id)
    goal = get_goal(session, goal_id)
    if goal is not None and result.all_satisfied and goal.status == GOAL_STATUS_ACTIVE:
        transition_status(session, goal_id, GOAL_STATUS_ACHIEVED)
    return result


__all__ = [
    "CHECK_KINDS",
    "CHECK_KIND_LEDGER_EVENT",
    "CHECK_KIND_MANUAL_EVIDENCE",
    "CHECK_KIND_TASK_STATUS",
    "EvaluatedCriterion",
    "EvaluationResult",
    "GoalCycleError",
    "GoalNotFoundError",
    "InvalidCheckKind",
    "OwnerApprovalRequiredError",
    "add_dependency",
    "approve",
    "create_goal",
    "evaluate",
    "evaluate_and_maybe_complete",
    "get_goal",
    "link_task",
    "list_goal_tasks",
    "list_goals",
    "list_subgoals",
    "record_blocker",
    "record_evidence",
    "transition_status",
    "update_goal",
    "utcnow",
]
