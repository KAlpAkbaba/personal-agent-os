"""Owner Location Context / Live Weather / Morning Briefing: location_context,
weather_query_evidence, briefing_preferences (docs/DECISIONS.md ADR-0091).

Revision ID: 0034_location_weather_briefing
Revises: 0033_executive_runs
Create Date: 2026-09-08

Chains from ``0033_executive_runs`` — the true chain tip in this worktree (confirmed
against every ``down_revision`` in this directory before writing this file). Reversible,
expand-only (three new tables, no existing table touched). ORM models:
app/location/models.py::LocationContextRow, app/weather/models.py::WeatherQueryEvidenceRow,
app/briefing/models.py::BriefingPreferencesRow. Portable types throughout (generic
``Uuid``, ``JSON`` with a ``JSONB`` variant on Postgres), the same discipline migration
0033 documents. ``briefing_preferences`` is a singleton row (``preferences_id`` primary
key, string), created lazily on first read (``app.briefing.service.get_preferences_row``)
rather than seeded here — the same choice migration history already makes for
``ambient_policy``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034_location_weather_briefing"
down_revision: str | None = "0033_executive_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "location_context",
        sa.Column("location_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("city", sa.String(length=200), nullable=True),
        sa.Column("region", sa.String(length=200), nullable=True),
        sa.Column("country", sa.String(length=200), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "permission_scope", sa.String(length=32), nullable=False, server_default="weather"
        ),
    )
    op.create_index(
        "ix_location_context_source_captured_at", "location_context", ["source", "captured_at"]
    )
    op.create_index(
        "ix_location_context_default_scope",
        "location_context",
        ["is_default", "permission_scope"],
    )

    op.create_table(
        "weather_query_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("queried_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location_json", json_type, nullable=False),
        sa.Column("location_source", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("temperature_c", sa.Float(), nullable=True),
        sa.Column("condition", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("precipitation_probability", sa.Float(), nullable=True),
        sa.Column("daily_high_c", sa.Float(), nullable=True),
        sa.Column("daily_low_c", sa.Float(), nullable=True),
        sa.Column("summary", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("session_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_weather_query_evidence_queried_at", "weather_query_evidence", ["queried_at"]
    )

    op.create_table(
        "briefing_preferences",
        sa.Column("preferences_id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column(
            "morning_briefing_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("include_weather", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("include_system_status", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("include_calendar", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("include_overnight_work", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("include_news_summary", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_open_news_video", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("briefing_preferences")
    op.drop_index("ix_weather_query_evidence_queried_at", table_name="weather_query_evidence")
    op.drop_table("weather_query_evidence")
    op.drop_index("ix_location_context_default_scope", table_name="location_context")
    op.drop_index("ix_location_context_source_captured_at", table_name="location_context")
    op.drop_table("location_context")
