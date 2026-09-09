"""Latest News Mode: news_sources + news_resolutions + news_playback_contexts
(docs/M27_LATEST_NEWS_MODE_SPEC.md §1, §2, §5).

Revision ID: 0035_news_mode
Revises: 0034_location_weather_briefing
Create Date: 2026-09-08

Chains from ``0034_location_weather_briefing`` — the true chain tip (confirmed against every
``down_revision`` in this directory before writing this file). Reversible, expand-only
(three new tables). ORM models: app/news/models.py::NewsSourceRow, NewsResolutionRow,
NewsPlaybackContextRow. Portable types throughout (generic ``Uuid``, ``JSON`` with a
``JSONB`` variant on Postgres), the same discipline migration 0033 documents.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_news_mode"
down_revision: str | None = "0034_location_weather_briefing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "news_sources",
        sa.Column("news_source_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False, server_default="youtube"),
        sa.Column("channel_input", sa.String(length=500), nullable=True),
        sa.Column("channel_id", sa.String(length=64), nullable=True),
        sa.Column(
            "identity_status", sa.String(length=24), nullable=False, server_default="needs_identity"
        ),
        sa.Column("identity_resolved_by", sa.String(length=32), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "content_type", sa.String(length=32), nullable=False, server_default="latest_any_news"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "content_type IN ('latest_any_news', 'latest_full_broadcast', 'latest_main_news')",
            name="ck_news_sources_content",
        ),
        sa.CheckConstraint("provider IN ('youtube')", name="ck_news_sources_provider"),
        sa.CheckConstraint(
            "identity_status IN ('resolved', 'needs_identity')", name="ck_news_sources_identity"
        ),
    )
    op.create_index("ix_news_sources_enabled_priority", "news_sources", ["enabled", "priority"])

    op.create_table(
        "news_resolutions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "news_source_id",
            sa.String(length=64),
            sa.ForeignKey("news_sources.news_source_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel_id", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("answered_by", sa.String(length=24), nullable=False),
        sa.Column("candidates_json", json_type, nullable=False, server_default="[]"),
        sa.Column("selected_video_id", sa.String(length=64), nullable=True),
        sa.Column("selected_title", sa.String(length=500), nullable=True),
        sa.Column("selected_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("selected_url", sa.String(length=2048), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("ambiguous", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "answered_by IN ('channel_feed', 'videos_listing', 'dom_extraction', 'fixture')",
            name="ck_news_resolutions_answered_by",
        ),
    )
    op.create_index("ix_news_resolutions_source_id", "news_resolutions", ["news_source_id"])
    op.create_index(
        "ix_news_resolutions_source_created", "news_resolutions", ["news_source_id", "created_at"]
    )

    op.create_table(
        "news_playback_contexts",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "news_source_id",
            sa.String(length=64),
            sa.ForeignKey("news_sources.news_source_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("video_id", sa.String(length=64), nullable=False),
        sa.Column("channel_id", sa.String(length=64), nullable=False),
        sa.Column("video_title", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="opening"),
        sa.Column("receipt_json", json_type, nullable=False, server_default="{}"),
        sa.Column("error_class", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('opening', 'playing', 'unverified', 'failed', 'closed')",
            name="ck_news_playback_contexts_status",
        ),
    )
    op.create_index(
        "ix_news_playback_contexts_source_id", "news_playback_contexts", ["news_source_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_news_playback_contexts_source_id", table_name="news_playback_contexts")
    op.drop_table("news_playback_contexts")
    op.drop_index("ix_news_resolutions_source_created", table_name="news_resolutions")
    op.drop_index("ix_news_resolutions_source_id", table_name="news_resolutions")
    op.drop_table("news_resolutions")
    op.drop_index("ix_news_sources_enabled_priority", table_name="news_sources")
    op.drop_table("news_sources")
