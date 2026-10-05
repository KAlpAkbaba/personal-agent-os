"""The watch: a public page read in the cloud, its hash and one value per reading.

Revision ID: 0066_watches
Revises: 0065_misheard_utterances
Create Date: 2026-10-03

Chains from ``0065_misheard_utterances`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why two tables.** ``watches`` is a standing subscription with its own clock
(``every_hours`` / ``next_due_at``) and its state (the baseline hash, whether the condition
holds, the failures in a row). ``watch_readings`` is one row per reading - a hash, at most one
number, the outcome and a short reason - purged after 30 days. No column holds page text.

**Expand-only and reversible.** Two new tables and their indexes; nothing existing is
touched, so both colours of a blue-green release serve beside them (the old colour never
reads or writes here). The downgrade drops both (the readings are 30-day data by design).

ORM models: app/watch/models.py::Watch, WatchReading.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066_watches"
down_revision: str | None = "0065_misheard_utterances"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("condition", sa.String(length=240), nullable=False),
        sa.Column("every_hours", sa.Integer(), nullable=False),
        sa.Column("selector", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outcome", sa.String(length=16), nullable=True),
        sa.Column("last_value", sa.Float(), nullable=True),
        sa.Column("baseline_sha256", sa.String(length=64), nullable=True),
        sa.Column("condition_met", sa.Boolean(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
    )
    op.create_index("ix_watches_next_due_at", "watches", ["next_due_at"])
    op.create_table(
        "watch_readings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "watch_id",
            sa.Uuid(),
            sa.ForeignKey("watches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.Column("notified", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("watch_id", "read_at", name="uq_watch_readings_watch_read_at"),
    )
    op.create_index("ix_watch_readings_read_at", "watch_readings", ["read_at"])


def downgrade() -> None:
    op.drop_index("ix_watch_readings_read_at", table_name="watch_readings")
    op.drop_table("watch_readings")
    op.drop_index("ix_watches_next_due_at", table_name="watches")
    op.drop_table("watches")
