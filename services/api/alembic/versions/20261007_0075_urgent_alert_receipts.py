"""The alarm rung's receipts: one row per alarm Pushover accepted.

Revision ID: 0075_urgent_alert_receipts
Revises: 0074_conversation_one_open
Create Date: 2026-10-07

Written on ``0072_money_ledger`` (the head of the integration base this card started from).
Another card's migration that lands first re-points ``down_revision`` at merge - the
integration step's work, not this card's.

``urgent_alert_receipts``: the receipt id Pushover gave (unique), when it was sent and when the
ringing ends by itself, and - once the loop closes it - the outcome (seen | unseen |
cancelled), when he saw it and where (pushover: the phone's "acknowledge"; inbox: read on the
web or a toast). A partial index on the open rows: the loop reads only those, every minute.
No column holds a key, a user key or the alarm's text.

**Expand-only and reversible.** One new table; nothing existing is touched, so both colours
of a blue-green release serve beside it. The downgrade drops it.

ORM model: app/urgent_alert/models.py.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0075_urgent_alert_receipts"
down_revision: str | None = "0074_conversation_one_open"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "urgent_alert_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "notification_id",
            sa.Uuid(),
            sa.ForeignKey("notifications.id", name="fk_urgent_alert_receipts_notification"),
            nullable=False,
        ),
        sa.Column("receipt_id", sa.String(length=64), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.UniqueConstraint("receipt_id", name="uq_urgent_alert_receipts_receipt_id"),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('seen', 'unseen', 'cancelled')",
            name="ck_urgent_alert_receipts_outcome",
        ),
        sa.CheckConstraint(
            "source IN ('pushover', 'inbox')", name="ck_urgent_alert_receipts_source"
        ),
    )
    op.create_index(
        "ix_urgent_alert_receipts_open",
        "urgent_alert_receipts",
        ["closed_at"],
        postgresql_where=sa.text("closed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_urgent_alert_receipts_open", table_name="urgent_alert_receipts")
    op.drop_table("urgent_alert_receipts")
