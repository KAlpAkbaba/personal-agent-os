"""Durable notifications: the row this system owns (B11 req 367/377/378/379/380/389).

Revision ID: 0044_notifications
Revises: 0043_executive_steps_failed
Create Date: 2026-09-13

Chains from ``0043_executive_steps_failed`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file. Expand-only and reversible: one
new table, nothing existing is touched.

**Why.** The in-app inbox read the FAKE push provider's in-memory ``deque``. A restart lost
every notification the owner had not seen, and in production there was nothing to read at all
- a real transport hands the message to Apple or Google and keeps no log, which is exactly
why the fake's log was standing in for one.

The notification stops being something a transport remembers. The row is written before
anything is attempted, every channel updates the same row, and the inbox reads the row. The
fallback ladder becomes a question about one row - which rung has not been tried - instead of
a chain of separate queues that can each lose their own copy.

The inbox is the FLOOR of the ladder rather than a rung on it: recording the notification is
putting it in the inbox, and toast/sound/push are attempts to reach the owner sooner. So the
worst a broken transport can do is leave them to find it, which is a degradation, not a loss.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0044_notifications"
down_revision: str | None = "0043_executive_steps_failed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONColumn = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="normal"),
        sa.Column("group_key", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("data_json", JSONColumn, nullable=False, server_default="{}"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_via", sa.String(length=16), nullable=True),
        sa.Column("attempted_json", JSONColumn, nullable=False, server_default="[]"),
        sa.Column("deferred_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ladder_exhausted", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    op.create_index("ix_notifications_kind", "notifications", ["kind"])
    op.create_index("ix_notifications_priority", "notifications", ["priority"])
    op.create_index("ix_notifications_group_key", "notifications", ["group_key"])
    op.create_index("ix_notifications_unread", "notifications", ["read_at", "created_at"])


def downgrade() -> None:
    for name in (
        "ix_notifications_unread",
        "ix_notifications_group_key",
        "ix_notifications_priority",
        "ix_notifications_kind",
        "ix_notifications_created_at",
    ):
        op.drop_index(name, table_name="notifications")
    op.drop_table("notifications")
