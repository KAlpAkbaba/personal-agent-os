"""Voice macros: a named, recorded sequence of tool calls (ADR-0196).

Revision ID: 0061_voice_macros
Revises: 0060_webpush_subscriptions
Create Date: 2026-09-21

Chains from ``0060_webpush_subscriptions`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file. Expand-only and
reversible: one new table, nothing existing is touched.

ORM model: app/macros/models.py::VoiceMacroRow.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0061_voice_macros"
down_revision: str | None = "0060_webpush_subscriptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "voice_macros"
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("macro_id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("name_key", sa.String(length=200), nullable=False),
        sa.Column("steps_json", _JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="voice"),
        sa.Column("detail_json", _JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.UniqueConstraint("name_key", name="uq_voice_macros_name_key"),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
