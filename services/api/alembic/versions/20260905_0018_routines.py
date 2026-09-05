"""M18 Routine Engine: routines, routine_firings.

Revision ID: 0018_routines
Revises: 0017_cognitive_ts_defaults
Create Date: 2026-09-05

Reversible. ORM models: app/routines/models.py.

Every NOT NULL timestamp column the model declares ``server_default=func.now()`` for is
given ``server_default=sa.text("now()")`` HERE, in the same CREATE TABLE — migration 0017
exists because 0016 did not do this for M17's tables, and the unit suite could not see the
drift because it builds its tables from the model metadata, not from this file (see
``tests/unit/test_migration_model_agreement.py``). Two tables, one migration, because they
ship as one milestone: ``routine_firings.routine_id`` cascades from ``routines.routine_id``,
so an operator does not have to sequence them, and the downgrade drops both.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_routines"
down_revision: str | None = "0017_cognitive_ts_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROUTINE_STATUSES = ("armed", "completed", "cancelled")
_TRIGGER_KINDS = ("at", "schedule", "presence")
_FIRING_STATUSES = ("triggered", "skipped")


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "routines",
        sa.Column("routine_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("trigger_kind", sa.String(length=16), nullable=False),
        sa.Column("trigger_json", json_type, nullable=False),
        sa.Column("conditions_json", json_type, nullable=False),
        sa.Column("actions_json", json_type, nullable=False),
        sa.Column("last_presence_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("armed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(length=500), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=256), nullable=False),
        sa.Column("detail_json", json_type, nullable=False),
        sa.CheckConstraint(_in_list("status", _ROUTINE_STATUSES), name="ck_routines_status"),
        sa.CheckConstraint(
            _in_list("trigger_kind", _TRIGGER_KINDS), name="ck_routines_trigger_kind"
        ),
        sa.UniqueConstraint("source", "source_ref", name="uq_routines_source_ref"),
    )
    op.create_index("ix_routines_status", "routines", ["status"], unique=False)
    op.create_index("ix_routines_trigger_kind", "routines", ["trigger_kind"], unique=False)

    op.create_table(
        "routine_firings",
        sa.Column("firing_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "routine_id",
            sa.Uuid(),
            sa.ForeignKey("routines.routine_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("occurrence_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("conditions_result", json_type, nullable=False),
        sa.Column("actions_snapshot", json_type, nullable=False),
        sa.Column("skip_reason", sa.String(length=500), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(_in_list("status", _FIRING_STATUSES), name="ck_routine_firings_status"),
        sa.UniqueConstraint(
            "routine_id", "occurrence_key", name="uq_routine_firings_routine_occurrence"
        ),
    )
    op.create_index(
        "ix_routine_firings_routine_id", "routine_firings", ["routine_id"], unique=False
    )
    op.create_index(
        "ix_routine_firings_occurred_at", "routine_firings", ["occurred_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_routine_firings_occurred_at", table_name="routine_firings")
    op.drop_index("ix_routine_firings_routine_id", table_name="routine_firings")
    op.drop_table("routine_firings")
    op.drop_index("ix_routines_trigger_kind", table_name="routines")
    op.drop_index("ix_routines_status", table_name="routines")
    op.drop_table("routines")
