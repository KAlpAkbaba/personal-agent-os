"""Briefing: the unread-mail clause (B45 req 278, 362).

Revision ID: 0057_briefing_include_mail
Revises: 0056_scenes_exports
Create Date: 2026-09-15

Chains from ``0056_scenes_exports`` -- the chain tip once B44 is applied, confirmed against
every ``down_revision`` in this directory before writing this file.

**Why a column.** The mail clause is the owner's to switch off like every other clause of
the morning briefing (`include_weather`, `include_calendar`, ...); the default is on, so an
existing preferences row gains the clause the moment an account is configured.

**Expand-only and reversible.**
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057_briefing_include_mail"
down_revision: str | None = "0056_scenes_exports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "briefing_preferences",
        sa.Column("include_mail", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("briefing_preferences", "include_mail")
