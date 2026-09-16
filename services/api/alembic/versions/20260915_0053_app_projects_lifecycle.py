"""The generated application's lifecycle record (B41 req 440-452).

Revision ID: 0053_app_projects_lifecycle
Revises: 0052_app_projects_composed
Create Date: 2026-09-15

Chains from ``0052_app_projects_composed`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why one column.** The UI verification, the persistence check, the log read-back, the
release artifact, the launch lineage and the modification record are all facts about one
project version, read together and never queried apart: one nullable JSON column.

**Expand-only and reversible.**
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_app_projects_lifecycle"
down_revision: str | None = "0052_app_projects_composed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_projects", sa.Column("lifecycle_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("app_projects", "lifecycle_json")
