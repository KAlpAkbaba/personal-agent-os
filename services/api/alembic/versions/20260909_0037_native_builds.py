"""M28 Native Application Factory: the native_builds table
(docs/M28_NATIVE_APP_FACTORY_SPEC.md §4, ADR-0095).

Revision ID: 0037_native_builds
Revises: 0036_creative_runs
Create Date: 2026-09-09

Chains from ``0036_creative_runs`` — the true chain tip, confirmed against every
``down_revision`` in this directory before writing this file. Reversible, expand-only (a
new table). ORM model: app/nativefactory/models.py::NativeBuildRow. Portable types
throughout (generic ``Uuid``, ``JSON`` with a ``JSONB`` variant on Postgres), the same
discipline migration 0032 (``scenes``) documents.

One row per (application, TARGET): "an EXE" and "an installer" are different artefacts
with different verdicts, and collapsing them into one row would mean one could not fail
without the other.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_native_builds"
down_revision: str | None = "0036_creative_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "native_builds",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("stack", sa.String(length=32), nullable=False),
        sa.Column("template", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=24), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("spec_json", json_type, nullable=False),
        sa.Column("artifact_json", json_type, nullable=True),
        sa.Column("verdict_json", json_type, nullable=True),
        sa.Column("tests_json", json_type, nullable=True),
        sa.Column("project_path", sa.String(length=500), nullable=True),
        sa.Column("artifact_path", sa.String(length=500), nullable=True),
        sa.Column("error_class", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("log_tail", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_native_builds_state", "native_builds", ["state"])
    op.create_index("ix_native_builds_slug", "native_builds", ["slug"])
    op.create_index("ix_native_builds_created_at", "native_builds", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_native_builds_created_at", table_name="native_builds")
    op.drop_index("ix_native_builds_slug", table_name="native_builds")
    op.drop_index("ix_native_builds_state", table_name="native_builds")
    op.drop_table("native_builds")
