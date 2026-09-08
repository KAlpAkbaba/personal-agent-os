"""M26 Executive Autonomy: executive_runs + executive_steps (docs/
M26_EXECUTIVE_AUTONOMY_SPEC.md §1, §3, ADR-0089).

Revision ID: 0033_executive_runs
Revises: 0032_scenes
Create Date: 2026-09-08

Chains from ``0032_scenes`` — the true chain tip (confirmed against every
``down_revision`` in this directory before writing this file). Reversible,
expand-only (two new tables). ORM models: app/executive/models.py::ExecutiveRunRow,
ExecutiveStepRow. Portable types throughout (generic ``Uuid``, ``JSON`` with a
``JSONB`` variant on Postgres), the same discipline migration 0032 documents.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_executive_runs"
down_revision: str | None = "0032_scenes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "executive_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("goal", sa.String(length=2000), nullable=False),
        sa.Column("graph_json", json_type, nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="planned"),
        sa.Column("current_step", sa.String(length=8), nullable=True),
        sa.Column("steps_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("steps_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("synthesis_text", sa.Text(), nullable=True),
        sa.Column("partial_reasons_json", json_type, nullable=True),
        sa.Column("error_class", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("workflow_id", sa.String(length=200), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="voice"),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_executive_runs_state", "executive_runs", ["state"])
    op.create_index("ix_executive_runs_created_at", "executive_runs", ["created_at"])

    op.create_table(
        "executive_steps",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("executive_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_id", sa.String(length=8), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("inputs_json", json_type, nullable=False, server_default="{}"),
        sa.Column("precondition_json", json_type, nullable=False, server_default="{}"),
        sa.Column("postcondition_json", json_type, nullable=False),
        sa.Column("retry_json", json_type, nullable=False, server_default="{}"),
        sa.Column("timeout_s", sa.Integer(), nullable=False),
        sa.Column("risk_class", sa.String(length=24), nullable=False),
        sa.Column("compensation", sa.String(length=24), nullable=False, server_default="none"),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_json", json_type, nullable=True),
        sa.Column("error_class", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_unique_constraint(
        "uq_executive_steps_run_step", "executive_steps", ["run_id", "step_id"]
    )
    op.create_index("ix_executive_steps_run_id", "executive_steps", ["run_id"])
    op.create_index("ix_executive_steps_state", "executive_steps", ["state"])


def downgrade() -> None:
    op.drop_index("ix_executive_steps_state", table_name="executive_steps")
    op.drop_index("ix_executive_steps_run_id", table_name="executive_steps")
    op.drop_constraint("uq_executive_steps_run_step", "executive_steps", type_="unique")
    op.drop_table("executive_steps")
    op.drop_index("ix_executive_runs_created_at", table_name="executive_runs")
    op.drop_index("ix_executive_runs_state", table_name="executive_runs")
    op.drop_table("executive_runs")
