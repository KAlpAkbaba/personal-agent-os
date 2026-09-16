"""Artifact version provenance (B42 req 405-408).

Revision ID: 0054_artifact_provenance
Revises: 0053_app_projects_lifecycle
Create Date: 2026-09-15

Chains from ``0053_app_projects_lifecycle`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why one column.** Who asked for a version, the Python and library versions that
rendered it, and the version or artifact it was derived from are facts about ONE version,
recorded when it is made and never rewritten; ``source_manifest_json`` (M13) already
held "what it is made of" and is now written by the factory.

**Expand-only and reversible.**
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054_artifact_provenance"
down_revision: str | None = "0053_app_projects_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("artifact_versions", sa.Column("provenance_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("artifact_versions", "provenance_json")
