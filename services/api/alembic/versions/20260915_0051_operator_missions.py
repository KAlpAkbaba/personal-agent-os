"""The durable operator mission (B39 req 128).

Revision ID: 0051_operator_missions
Revises: 0050_executive_approvals
Create Date: 2026-09-15

Chains from ``0050_executive_approvals`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why a table.** A mission is a multi-step desktop task that outlives one tool call and one
worker: it is parked for the owner's yes (preview), paused between rounds, resumed after an
escalation, and its trail is the evidence of what the desktop looked like at every decision.
The row is the owner-facing truth; the Temporal workflow only drives it.

**Expand-only and reversible.** One new table; the downgrade drops it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051_operator_missions"
down_revision: str | None = "0050_executive_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operator_missions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("goal", sa.String(300), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("preview", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(16), nullable=False, server_default="voice"),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("error_class", sa.String(64), nullable=False, server_default=""),
        sa.Column("message", sa.String(600), nullable=False, server_default=""),
        sa.Column("mission_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("pause_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_operator_missions_status_created", "operator_missions", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_operator_missions_status_created", table_name="operator_missions")
    op.drop_table("operator_missions")
