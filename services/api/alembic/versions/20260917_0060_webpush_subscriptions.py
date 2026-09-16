"""Web Push subscriptions: the push rung's storage (B11 req 372).

Revision ID: 0060_webpush_subscriptions
Revises: 0059_ambient_camera_mode
Create Date: 2026-09-17

Chains from ``0059_ambient_camera_mode`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file. Expand-only and
reversible: one new table, nothing existing is touched.

ORM model: app/webpush/models.py::PushSubscriptionRow.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0060_webpush_subscriptions"
down_revision: str | None = "0059_ambient_camera_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "webpush_subscriptions"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=128), nullable=False),
        sa.Column("auth", sa.String(length=48), nullable=False),
        sa.Column("user_agent", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_reason", sa.String(length=32), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
    )
    # Text columns cannot carry a plain UNIQUE constraint on every backend at arbitrary
    # length (Postgres can; this keeps parity with how the rest of this schema avoids
    # relying on backend-specific index behaviour) - a unique INDEX is the same
    # constraint app.webpush.service.subscribe relies on (re-subscribing the same
    # browser updates the existing row) and is supported identically everywhere this
    # schema runs (Postgres in production, SQLite in unit tests).
    op.create_index("ux_webpush_subscriptions_endpoint", _TABLE, ["endpoint"], unique=True)
    op.create_index("ix_webpush_subscriptions_created_at", _TABLE, ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_webpush_subscriptions_created_at", table_name=_TABLE)
    op.drop_index("ux_webpush_subscriptions_endpoint", table_name=_TABLE)
    op.drop_table(_TABLE)
