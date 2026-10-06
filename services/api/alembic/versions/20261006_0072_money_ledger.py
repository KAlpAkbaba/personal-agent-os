"""JARVIS's own money ledger: entries, balances, the bank mails read, the questions, the scans.

Revision ID: 0072_money_ledger
Revises: 0071_claim_verifications
Create Date: 2026-10-06

Written on ``0070_household_stock`` (the head of team/nightly/lead when the card started).
Another card's migration that lands first (conversation-followups carries a 0071 in parallel)
re-points ``down_revision`` at merge - the integration step's work, not this card's.

**Five tables.** ``money_entries`` - one row per spend or income, in kuruş, tentative /
confirmed / cancelled; a bank mail that confirms a row writes its reference ONTO it
(``bank_ref`` unique: one spend, counted once) and a conversation spend is unique per
(conversation, line). ``money_balances`` - the last balance per bank with the mail's time.
``money_bank_notices`` - each bank mail read once (``ref`` unique). ``money_questions`` - "X
liralık bir harcama yaptınız mı?", unique per (conversation, line). ``money_scans`` - how far a
conversation was read. No column holds a bank password, a card number or an account number.

**Expand-only and reversible.** Five new tables; nothing existing is touched, so both colours
of a blue-green release serve beside them. The downgrade drops all five.

ORM models: app/money/models.py.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072_money_ledger"
down_revision: str | None = "0071_claim_verifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "money_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("direction", sa.String(length=4), nullable=False),
        sa.Column("amount_kurus", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=True),
        sa.Column("category", sa.String(length=24), nullable=True),
        sa.Column("description", sa.String(length=120), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("segment_seq", sa.Integer(), nullable=True),
        sa.Column("bank_ref", sa.String(length=600), nullable=True),
        sa.Column("bank", sa.String(length=40), nullable=True),
        sa.CheckConstraint("amount_kurus > 0", name="ck_money_entries_amount_positive"),
        sa.CheckConstraint(
            "status IN ('tentative', 'confirmed', 'cancelled')", name="ck_money_entries_status"
        ),
        sa.CheckConstraint("direction IN ('out', 'in')", name="ck_money_entries_direction"),
        sa.UniqueConstraint("bank_ref", name="uq_money_entries_bank_ref"),
        sa.UniqueConstraint(
            "conversation_id", "segment_seq", name="uq_money_entries_conversation_segment"
        ),
    )
    op.create_index("ix_money_entries_occurred_at", "money_entries", ["occurred_at"])
    op.create_table(
        "money_balances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("bank", sa.String(length=40), nullable=False),
        sa.Column("balance_kurus", sa.BigInteger(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ref", sa.String(length=600), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("bank", name="uq_money_balances_bank"),
    )
    op.create_table(
        "money_bank_notices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ref", sa.String(length=600), nullable=False),
        sa.Column("bank", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("amount_kurus", sa.BigInteger(), nullable=True),
        sa.Column("balance_kurus", sa.BigInteger(), nullable=True),
        sa.Column("merchant", sa.String(length=120), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=12), nullable=False),
        sa.Column(
            "entry_id",
            sa.Uuid(),
            sa.ForeignKey("money_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ref", name="uq_money_bank_notices_ref"),
    )
    op.create_table(
        "money_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("anchor_seq", sa.Integer(), nullable=False),
        sa.Column("amount_kurus", sa.BigInteger(), nullable=True),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=True),
        sa.Column("reason", sa.String(length=24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answer", sa.String(length=8), nullable=True),
        sa.Column(
            "entry_id",
            sa.Uuid(),
            sa.ForeignKey("money_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("conversation_id", "anchor_seq", name="uq_money_questions_anchor"),
    )
    op.create_table(
        "money_scans",
        sa.Column("conversation_id", sa.Uuid(), primary_key=True),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("money_scans")
    op.drop_table("money_questions")
    op.drop_table("money_bank_notices")
    op.drop_table("money_balances")
    op.drop_index("ix_money_entries_occurred_at", table_name="money_entries")
    op.drop_table("money_entries")
