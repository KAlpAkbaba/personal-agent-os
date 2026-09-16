"""The self-development queue (B35 req 581, 583, 609, 615, 620, 621).

Revision ID: 0048_selfdev_defects
Revises: 0047_file_mutations
Create Date: 2026-09-15

Chains from ``0047_file_mutations`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why a table.** A defect the owner assigns by voice, a feature the owner asks for, an
opportunity the bridge turns into work, a CI failure the fix loop turns into a follow-up:
each is a row here, with the run's outcome and the owner's decision on the same row. The
queue IS the scheduler's input, the parallel bound's count and the Cockpit's list.

**Expand-only and reversible.** One new table; the downgrade drops it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_selfdev_defects"
down_revision: str | None = "0047_file_mutations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "selfdev_defects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("session_id", sa.String(200), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column(
            "scope_json",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("failing_test", sa.String(400), nullable=True),
        sa.Column("opportunity_id", sa.String(64), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("promotion_class", sa.String(40), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("claimed_by", sa.String(120), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", sa.String(120), nullable=True),
        sa.Column(
            "run_json",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("candidate_sha", sa.String(64), nullable=True),
        sa.Column("branch", sa.String(200), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("error_class", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_selfdev_defects_opportunity_id", "selfdev_defects", ["opportunity_id"])
    op.create_index("ix_selfdev_defects_state", "selfdev_defects", ["state"])
    op.create_index("ix_selfdev_defects_state_created", "selfdev_defects", ["state", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_selfdev_defects_state_created", table_name="selfdev_defects")
    op.drop_index("ix_selfdev_defects_state", table_name="selfdev_defects")
    op.drop_index("ix_selfdev_defects_opportunity_id", table_name="selfdev_defects")
    op.drop_table("selfdev_defects")
