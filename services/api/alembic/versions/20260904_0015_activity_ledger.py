"""Activity Ledger (M16 track A).

Revision ID: 0015_activity_ledger
Revises: 0014_research_owner_verification
Create Date: 2026-09-04

Reversible. ORM models: app/ledger/models.py (ActivityEventRow,
PendingBriefingRow).

Why: M16_ACTIVITY_LEDGER_SPEC.md §1 needs one durable, structured,
append-only stream for every current and future subsystem so the owner can
ask "son yaptıklarını anlat" and get an answer built from durable evidence,
never from model memory or seeded/demo state; §4 needs a queue of briefings
that have been decided worth speaking but not yet delivered, so nothing is
lost when no voice session is active at the moment an event happens.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_activity_ledger"
down_revision: str | None = "0014_research_owner_verification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    # ------------------------------------------------------------ activity_events
    op.create_table(
        "activity_events",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("subsystem", sa.String(length=32), nullable=False),
        sa.Column("module", sa.String(length=128), nullable=True),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("result", sa.String(length=256), nullable=True),
        sa.Column("production_state", sa.String(length=24), nullable=False, server_default="n/a"),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("research_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("browser_session_id", sa.String(length=128), nullable=True),
        sa.Column("related_goal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("related_module_id", sa.String(length=128), nullable=True),
        sa.Column("evidence_refs", json_type, nullable=False, server_default="[]"),
        sa.Column("factual_summary", sa.Text(), nullable=False),
        sa.Column("detail_json", json_type, nullable=False, server_default="{}"),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=256), nullable=False),
        sa.UniqueConstraint("source", "source_ref", name="uq_activity_events_source_ref"),
    )
    op.create_index("ix_activity_events_occurred_at", "activity_events", ["occurred_at"])
    op.create_index("ix_activity_events_subsystem", "activity_events", ["subsystem"])
    op.create_index("ix_activity_events_event_type", "activity_events", ["event_type"])
    op.create_index("ix_activity_events_research_job_id", "activity_events", ["research_job_id"])
    op.create_index("ix_activity_events_trace_id", "activity_events", ["trace_id"])

    # ----------------------------------------------------------- pending_briefings
    op.create_table(
        "pending_briefings",
        sa.Column(
            "briefing_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("policy", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("speech", sa.Text(), nullable=False),
        sa.Column("event_ids", json_type, nullable=False, server_default="[]"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_via", sa.String(length=32), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pending_briefings_delivered_at", "pending_briefings", ["delivered_at"])


def downgrade() -> None:
    op.drop_index("ix_pending_briefings_delivered_at", table_name="pending_briefings")
    op.drop_table("pending_briefings")
    op.drop_index("ix_activity_events_trace_id", table_name="activity_events")
    op.drop_index("ix_activity_events_research_job_id", table_name="activity_events")
    op.drop_index("ix_activity_events_event_type", table_name="activity_events")
    op.drop_index("ix_activity_events_subsystem", table_name="activity_events")
    op.drop_index("ix_activity_events_occurred_at", table_name="activity_events")
    op.drop_table("activity_events")
