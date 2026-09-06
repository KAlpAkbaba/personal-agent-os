"""M18.2 research focus: which research the owner is pointing at (ADR-0076).

Revision ID: 0022_research_focus
Revises: 0021_wake_alarms
Create Date: 2026-09-07

Reversible. ORM models: app/research/models.py::ResearchFocusRow, ::ResearchOwnerStateRow.

``research_focus`` — append-only. One row per time the focus moved, with WHY it moved
(``source_of_focus``, a closed vocabulary spelled out in the CHECK constraint so a source
outside ``app.research.models.FOCUS_SOURCES`` cannot reach the database even from a path
that skips the Python-side guard). The current focus is the most recent row; "bir önceki"
is the most recent row naming a different job; an ordinal counts distinct jobs down that
same list. Nothing is ever updated in place: recency IS the ordering, and an UPDATE would
erase exactly the history the owner's "bir öncekini anlat" reads.

``research_focus.research_job_id`` is a task id, and it is the identity. The defect this
migration exists for (owner record, 2026-09-06 evening) had two completed runs sharing one
title — "identity is ID-based, never title-based" is the owner's own directive, and a
foreign key onto ``tasks.id`` is the schema saying so.

``research_owner_state`` — one row, primary key ``'owner'`` (the same fixed-key shape
migration 0021 gave ``ambient_policy``, for the same reason: this is the single owner's
state, not a tenant's). It carries the clarification the server is currently waiting on,
as JSON, because the shape is a question plus a bounded candidate list read back whole and
never queried by field. Its TTL is applied on read (app.research.focus), so an unanswered
question expires without a sweeper.

Portable types throughout (generic ``Uuid``, ``JSON`` with a ``JSONB`` variant on
Postgres): the service layer's unit tests build this schema from the model metadata on
SQLite, and a Postgres-only column type would force a second, drifting definition.

Verified while authoring it by applying THIS revision, in both directions, against a
throwaway SQLite database: ``PAGENTOS_DATABASE_URL=sqlite:///... alembic stamp
0021_wake_alarms`` (the chain BELOW this point is not SQLite-portable — 0001 issues
``CREATE EXTENSION IF NOT EXISTS vector``), then ``alembic upgrade head`` and
``alembic downgrade -1``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_research_focus"
down_revision: str | None = "0021_wake_alarms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FOCUS_SOURCES = (
    "research_just_completed",
    "result_just_spoken",
    "owner_selected_in_ui",
    "owner_selected_by_voice",
    "followup_reference",
)


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "research_focus",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False, server_default="owner"),
        sa.Column("research_job_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=True),
        sa.Column("source_of_focus", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["research_job_id"],
            ["tasks.id"],
            name="fk_research_focus_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["artifacts.id"],
            name="fk_research_focus_artifact",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            _in_list("source_of_focus", _FOCUS_SOURCES), name="ck_research_focus_source"
        ),
    )
    op.create_index("ix_research_focus_research_job_id", "research_focus", ["research_job_id"])
    op.create_index(
        "ix_research_focus_owner_selected_at", "research_focus", ["owner_id", "selected_at"]
    )

    op.create_table(
        "research_owner_state",
        sa.Column("owner_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("pending_clarification_json", json_type, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("research_owner_state")
    op.drop_index("ix_research_focus_owner_selected_at", table_name="research_focus")
    op.drop_index("ix_research_focus_research_job_id", table_name="research_focus")
    op.drop_table("research_focus")
