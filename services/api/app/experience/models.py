"""Experience Compiler ORM row: candidate procedural lessons.

ONE new table (``experience_lessons``) — everything else this package touches
(memories, ledger events, incidents, research reports) already has a durable
home in ``app.memory`` / ``app.ledger`` / ``app.selfhealing`` / ``app.research``.

Follows the style of ``app.ledger.models``: portable types (generic ``Uuid``,
``JSON`` with a ``JSONB`` variant) so PostgreSQL stays canonical while unit
tests run the identical schema on SQLite via ``Base.metadata`` /
``ExperienceLessonRow.__table__.create(engine)`` — no alembic revision is
shipped from this package; the integrator writes one migration for every new
table added this pass.

A lesson row is the compiler's audit trail for one candidate generalization:
which incident(s)/evidence it came from, the root cause and resolution text
that justify it, and the score that decided whether it was ALSO written as a
memory (candidate-stage procedural memory, auto-recorded by the compiler when
the score crosses ``compiler.AUTO_PROMOTE_SCORE_THRESHOLD`` — see that
module's docstring) or left for the owner to review via
``POST /v1/experience/lessons/{id}/promote``.

``status`` values (never a 5th "auto-promoted" status: whether a memory was
created because the compiler's own threshold fired or because the owner asked
for it, `promoted` + `promoted_memory_id` records the outcome; `detail_json`
carries which one happened):

- ``candidate``  — compiled but not (yet) turned into a memory.
- ``promoted``   — a PROCEDURAL memory exists for this lesson
  (``promoted_memory_id`` set); ``detail_json["promotion"]["kind"]`` is
  ``"auto"`` (compiler threshold) or ``"owner"`` (explicit owner action).
- ``rejected``   — the owner explicitly dismissed this candidate.
- ``superseded`` — reserved for a future re-compile that replaces this row
  with a better-evidenced one; unused by the current compiler (a re-compile
  updates a still-``candidate`` row in place instead — see
  ``compiler.compile_lessons``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
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

STATUS_CANDIDATE = "candidate"
STATUS_PROMOTED = "promoted"
STATUS_REJECTED = "rejected"
STATUS_SUPERSEDED = "superseded"

LESSON_STATUSES: tuple[str, ...] = (
    STATUS_CANDIDATE,
    STATUS_PROMOTED,
    STATUS_REJECTED,
    STATUS_SUPERSEDED,
)

#: `scope` free-text convention: a ledger subsystem name (app.ledger.vocabulary
#: .SUBSYSTEMS) or this sentinel for a lesson that applies everywhere.
SCOPE_GLOBAL = "global"


class ExperienceLessonRow(Base):
    """One candidate (incident -> root_cause -> resolution -> lesson) row."""

    __tablename__ = "experience_lessons"
    __table_args__ = (
        UniqueConstraint("source", "source_ref", name="uq_experience_lessons_source_ref"),
    )

    lesson_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    #: the generalized lesson statement itself — what gets embedded/taught.
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    #: ledger event ids / incident ids this lesson's "incident" step cites.
    incident_refs: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: every ledger event id (and any other durable ref) backing the whole
    #: incident -> root_cause -> resolution chain — never fabricated.
    evidence_refs: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resolution: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: a ledger subsystem name this lesson is scoped to, or SCOPE_GLOBAL.
    scope: Mapped[str] = mapped_column(String(64), nullable=False, default=SCOPE_GLOBAL)
    #: how many distinct incidents/events support this exact lesson.
    recurrence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    generalizability: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    owner_relevance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_overgeneralization: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: compiler.score_lesson(...) output at last compile; drives auto-promotion.
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_CANDIDATE, index=True
    )
    promoted_memory_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: "experience_compiler" today; a stable component name for idempotency.
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    #: idempotency key within `source`, e.g. "incident:<incident_id>:<pattern>".
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)


__all__ = [
    "LESSON_STATUSES",
    "SCOPE_GLOBAL",
    "STATUS_CANDIDATE",
    "STATUS_PROMOTED",
    "STATUS_REJECTED",
    "STATUS_SUPERSEDED",
    "ExperienceLessonRow",
]
