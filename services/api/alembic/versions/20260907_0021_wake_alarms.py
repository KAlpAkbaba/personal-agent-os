"""M18.3 wake alarms + the ambient display policy.

Revision ID: 0021_wake_alarms
Revises: 0020_routine_dispatch
Create Date: 2026-09-07

Reversible. ORM models: app/alarms/models.py::WakeAlarm, ::AmbientPolicyRow.

Two new tables, both created fresh (so the CHECK constraints go in the CREATE TABLE and
none of migration 0020's ``batch_alter_table`` gymnastics is needed):

``wake_alarms`` — the aggregate of M18.3 spec §3.1. Portable types throughout (generic
``Uuid``, ``JSON`` with a ``JSONB`` variant on Postgres) for the same reason
``app.routines.models`` uses them: the service layer's unit tests build this schema from
the model metadata on SQLite, and a Postgres-only column type would make that impossible
without a second, drifting definition. The two CHECK constraints spell out the closed state
vocabulary so a state outside ``ALARM_STATES`` cannot reach the database even from a path
that skips the Python-side transition guard.

``ambient_policy`` — one row, primary key ``'owner'`` (M18.3 spec §3.9). A fixed-key table
rather than a JSON preferences blob: the thresholds that decide whether the owner's screens
go dark are worth a typed column each, and a NOT NULL default each, so a half-written policy
cannot exist. Every default here is the conservative one (``auto_off_enabled`` false).

Verified while authoring it by applying THIS revision, in both directions, against a
throwaway SQLite database: ``PAGENTOS_DATABASE_URL=sqlite:///... alembic stamp
0020_routine_dispatch`` (the chain BELOW this point is not SQLite-portable — 0001 issues
``CREATE EXTENSION IF NOT EXISTS vector``, which is exactly the kind of Postgres-only
construct ``alembic/env.py``'s own docstring says the migrations stay authoritative for),
then ``alembic upgrade head`` and ``alembic downgrade -1``. Migration 0020's docstring
claims a full-chain SQLite run; that is no longer possible and this one does not repeat the
claim. What the two directions actually prove is what matters here: every DDL statement in
THIS file is dialect-portable, and the downgrade really drops what the upgrade created.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_wake_alarms"
down_revision: str | None = "0020_routine_dispatch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALARM_STATES = (
    "SCHEDULED",
    "ARMED",
    "FIRING",
    "DISPLAY_WAKING",
    "MEDIA_STARTING",
    "PLAYING",
    "GREETING",
    "SNOOZED",
    "STOPPED",
    "COMPLETED",
    "CANCELLED",
    "FAILED",
)
_TERMINAL_STATES = ("STOPPED", "COMPLETED", "CANCELLED", "FAILED")


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "wake_alarms",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False, server_default="owner"),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column(
            "timezone", sa.String(length=64), nullable=False, server_default="Europe/Istanbul"
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_time", sa.String(length=5), nullable=False),
        sa.Column("recurrence", json_type, nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="SCHEDULED"),
        sa.Column("media_source", json_type, nullable=False, server_default="{}"),
        sa.Column("resolved_media_identity", json_type, nullable=True),
        sa.Column("volume_policy", json_type, nullable=False, server_default="{}"),
        sa.Column("greeting_policy", json_type, nullable=False, server_default="{}"),
        sa.Column("display_wake_policy", json_type, nullable=False, server_default="{}"),
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("routine_id", sa.Uuid(), nullable=True),
        sa.Column("snooze_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snooze_minutes", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("armed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_state", sa.String(length=20), nullable=True),
        sa.Column("terminal_reason", sa.String(length=200), nullable=True),
        sa.Column("last_firing_id", sa.Uuid(), nullable=True),
        sa.Column("media_session_id", sa.String(length=128), nullable=True),
        sa.Column("media_kind", sa.String(length=20), nullable=True),
        sa.Column("greeting_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("greeted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("playing_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_play_seconds", sa.Integer(), nullable=False, server_default="600"),
        sa.Column("detail_json", json_type, nullable=False, server_default="{}"),
        sa.CheckConstraint(_in_list("state", _ALARM_STATES), name="ck_wake_alarms_state"),
        sa.CheckConstraint(
            f"terminal_state IS NULL OR {_in_list('terminal_state', _TERMINAL_STATES)}",
            name="ck_wake_alarms_terminal_state",
        ),
    )
    op.create_index("ix_wake_alarms_state", "wake_alarms", ["state"])
    op.create_index("ix_wake_alarms_scheduled_for", "wake_alarms", ["scheduled_for"])
    op.create_index("ix_wake_alarms_routine_id", "wake_alarms", ["routine_id"])

    op.create_table(
        "ambient_policy",
        sa.Column("policy_id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("auto_off_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("off_when_away", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("off_when_asleep", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("wake_on_return", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("away_after_s", sa.Integer(), nullable=False, server_default="900"),
        sa.Column("asleep_after_s", sa.Integer(), nullable=False, server_default="600"),
        sa.Column("asleep_min_confidence", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("input_holdoff_s", sa.Integer(), nullable=False, server_default="600"),
        sa.Column("command_holdoff_s", sa.Integer(), nullable=False, server_default="900"),
        sa.Column("alarm_holdoff_s", sa.Integer(), nullable=False, server_default="1800"),
        sa.Column("return_holdoff_s", sa.Integer(), nullable=False, server_default="600"),
        sa.Column("quiet_hours", json_type, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("ambient_policy")
    op.drop_index("ix_wake_alarms_routine_id", table_name="wake_alarms")
    op.drop_index("ix_wake_alarms_scheduled_for", table_name="wake_alarms")
    op.drop_index("ix_wake_alarms_state", table_name="wake_alarms")
    op.drop_table("wake_alarms")
