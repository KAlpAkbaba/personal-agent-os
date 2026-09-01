"""Owner identity, sessions and mobile surface (M9).

Revision ID: 0009_identity_mobile
Revises: 0008_authorized_assets
Create Date: 2026-09-01

Reversible. Lead-authored frozen foundation for M9 — ORM models in
app/identity/models.py and app/mobile/models.py must match.

Why this lands in M9: "authenticated native client connects" is M9's first
acceptance bullet, and it is the same API authentication/identity layer that
has been the standing hard gate since M0 — the prerequisite recorded against
M4 speaker verification, M5 memory mutations, M6 self-healing ingest, M7
evolution endpoints and M8 asset enrollment. Building it here closes all five.

Design notes:
- `owner_sessions` are opaque bearer credentials stored ONLY as a hash
  (`token_hash`), scoped to a client kind, optionally bound to an enrolled
  device, with an absolute expiry and an idle timeout. Revoking a device
  revokes its sessions (M9 acceptance: device revocation invalidates session).
- `session_events` is append-only: issued / refreshed / revoked / expired /
  rejected, so an authentication decision is always explainable.
- `push_registrations` hold provider tokens for artifact-ready notifications.
  The token is stored hashed as well: the delivery adapter re-reads the live
  token from the client at registration time, so a database leak cannot be
  replayed against the push provider.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_identity_mobile"
down_revision: str | None = "0008_authorized_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CLIENT_KINDS = ("web", "mobile", "desktop", "device", "cli")
_SESSION_STATUSES = ("active", "revoked", "expired")
_SESSION_EVENTS = ("issued", "refreshed", "revoked", "expired", "rejected")
_PUSH_PROVIDERS = ("fake", "fcm", "apns", "webpush")
_PUSH_STATUSES = ("active", "revoked", "invalid")


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    op.create_table(
        "owner_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("client_kind", sa.String(length=16), nullable=False),
        sa.Column("client_label", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column(
            "scopes_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=256), nullable=True),
        sa.CheckConstraint(
            _in_list("client_kind", _CLIENT_KINDS), name="ck_owner_sessions_client_kind"
        ),
        sa.CheckConstraint(
            _in_list("status", _SESSION_STATUSES), name="ck_owner_sessions_status"
        ),
        sa.UniqueConstraint("token_hash", name="uq_owner_sessions_token_hash"),
    )
    op.create_index("ix_owner_sessions_status", "owner_sessions", ["status"])
    op.create_index("ix_owner_sessions_device_id", "owner_sessions", ["device_id"])

    op.create_table(
        "session_events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("owner_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("client_kind", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.String(length=256), nullable=False, server_default=""),
        sa.Column(
            "detail_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(_in_list("action", _SESSION_EVENTS), name="ck_session_events_action"),
    )
    op.create_index("ix_session_events_action", "session_events", ["action"])
    op.create_index("ix_session_events_session_id", "session_events", ["session_id"])

    op.create_table(
        "push_registrations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("owner_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("locale", sa.String(length=16), nullable=False, server_default="tr-TR"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            _in_list("provider", _PUSH_PROVIDERS), name="ck_push_registrations_provider"
        ),
        sa.CheckConstraint(
            _in_list("status", _PUSH_STATUSES), name="ck_push_registrations_status"
        ),
        sa.UniqueConstraint(
            "session_id", "provider", name="uq_push_registrations_session_provider"
        ),
    )
    op.create_index("ix_push_registrations_session_id", "push_registrations", ["session_id"])
    op.create_index("ix_push_registrations_status", "push_registrations", ["status"])


def downgrade() -> None:
    op.drop_table("push_registrations")
    op.drop_table("session_events")
    op.drop_table("owner_sessions")
