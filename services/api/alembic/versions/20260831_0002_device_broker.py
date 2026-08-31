"""Device broker schema (M1): devices, device_sessions, device_commands,
enrollment_tokens, audit_events.

Revision ID: 0002_device_broker
Revises: 0001_initial
Create Date: 2026-08-31

Reversible: downgrade drops all M1 tables and leaves the M0 schema intact.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_device_broker"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column("public_key_spki_b64", sa.Text(), nullable=False),
        sa.Column(
            "capabilities_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "enrolled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="enrolled"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('enrolled', 'revoked')", name="ck_devices_status"),
    )

    op.create_table(
        "device_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("software_version", sa.String(length=64), nullable=False),
    )
    op.create_index("ix_device_sessions_device_id", "device_sessions", ["device_id"])

    op.create_table(
        "device_commands",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column(
            "payload_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("result_json", postgresql.JSONB(), nullable=True),
        sa.Column("error_class", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'accepted', 'running', "
            "'succeeded', 'failed', 'expired', 'cancelled')",
            name="ck_device_commands_status",
        ),
        sa.UniqueConstraint(
            "device_id", "idempotency_key", name="uq_device_commands_idempotency"
        ),
    )
    op.create_index("ix_device_commands_device_id", "device_commands", ["device_id"])
    op.create_index("ix_device_commands_status", "device_commands", ["status"])
    op.create_index(
        "ix_device_commands_expiry_sweep",
        "device_commands",
        ["expires_at"],
        postgresql_where=sa.text("status IN ('pending', 'delivered')"),
    )

    op.create_table(
        "enrollment_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("subject_ref", sa.String(length=256), nullable=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_device_id", "audit_events", ["device_id"])
    op.create_index("ix_audit_events_command_id", "audit_events", ["command_id"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("enrollment_tokens")
    op.drop_table("device_commands")
    op.drop_table("device_sessions")
    op.drop_table("devices")
