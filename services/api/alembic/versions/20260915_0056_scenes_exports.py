"""Scene exports (B44 req 527).

Revision ID: 0056_scenes_exports
Revises: 0055_creative_history
Create Date: 2026-09-15

Chains from ``0055_creative_history`` -- the chain tip once B43 is applied, confirmed
against every ``down_revision`` in this directory before writing this file.

**Why one column.** An exported GLB/FBX stays on the owner's disk (a scene file can be
megabytes and the device connection's frame is one); what the Cloud Core keeps is the
device's proof of each file: format, relative path, size, sha256, verified.

**Expand-only and reversible.**
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0056_scenes_exports"
down_revision: str | None = "0055_creative_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scenes", sa.Column("exports_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("scenes", "exports_json")
