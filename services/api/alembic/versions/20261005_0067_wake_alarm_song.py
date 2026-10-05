"""A song per wake alarm: ``wake_alarms.song``.

Revision ID: 0067_wake_alarm_song
Revises: 0066_watches
Create Date: 2026-10-05

Chains from ``0066_watches`` -- the chain tip, confirmed against every ``down_revision`` in
this directory before writing this file.

**Expand-only and reversible.** One nullable JSON column on an existing table, no default and
no backfill: an alarm without its own song keeps playing the global wake song and then the
tone, exactly as before, so both colours of a blue-green release serve beside it (the old
colour never reads it). The downgrade drops the column; the alarms keep ringing the global
song.

ORM model: app/alarms/models.py::WakeAlarm.song.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0067_wake_alarm_song"
down_revision: str | None = "0066_watches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.add_column("wake_alarms", sa.Column("song", json_type, nullable=True))


def downgrade() -> None:
    op.drop_column("wake_alarms", "song")
