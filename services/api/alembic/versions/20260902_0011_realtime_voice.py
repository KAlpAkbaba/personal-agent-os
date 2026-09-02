"""Realtime voice sessions (M12).

Revision ID: 0011_realtime_voice
Revises: 0010_task_announcement
Create Date: 2026-09-02

Reversible. ORM models in app/voice/realtime_sessions/models.py must match.

Why: M12 makes the primary conversation a native speech-to-speech provider
session (ADR-0034). Audio never transits Cloud Core; what Cloud Core owns is
the session RECORD — who opened it (owner session + device), on which provider
and transport, its open plan, its narration cursor reference, a rolling
transcript summary for continuity across desktop/web/phone, and every tool
call the provider relayed through the client (idempotent on the provider's
``call_id``, so a retried relay never executes a tool twice).

What is deliberately absent: the provider credential (minted per media leg,
returned once, never stored), any audio, and the raw transcript. The audit of
each step goes to the shared ``audit_events`` table (category
``voice_realtime``) with ids and timings only.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_realtime_voice"
down_revision: str | None = "0010_task_announcement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATES = ("created", "active", "closed", "expired")
_TOOL_STATUSES = ("running", "succeeded", "failed")


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "realtime_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("client_kind", sa.String(length=16), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False, server_default="tr-TR"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="created"),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("narration_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("context_json", json_type, nullable=False, server_default="{}"),
        sa.Column("transcript_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(_in_list("state", _STATES), name="ck_realtime_sessions_state"),
    )
    op.create_index("ix_realtime_sessions_device_id", "realtime_sessions", ["device_id"])
    op.create_index(
        "ix_realtime_sessions_owner_session_id", "realtime_sessions", ["owner_session_id"]
    )
    op.create_index("ix_realtime_sessions_state", "realtime_sessions", ["state"])

    op.create_table(
        "realtime_tool_calls",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("realtime_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("arguments_json", json_type, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("result_json", json_type, nullable=True),
        sa.Column("error_class", sa.String(length=64), nullable=True),
        sa.Column("long_running", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("session_id", "call_id", name="uq_realtime_tool_call"),
        sa.CheckConstraint(
            _in_list("status", _TOOL_STATUSES), name="ck_realtime_tool_calls_status"
        ),
    )
    op.create_index("ix_realtime_tool_calls_session_id", "realtime_tool_calls", ["session_id"])
    op.create_index("ix_realtime_tool_calls_status", "realtime_tool_calls", ["status"])


def downgrade() -> None:
    op.drop_index("ix_realtime_tool_calls_status", table_name="realtime_tool_calls")
    op.drop_index("ix_realtime_tool_calls_session_id", table_name="realtime_tool_calls")
    op.drop_table("realtime_tool_calls")
    op.drop_index("ix_realtime_sessions_state", table_name="realtime_sessions")
    op.drop_index("ix_realtime_sessions_owner_session_id", table_name="realtime_sessions")
    op.drop_index("ix_realtime_sessions_device_id", table_name="realtime_sessions")
    op.drop_table("realtime_sessions")
