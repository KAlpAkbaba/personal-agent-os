"""Person cards and the follow-ups taken from conversations.

Revision ID: 0077_conversation_followups
Revises: 0076_aktivra_events
Create Date: 2026-10-06

Another card's migration that lands first re-points ``down_revision`` at merge (the
Danışman's note on the card).

**Why two tables.** ``people_cards`` is one row per person the owner talked about or with: the
name and its Turkish-folded key (unique), the relation when somebody said it, and the last
conversation they were in. ``people_followups`` is one row per promise or date taken from a
conversation; each cites its line (``conversation_id`` + ``segment_seq`` + ``quote``) and a
calendar item waits in ``calendar_state='proposed'`` until the owner says 'tamam'. A deleted
conversation takes its follow-ups with it (``ON DELETE CASCADE``: 'unut' and deleting one
conversation leave none of the other side's words anywhere - the owner's KVKK rule); a deleted
card takes its follow-ups too. The card keeps no line's text: ``last_topic_seq`` points at the
line, read from the transcript while it exists.

**Expand-only and reversible.** Two new tables and their indexes, each foreign key pointing at
``conversations`` (0069) or at each other; nothing existing is touched, so both colours of a
blue-green release serve beside them. The downgrade drops both.

ORM models: app/people/models.py::PersonCardRow, FollowupRow.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0077_conversation_followups"
down_revision: str | None = "0076_aktivra_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "people_cards",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("name_key", sa.String(length=80), nullable=False),
        sa.Column("relation", sa.String(length=60), nullable=True),
        sa.Column("last_talk_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_topic_seq", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name_key", name="uq_people_cards_name_key"),
    )
    op.create_table(
        "people_followups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "card_id",
            sa.Uuid(),
            sa.ForeignKey("people_cards.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=True),
        sa.Column("what", sa.String(length=200), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_has_time", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("segment_seq", sa.Integer(), nullable=False),
        sa.Column("quote", sa.String(length=400), nullable=False),
        sa.Column("spoken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("calendar_state", sa.String(length=16), nullable=True),
        sa.Column("calendar_proposal_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('promise', 'date')", name="ck_people_followups_kind"),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('owner', 'them')",
            name="ck_people_followups_direction",
        ),
        sa.CheckConstraint(
            "calendar_state IS NULL OR calendar_state IN "
            "('proposed', 'written', 'declined', 'failed')",
            name="ck_people_followups_calendar_state",
        ),
        sa.CheckConstraint("segment_seq >= 1", name="ck_people_followups_segment"),
    )
    op.create_index("ix_people_followups_card_id", "people_followups", ["card_id"])
    op.create_index("ix_people_followups_conversation_id", "people_followups", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_people_followups_conversation_id", table_name="people_followups")
    op.drop_index("ix_people_followups_card_id", table_name="people_followups")
    op.drop_table("people_followups")
    op.drop_table("people_cards")
