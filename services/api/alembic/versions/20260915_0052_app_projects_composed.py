"""The composed App Factory's records on the project row (B40 req 423-439).

Revision ID: 0052_app_projects_composed
Revises: 0051_operator_missions
Create Date: 2026-09-15

Chains from ``0051_operator_missions`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why five columns.** A composed application has a plan the owner may read back
(``plan_json``), the lint / security / generation reports that gated it
(``reports_json``), a browser oracle of its own (``oracle_json`` - the templates' oracles
are code, a composed one is data), a fix record when its tests failed (``fix_json``), and
a version with the row it was fixed from (``version``, ``parent_id``) because the device
never rewrites a project in place.

**Expand-only and reversible.** Nullable JSON columns, a defaulted integer, a nullable
reference; the downgrade drops them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_app_projects_composed"
down_revision: str | None = "0051_operator_missions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_projects", sa.Column("plan_json", sa.JSON(), nullable=True))
    op.add_column("app_projects", sa.Column("reports_json", sa.JSON(), nullable=True))
    op.add_column("app_projects", sa.Column("oracle_json", sa.JSON(), nullable=True))
    op.add_column("app_projects", sa.Column("fix_json", sa.JSON(), nullable=True))
    op.add_column(
        "app_projects", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("app_projects", sa.Column("parent_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("app_projects", "parent_id")
    op.drop_column("app_projects", "version")
    op.drop_column("app_projects", "fix_json")
    op.drop_column("app_projects", "oracle_json")
    op.drop_column("app_projects", "reports_json")
    op.drop_column("app_projects", "plan_json")
