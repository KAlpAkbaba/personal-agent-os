"""The genesis interface catalogue (B36 req 562, 563, 564).

Revision ID: 0049_genesis_catalogue
Revises: 0048_selfdev_defects
Create Date: 2026-09-15

Chains from ``0048_selfdev_defects`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why a table.** The spoken-name -> interface catalogue the voice router reads was an
in-memory list nothing registered into: empty in every production process, filled only by
tests. An interface the owner registers must survive a restart; the in-memory catalogue
is built from these rows at startup and after every registration.

**Expand-only and reversible.** One new table; the downgrade drops it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_genesis_catalogue"
down_revision: str | None = "0048_selfdev_defects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "genesis_catalogue",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(32), nullable=False, unique=True),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("target_phrases_json", sa.JSON(), nullable=False),
        sa.Column("operations_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("spec_digest", sa.String(64), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("genesis_catalogue")
