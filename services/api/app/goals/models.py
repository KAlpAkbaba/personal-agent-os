"""Goal Engine ORM rows (overnight plan Phase 4: Goal Engine + Cognitive Core
foundation).

Canonical schema: the integrator writes the alembic migration for these two
tables (task instructions); this module only needs to create them from
metadata for SQLite unit tests, same discipline as ``app.ledger.models``.

Goals sit ABOVE tasks (``app.artifacts.models.Task``, ARCHITECTURE.md §4): a
goal is an owner-level intent the Cognitive Core (``app.goals.cognitive``)
plans and pursues over time; it is realized through zero or more concrete
Tasks, linked via ``goal_tasks``. A goal never duplicates task execution
state — ``GoalTask.role`` records *why* a task belongs to a goal ("primary",
"verification", ...), never a redundant status column; the task's own status
(``app.artifacts.models.Task.status``) remains the only truth about that task.

``GoalTask.task_id`` deliberately carries no ``ForeignKey`` to ``tasks.id``,
mirroring ``ActivityEventRow.research_job_id`` in ``app.ledger.models`` — this
package must not import ``app.artifacts.models`` at the schema level, only at
the service level where it is actually needed (evaluating a ``task_status``
success criterion).

``Goal.depends_on`` (a JSON list of ``goal_id`` strings) models "goal A cannot
proceed until goal B is done" as a plain column rather than a third table: the
edge set for one owner's goal graph is small, and ``app.goals.service`` is the
only place cycles are validated, so a separate join table would add schema
without adding safety.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

# ------------------------------------------------------------------ statuses

GOAL_STATUS_DRAFT = "draft"
GOAL_STATUS_ACTIVE = "active"
GOAL_STATUS_BLOCKED = "blocked"
GOAL_STATUS_WAITING_OWNER = "waiting_owner"
GOAL_STATUS_ACHIEVED = "achieved"
GOAL_STATUS_ABANDONED = "abandoned"
GOAL_STATUS_SUPERSEDED = "superseded"

GOAL_STATUSES: tuple[str, ...] = (
    GOAL_STATUS_DRAFT,
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_BLOCKED,
    GOAL_STATUS_WAITING_OWNER,
    GOAL_STATUS_ACHIEVED,
    GOAL_STATUS_ABANDONED,
    GOAL_STATUS_SUPERSEDED,
)

#: no legal edge leaves any of these (app.goals.state); a goal here is done.
GOAL_TERMINAL_STATUSES = frozenset(
    {GOAL_STATUS_ACHIEVED, GOAL_STATUS_ABANDONED, GOAL_STATUS_SUPERSEDED}
)

# ------------------------------------------------------------------- horizons

GOAL_HORIZON_NOW = "now"
GOAL_HORIZON_TODAY = "today"
GOAL_HORIZON_WEEK = "week"
GOAL_HORIZON_MONTH = "month"
GOAL_HORIZON_SOMEDAY = "someday"

GOAL_HORIZONS: tuple[str, ...] = (
    GOAL_HORIZON_NOW,
    GOAL_HORIZON_TODAY,
    GOAL_HORIZON_WEEK,
    GOAL_HORIZON_MONTH,
    GOAL_HORIZON_SOMEDAY,
)


class Goal(Base):
    __tablename__ = "goals"
    __table_args__ = (UniqueConstraint("source", "source_ref", name="uq_goals_source_ref"),)

    goal_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    parent_goal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("goals.goal_id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=GOAL_STATUS_DRAFT, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    horizon: Mapped[str] = mapped_column(String(16), nullable=False, default=GOAL_HORIZON_SOMEDAY)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: [{"id": str, "statement": str, "check_kind": str, "satisfied": bool,
    #:   "evidence_refs": [...], "detail": {...}}, ...] — see app.goals.service
    #: CHECK_KINDS for the closed set of evaluators. Never hand-set to True by
    #: a caller; only app.goals.service.evaluate() may flip it, from evidence.
    success_criteria: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: [{"reason": str, "detail": {...}, "recorded_at": iso str}, ...], append-only.
    blockers: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: goal_id strings this goal depends on (must be resolvable before this one
    #: can proceed). app.goals.service refuses an edge that would create a cycle.
    depends_on: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    requires_owner_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: "owner" | "cognitive_core" | ... ; who/what asked for this goal to exist.
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="owner")
    #: idempotency key within `source` (mirrors app.ledger.models.ActivityEventRow).
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)


class GoalTask(Base):
    """Links a goal to a real, executing task (app.artifacts.models.Task)."""

    __tablename__ = "goal_tasks"
    __table_args__ = (UniqueConstraint("goal_id", "task_id", name="uq_goal_tasks_goal_task"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("goals.goal_id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: app.artifacts.models.Task.id — no cross-module ForeignKey (module docstring).
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    #: "primary" | "subtask" | "verification" | caller-defined; documents WHY
    #: the task belongs to the goal, never a duplicate of the task's own status.
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="primary")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "GOAL_HORIZONS",
    "GOAL_HORIZON_MONTH",
    "GOAL_HORIZON_NOW",
    "GOAL_HORIZON_SOMEDAY",
    "GOAL_HORIZON_TODAY",
    "GOAL_HORIZON_WEEK",
    "GOAL_STATUSES",
    "GOAL_STATUS_ABANDONED",
    "GOAL_STATUS_ACHIEVED",
    "GOAL_STATUS_ACTIVE",
    "GOAL_STATUS_BLOCKED",
    "GOAL_STATUS_DRAFT",
    "GOAL_STATUS_SUPERSEDED",
    "GOAL_STATUS_WAITING_OWNER",
    "GOAL_TERMINAL_STATUSES",
    "Goal",
    "GoalTask",
]
