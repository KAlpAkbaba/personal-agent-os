"""The owner's connected mail/calendar accounts, and the account on every message and draft.

Revision ID: 0068_mail_accounts
Revises: 0066_watches
Create Date: 2026-10-05

Chains from ``0066_watches`` - the chain tip on this branch. (Card alarm-song-by-voice adds
``0067`` on its own branch from the same tip; whichever merges second re-points its
``down_revision`` at the other - the number 0068 was taken so the two never collide.)

**Two new tables.** ``mail_accounts``: one row per account the owner connected on
Ayarlar > Hesaplar, under his own name for it; the OAuth tokens are Fernet ciphertext
(``LargeBinary``), never text. ``mail_account_pending``: one authorization in flight - the
SHA-256 of its ``state`` and the PKCE verifier encrypted; a row lives at most 15 minutes.

**Two new columns.** ``mail_index.account_name`` (NOT NULL, server default ``''`` - the env
account), and the index's identity becomes ``(account_name, provider_message_id)``: one
message may sit in two of the owner's accounts. ``mail_drafts.account_name`` (nullable): the
account the draft goes from.

**Expand-only for a blue-green release.** The old colour never reads the new tables; it
writes ``mail_index`` rows without the new column (the server default fills ``''``) and its
upsert looks a row up by Message-ID alone, which still finds the env account's row. The
downgrade drops the tables and columns; ``mail_index`` is a cache rebuilt by the next poll,
so its account rows are deleted first (the old unique index on Message-ID alone could not
hold one message twice).

ORM models: app/accounts/models.py::MailAccountRow, MailAccountPendingRow;
app/mail/models.py::MailIndexRow.account_name, MailDraftRow.account_name.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0068_mail_accounts"
down_revision: str | None = "0066_watches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "mail_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("name_key", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("address", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("scopes_json", _JSON, nullable=False),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("last_error", sa.String(length=120), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mail_accounts_name_key", "mail_accounts", ["name_key"], unique=True)
    op.create_table(
        "mail_account_pending",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("code_verifier_enc", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_mail_account_pending_state_hash", "mail_account_pending", ["state_hash"], unique=True
    )
    op.add_column(
        "mail_index",
        sa.Column("account_name", sa.String(length=40), nullable=False, server_default=""),
    )
    op.drop_index("ix_mail_index_provider_message_id", table_name="mail_index")
    op.create_index(
        "ix_mail_index_account_message_id",
        "mail_index",
        ["account_name", "provider_message_id"],
        unique=True,
    )
    op.add_column("mail_drafts", sa.Column("account_name", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("mail_drafts", "account_name")
    op.execute("DELETE FROM mail_index WHERE account_name <> ''")
    op.drop_index("ix_mail_index_account_message_id", table_name="mail_index")
    op.create_index(
        "ix_mail_index_provider_message_id", "mail_index", ["provider_message_id"], unique=True
    )
    op.drop_column("mail_index", "account_name")
    op.drop_index("ix_mail_account_pending_state_hash", table_name="mail_account_pending")
    op.drop_table("mail_account_pending")
    op.drop_index("ix_mail_accounts_name_key", table_name="mail_accounts")
    op.drop_table("mail_accounts")
