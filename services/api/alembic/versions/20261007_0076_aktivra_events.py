"""Aktivra's 'önemli' channel: one row per accepted event.

Revision ID: 0076_aktivra_events
Revises: 0075_urgent_alert_receipts
Create Date: 2026-10-07

Written on ``0075_urgent_alert_receipts`` (the head of the branch this card started from).
Another card's migration that lands first re-points ``down_revision`` at merge - the
integration step's work, not this card's.

``aktivra_events``: the event id Aktivra chose (unique - a retry finds its row and gets the
same notification back), the notification it made, the severity (important | info) and when
it arrived (indexed: the hourly cap and the 24-hour count read it; rows older than 30 days are
swept by app.aktivra.service.sweep_expired). ``notification_id`` is NULL only between the
claim and the notification's commit inside one request. No column holds the token, the title
or anything of the company's.

**Expand-only and reversible.** One new table; nothing existing is touched, so both colours
of a blue-green release serve beside it. The downgrade drops it.

ORM model: app/aktivra/models.py.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0076_aktivra_events"
down_revision: str | None = "0075_urgent_alert_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aktivra_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column(
            "notification_id",
            sa.Uuid(),
            sa.ForeignKey("notifications.id", name="fk_aktivra_events_notification"),
            nullable=True,
        ),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_aktivra_events_event_id"),
        sa.CheckConstraint("severity IN ('important', 'info')", name="ck_aktivra_events_severity"),
    )
    op.create_index("ix_aktivra_events_received_at", "aktivra_events", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_aktivra_events_received_at", table_name="aktivra_events")
    op.drop_table("aktivra_events")
