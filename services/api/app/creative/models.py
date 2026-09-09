"""``creative_runs``: the M27 Creative Tools Operator's own durable record
(docs/M27_CREATIVE_TOOLS_SPEC.md §2-§4, ADR-0093). Expand-only migration:
``alembic/versions/20260909_0036_creative_runs.py``.

One row per creative edit the assistant is driving, mirroring
``app.creative3d.models.SceneRow``'s own shape: ``plan_json`` is the MOST RECENT
``CreativePlan`` applied (canonical JSON, never re-derived from prose); ``inspection_json``
is the executor's own read-back (never the plan restated); ``compare_json`` is the last
``app.creative.compare.compare()`` result — what the row believes is true is only ever
what was actually measured (the M25 security lesson, restated here by name).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy import Uuid as SAUuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

STATE_PLANNED = "planned"
STATE_APPLIED = "applied"
STATE_FAILED = "failed"
#: The executor did the work and the INDEPENDENT comparison disagrees with the plan.
#: Not a failure of the run and not a success either (the same distinction
#: ``app.creative3d.models.STATE_MISMATCH`` documents for M25).
STATE_MISMATCH = "mismatch"
#: The named tool is not installed/licensed on this machine (ADR-0093 decision 3) —
#: never confused with a genuine ``failed`` (a plan/executor problem).
STATE_DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
#: The comparison ran and every checked constraint agreed.
STATE_VERIFIED = "verified"
#: The comparison ran, the plan asked for nothing checkable, and the run itself
#: succeeded anyway (an empty canvas, say) — never "verified" over nothing, and never
#: a disagreement either (``app.creative3d.models.wire_step``'s own ``unverified``
#: case, restated here for the same reason).
STATE_UNVERIFIED = "unverified"

CREATIVE_STATES: tuple[str, ...] = (
    STATE_PLANNED,
    STATE_APPLIED,
    STATE_VERIFIED,
    STATE_UNVERIFIED,
    STATE_MISMATCH,
    STATE_DEPENDENCY_UNAVAILABLE,
    STATE_FAILED,
)

#: Self-correction is bounded at 3 rounds (ADR-0093 decision 7) — enforced here as a
#: DB-level fact the service can check without re-deriving it from the ledger.
MAX_SELF_CORRECTION_ROUNDS = 3


class CreativeRunRow(Base):
    """One creative edit the assistant is driving (spec §2's ``CreativePlan`` identity:
    ``tool``/``name``)."""

    __tablename__ = "creative_runs"
    __table_args__ = (
        Index("ix_creative_runs_state", "state"),
        Index("ix_creative_runs_created_at", "created_at"),
        Index("ix_creative_runs_tool_name", "tool", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    tool: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default=STATE_PLANNED)
    #: The MOST RECENT ``CreativePlan`` applied (canonical JSON, module docstring).
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    #: The executor's own read-back (``app.creative.execute.ExecutionResult.inspection()``)
    #: — never the plan restated (module docstring).
    inspection_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    #: The last ``app.creative.compare.compare()`` result.
    compare_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    #: How many self-correction rounds have run so far — bounded at
    #: :data:`MAX_SELF_CORRECTION_ROUNDS` (ADR-0093 decision 7); every round's own
    #: metrics live in ``rounds_json``, never overwritten.
    round_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rounds_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONColumn, nullable=False, default=list
    )
    output_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    output_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "CREATIVE_STATES",
    "MAX_SELF_CORRECTION_ROUNDS",
    "STATE_APPLIED",
    "STATE_DEPENDENCY_UNAVAILABLE",
    "STATE_FAILED",
    "STATE_MISMATCH",
    "STATE_PLANNED",
    "STATE_UNVERIFIED",
    "STATE_VERIFIED",
    "CreativeRunRow",
]
