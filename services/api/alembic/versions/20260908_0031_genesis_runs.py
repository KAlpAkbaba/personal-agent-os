"""M24 Capability Genesis: the genesis_runs table (docs/M24_CAPABILITY_GENESIS_SPEC.md
§5, ADR-0087).

Revision ID: 0031_genesis_runs
Revises: 0030_app_projects
Create Date: 2026-09-08

Chains from ``0030_app_projects`` — the true chain tip (confirmed against every
``down_revision`` in this directory before writing this file). Reversible,
expand-only (a new table). ORM model: app/genesis/models.py::GenesisRun.
Portable types throughout (generic ``Uuid``, ``JSON`` with a ``JSONB`` variant
on Postgres), the same discipline migration 0030 (``app_projects``) documents.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031_genesis_runs"
down_revision: str | None = "0030_app_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GENESIS_STATES = (
    "capability_missing",
    "researching",
    "designing",
    "building",
    "testing",
    "classifying",
    "awaiting_approval",
    "rolling_out",
    "registering",
    "available",
    "used",
    "verified",
    "failed",
    "cancelled",
)
AUTHORITY_CLASSES = ("read_only", "mutating_authorized_asset", "mutating_unauthorized")
SIDE_EFFECT_CLASSES = ("none", "read", "mutate_external")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "genesis_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("capability_id", sa.String(length=128), nullable=False),
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("gap_id", sa.Uuid(), nullable=True),
        sa.Column(
            "state", sa.String(length=24), nullable=False, server_default="capability_missing"
        ),
        sa.Column("interface_json", json_type, nullable=False, server_default="{}"),
        sa.Column("authority_class", sa.String(length=32), nullable=True),
        sa.Column("side_effect_class", sa.String(length=24), nullable=True),
        sa.Column(
            "approval_required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("approval_ref", sa.String(length=128), nullable=True),
        sa.Column("skill_version_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_json", json_type, nullable=False, server_default="{}"),
        sa.Column("error_class", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_in_list("state", GENESIS_STATES), name="ck_genesis_runs_state"),
        sa.CheckConstraint(
            _in_list("authority_class", AUTHORITY_CLASSES) + " OR authority_class IS NULL",
            name="ck_genesis_runs_authority_class",
        ),
        sa.CheckConstraint(
            _in_list("side_effect_class", SIDE_EFFECT_CLASSES) + " OR side_effect_class IS NULL",
            name="ck_genesis_runs_side_effect_class",
        ),
    )
    op.create_index("ix_genesis_runs_capability_id", "genesis_runs", ["capability_id"])
    op.create_index("ix_genesis_runs_gap_id", "genesis_runs", ["gap_id"])
    op.create_index(
        "ix_genesis_runs_capability_state", "genesis_runs", ["capability_id", "state"]
    )
    op.create_index("ix_genesis_runs_created_at", "genesis_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_genesis_runs_created_at", table_name="genesis_runs")
    op.drop_index("ix_genesis_runs_capability_state", table_name="genesis_runs")
    op.drop_index("ix_genesis_runs_gap_id", table_name="genesis_runs")
    op.drop_index("ix_genesis_runs_capability_id", table_name="genesis_runs")
    op.drop_table("genesis_runs")
