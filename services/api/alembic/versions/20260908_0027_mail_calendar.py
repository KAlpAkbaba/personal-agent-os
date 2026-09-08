"""M21 Mail & Calendar: mail_index, mail_drafts, calendar_index, calendar_proposals (ADR-0084).

Revision ID: 0027_mail_calendar
Revises: 0026_document_index
Create Date: 2026-09-08

Reversible, expand-only. ORM models: app/mail/models.py, app/calendar/models.py. Same
discipline migration 0026 already documents: portable types (generic ``Uuid``, ``JSON``
with a ``JSONB`` variant on Postgres), no background writer — every row here is the direct
result of an owner-initiated read/draft/propose (ADR-0084 decision 4).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_mail_calendar"
down_revision: str | None = "0026_document_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "mail_index",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("provider_message_id", sa.String(length=500), nullable=False),
        sa.Column("folder", sa.String(length=200), nullable=False),
        sa.Column("from_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("from_email", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("to_json", json_type, nullable=False, server_default="[]"),
        sa.Column("subject", sa.String(length=998), nullable=False, server_default=""),
        sa.Column("date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snippet", sa.String(length=500), nullable=False, server_default=""),
        sa.Column(
            "has_attachments", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("thread_key", sa.String(length=998), nullable=False, server_default=""),
        sa.Column("unread", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_mail_index_provider_message_id",
        "mail_index",
        ["provider_message_id"],
        unique=True,
    )
    op.create_index("ix_mail_index_thread_key", "mail_index", ["thread_key"])
    op.create_index("ix_mail_index_last_used_at", "mail_index", ["last_used_at"])

    op.create_table(
        "mail_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("to_json", json_type, nullable=False, server_default="[]"),
        sa.Column("cc_json", json_type, nullable=False, server_default="[]"),
        sa.Column("subject", sa.String(length=998), nullable=False, server_default=""),
        sa.Column("body", sa.String(length=20000), nullable=False, server_default=""),
        sa.Column("in_reply_to", sa.String(length=500), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="prepared"),
        sa.Column("read_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_message_id", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "calendar_index",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("uid", sa.String(length=500), nullable=False),
        sa.Column("summary", sa.String(length=998), nullable=False, server_default=""),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("all_day", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_calendar_index_uid", "calendar_index", ["uid"])
    op.create_index("ix_calendar_index_last_used_at", "calendar_index", ["last_used_at"])

    op.create_table(
        "calendar_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("event_uid", sa.String(length=500), nullable=True),
        sa.Column("summary", sa.String(length=998), nullable=False, server_default=""),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("conflicts_json", json_type, nullable=False, server_default="[]"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="prepared"),
        sa.Column("read_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_event_uid", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("calendar_proposals")
    op.drop_index("ix_calendar_index_last_used_at", table_name="calendar_index")
    op.drop_index("ix_calendar_index_uid", table_name="calendar_index")
    op.drop_table("calendar_index")
    op.drop_table("mail_drafts")
    op.drop_index("ix_mail_index_last_used_at", table_name="mail_index")
    op.drop_index("ix_mail_index_thread_key", table_name="mail_index")
    op.drop_index("ix_mail_index_provider_message_id", table_name="mail_index")
    op.drop_table("mail_index")
