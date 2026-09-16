"""Calendar: recurrence and reminders on a proposal; a synced, reminder-aware index (B46).

Revision ID: 0058_calendar_recurrence
Revises: 0057_briefing_include_mail
Create Date: 2026-09-15

Chains from ``0057_briefing_include_mail`` -- the chain tip once B45 is applied.

* ``calendar_proposals.rrule`` / ``reminder_minutes`` (req 356, 357): what the owner heard
  read back is what the writer sends - a recurrence rule and a VALARM are part of the
  proposal, never added at commit time.
* ``calendar_index.source`` / ``synced_at`` / ``reminded_at`` (req 358, 359, 361): the index
  is now also filled by the clock's two-week mirror (``source='sync'``), and a reminder is
  raised once per occurrence (``reminded_at``).

**Expand-only and reversible.**
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058_calendar_recurrence"
down_revision: str | None = "0057_briefing_include_mail"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("calendar_proposals", sa.Column("rrule", sa.String(500), nullable=True))
    op.add_column("calendar_proposals", sa.Column("reminder_minutes", sa.Integer(), nullable=True))
    op.add_column(
        "calendar_index",
        sa.Column("source", sa.String(16), nullable=False, server_default="read"),
    )
    op.add_column(
        "calendar_index", sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "calendar_index", sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("calendar_index", "reminded_at")
    op.drop_column("calendar_index", "synced_at")
    op.drop_column("calendar_index", "source")
    op.drop_column("calendar_proposals", "reminder_minutes")
    op.drop_column("calendar_proposals", "rrule")
