"""Conversations as text: conversations, their lines, named people, the 'evde dinle' switch.

Revision ID: 0069_conversation_transcripts
Revises: 0068_mail_accounts
Create Date: 2026-10-05

Chains from ``0066_watches`` -- the chain tip of the base this card started on; re-pointed at
merge if another card's migration lands first.

**No audio column.** ``conversation_segments`` holds one line of TEXT each, the voice's number
in that conversation, whether it was the owner, and the consenting person it belongs to. The
only bytes column is ``conversation_people.profile_sealed`` - a DERIVED voice embedding,
encrypted - and a CHECK refuses it unless ``consent_at`` is set (KVKK: biometric data). A
deleted person's lines keep their number (``person_id`` ON DELETE SET NULL), so they read
'Konuşmacı N' again.

**Expand-only and reversible.** Four new tables and their indexes; nothing existing is
touched, so both colours of a blue-green release serve beside them. The downgrade drops all
four (and with them every transcript - the owner's 'unut' does the same).

ORM models: app/conversations/models.py.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0069_conversation_transcripts"
down_revision: str | None = "0068_mail_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('manual', 'home')", name="ck_conversations_mode"),
    )
    op.create_index("ix_conversations_started_at", "conversations", ["started_at"])
    op.create_table(
        "conversation_people",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("name_key", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_note", sa.String(length=200), nullable=True),
        sa.Column("profile_sealed", sa.LargeBinary(), nullable=True),
        sa.Column("profile_model", sa.String(length=80), nullable=True),
        sa.CheckConstraint(
            "profile_sealed IS NULL OR consent_at IS NOT NULL",
            name="ck_conversation_people_profile_needs_consent",
        ),
        sa.UniqueConstraint("name_key", name="uq_conversation_people_name_key"),
    )
    op.create_table(
        "conversation_segments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("spoken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.String(length=4000), nullable=False),
        sa.Column("is_owner", sa.Boolean(), nullable=False),
        sa.Column("speaker_no", sa.Integer(), nullable=True),
        sa.Column(
            "person_id",
            sa.Uuid(),
            sa.ForeignKey("conversation_people.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_conversation_segments_seq"),
    )
    op.create_index(
        "ix_conversation_segments_conversation_id", "conversation_segments", ["conversation_id"]
    )
    op.create_index("ix_conversation_segments_person_id", "conversation_segments", ["person_id"])
    op.create_table(
        "conversation_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("home_listen", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("conversation_settings")
    op.drop_index("ix_conversation_segments_person_id", table_name="conversation_segments")
    op.drop_index("ix_conversation_segments_conversation_id", table_name="conversation_segments")
    op.drop_table("conversation_segments")
    op.drop_table("conversation_people")
    op.drop_index("ix_conversations_started_at", table_name="conversations")
    op.drop_table("conversations")
