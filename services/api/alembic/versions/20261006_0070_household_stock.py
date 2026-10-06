"""The house's stock: the items, their levels and list flag, and the dated events.

Revision ID: 0070_household_stock
Revises: 0069_conversation_transcripts
Create Date: 2026-10-05

Re-pointed at ``0069_conversation_transcripts`` by the Danisman at the 2026-10-06 integration (it was written on ``0066_watches``). Another
card's migration that lands first re-points ``down_revision`` at merge (the Danışman's note
on the card).

**Why two tables.** ``household_items`` is one row per thing the house keeps: its name and
folded key (unique - "sütü" and "süt" are one row), the level (var / azaldı / bitti, or NULL
for an item only ever listed), whether it is on the shopping list and how much, and the
rhythm learnt for it (``cycle_days``) with the reminder stamp. ``household_events`` is one
row per depletion and per restock, the only input the rhythm is computed from.

**Expand-only and reversible.** Two new tables and their indexes; nothing existing is
touched, so both colours of a blue-green release serve beside them (the old colour never
reads or writes here). The downgrade drops both.

ORM models: app/household/models.py::HouseholdItem, HouseholdEvent.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0070_household_stock"
down_revision: str | None = "0069_conversation_transcripts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "household_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=True),
        sa.Column("on_list", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("list_quantity", sa.String(length=40), nullable=True),
        sa.Column("usual_quantity", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("depleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cycle_days", sa.Float(), nullable=True),
        sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key", name="uq_household_items_key"),
    )
    op.create_table(
        "household_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("household_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_household_events_item_id", "household_events", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_household_events_item_id", table_name="household_events")
    op.drop_table("household_events")
    op.drop_table("household_items")
