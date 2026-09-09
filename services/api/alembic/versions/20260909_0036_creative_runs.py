"""M27 Creative Tools Operator: the creative_runs table
(docs/M27_CREATIVE_TOOLS_SPEC.md §2-§4, ADR-0093).

Revision ID: 0036_creative_runs
Revises: 0035_news_mode
Create Date: 2026-09-09

Chains from ``0035_news_mode`` — the true chain tip (confirmed against every
``down_revision`` in this directory before writing this file). Reversible,
expand-only (a new table). ORM model: app/creative/models.py::CreativeRunRow.
Portable types throughout (generic ``Uuid``, ``JSON`` with a ``JSONB`` variant on
Postgres), the same discipline migration 0032 (``scenes``) documents.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_creative_runs"
down_revision: str | None = "0035_news_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "creative_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("tool", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="planned"),
        sa.Column("plan_json", json_type, nullable=False, server_default="{}"),
        sa.Column("inspection_json", json_type, nullable=True),
        sa.Column("compare_json", json_type, nullable=True),
        sa.Column("round_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rounds_json", json_type, nullable=False, server_default="[]"),
        sa.Column("output_object_key", sa.String(length=512), nullable=True),
        sa.Column("output_name", sa.String(length=200), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("output_bytes", sa.Integer(), nullable=True),
        sa.Column("error_class", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_creative_runs_state", "creative_runs", ["state"])
    op.create_index("ix_creative_runs_created_at", "creative_runs", ["created_at"])
    op.create_index("ix_creative_runs_tool_name", "creative_runs", ["tool", "name"])


def downgrade() -> None:
    op.drop_index("ix_creative_runs_tool_name", table_name="creative_runs")
    op.drop_index("ix_creative_runs_created_at", table_name="creative_runs")
    op.drop_index("ix_creative_runs_state", table_name="creative_runs")
    op.drop_table("creative_runs")
