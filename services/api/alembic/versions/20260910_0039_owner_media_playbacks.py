"""Owner-requested media playback: ``owner_media_playbacks`` (ADR-0112).

Revision ID: 0039_owner_media_playbacks
Revises: 0038_voice_session_no_expiry
Create Date: 2026-09-10

Chains from ``0038_voice_session_no_expiry`` -- the true chain tip, confirmed against every
``down_revision`` in this directory before writing this file. Additive and reversible: one
new table, nothing existing is touched.

One row per thing the owner asked to be played. The table exists so "durdur" can name the
exact browser session that was opened (``owner-media-<id>``, mirroring ``news-<id>`` and
``alarm-<id>``) rather than "the media session", of which an alarm's and a news video's may
be open at the same time -- a feature that can start something it cannot stop is not
finished.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0039_owner_media_playbacks"
down_revision: str | Sequence[str] | None = "0038_voice_session_no_expiry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = ("opening", "playing", "unverified", "failed", "closed")


def upgrade() -> None:
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    statuses = ", ".join(f"'{s}'" for s in _STATUSES)
    op.create_table(
        "owner_media_playbacks",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("query", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("video_id", sa.String(length=64), nullable=True),
        sa.Column("video_title", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("session_id", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="opening"),
        sa.Column("error_class", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("receipt_json", json_type, nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(f"status IN ({statuses})", name="ck_owner_media_playbacks_status"),
    )
    op.create_index(
        "ix_owner_media_playbacks_status", "owner_media_playbacks", ["status"], unique=False
    )
    op.create_index(
        "ix_owner_media_playbacks_created_at", "owner_media_playbacks", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_owner_media_playbacks_created_at", table_name="owner_media_playbacks")
    op.drop_index("ix_owner_media_playbacks_status", table_name="owner_media_playbacks")
    op.drop_table("owner_media_playbacks")
