"""M25 3D Creation: the scenes table (docs/M25_CREATIVE_3D_SPEC.md §2, §4, ADR-0088).

Revision ID: 0032_scenes
Revises: 0031_genesis_runs
Create Date: 2026-09-08

Chains from ``0031_genesis_runs`` — the true chain tip (confirmed against every
``down_revision`` in this directory before writing this file). Reversible,
expand-only (a new table). ORM model: app/creative3d/models.py::SceneRow.
Portable types throughout (generic ``Uuid``, ``JSON`` with a ``JSONB`` variant on
Postgres), the same discipline migration 0031 (``genesis_runs``) documents.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_scenes"
down_revision: str | None = "0031_genesis_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "scenes",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("tool", sa.String(length=16), nullable=False),
        sa.Column("project", sa.String(length=64), nullable=False),
        sa.Column("scene", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("device_id", sa.String(length=200), nullable=False),
        sa.Column("root_path", sa.String(length=1024), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="planned"),
        sa.Column("plan_json", json_type, nullable=False, server_default="{}"),
        sa.Column("inspection_json", json_type, nullable=True),
        sa.Column("compare_json", json_type, nullable=True),
        sa.Column("render_object_key", sa.String(length=512), nullable=True),
        sa.Column("render_sha256", sa.String(length=64), nullable=True),
        sa.Column("render_bytes", sa.Integer(), nullable=True),
        sa.Column("error_class", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scenes_device_id", "scenes", ["device_id"])
    op.create_index("ix_scenes_state", "scenes", ["state"])
    op.create_index("ix_scenes_created_at", "scenes", ["created_at"])
    op.create_index("ix_scenes_project_scene", "scenes", ["project", "scene"])


def downgrade() -> None:
    op.drop_index("ix_scenes_project_scene", table_name="scenes")
    op.drop_index("ix_scenes_created_at", table_name="scenes")
    op.drop_index("ix_scenes_state", table_name="scenes")
    op.drop_index("ix_scenes_device_id", table_name="scenes")
    op.drop_table("scenes")
