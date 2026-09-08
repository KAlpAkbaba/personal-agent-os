"""M23 App Factory: the app_projects table (docs/M23_APP_FACTORY_SPEC.md §1, ADR-0086).

Revision ID: 0030_app_projects
Revises: 0028_artifact_validation
Create Date: 2026-09-08

Chains from ``0028_artifact_validation`` — the true chain tip at the time this migration
was written (``0028``'s own ``down_revision`` is ``0029_confirmation_binding``, despite
the filename ordering; ``alembic heads`` names the real tip, never the filename).

Reversible, expand-only (a new table). ORM model: app/appfactory/models.py::AppProjectRow.
Portable types throughout (generic ``Uuid``, ``JSON`` with a ``JSONB`` variant on
Postgres), the same discipline migration 0026 (``document_index``) already documents.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_app_projects"
down_revision: str | None = "0028_artifact_validation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "app_projects",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("template", sa.String(length=64), nullable=False),
        sa.Column("spec_json", json_type, nullable=False, server_default="{}"),
        sa.Column("device_id", sa.String(length=200), nullable=False),
        sa.Column("root_path", sa.String(length=1024), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="planned"),
        sa.Column("run_port", sa.Integer(), nullable=True),
        sa.Column("last_run_log_ref", sa.String(length=1024), nullable=True),
        sa.Column("test_report_json", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_app_projects_device_id", "app_projects", ["device_id"])
    op.create_index("ix_app_projects_state", "app_projects", ["state"])
    op.create_index("ix_app_projects_created_at", "app_projects", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_app_projects_created_at", table_name="app_projects")
    op.drop_index("ix_app_projects_state", table_name="app_projects")
    op.drop_index("ix_app_projects_device_id", table_name="app_projects")
    op.drop_table("app_projects")
