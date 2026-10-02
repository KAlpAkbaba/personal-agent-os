"""The misheard notebook: the sentence the recogniser wrote when it was not understood.

Revision ID: 0065_misheard_utterances
Revises: 0064_memory_vocabulary_class
Create Date: 2026-10-02

Chains from ``0064_memory_vocabulary_class`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why a table.** A sentence the system did not understand was kept nowhere (the paid session)
or for one turn (the local one), so the STT corpus held three sentences the owner really said.
One row per such sentence - text only, never audio - with the mode, the engine, the device,
the confidence band and which of four reasons put it here; ``meant`` is the owner's answer to
"ne demek istemiştin?". ``expires_at`` is ``heard_at`` + 30 days and the row is deleted then.

**Expand-only and reversible.** One new table and its two indexes; the downgrade drops it
(the sentences go with it - they are 30-day data by design). Nothing existing is touched, so
both colours of a blue-green release serve beside it; the old colour never writes here.

ORM model: app/voice/misheard/models.py::MisheardUtterance.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065_misheard_utterances"
down_revision: str | None = "0064_memory_vocabulary_class"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "misheard_utterances"
_EXPIRES_INDEX = "ix_misheard_utterances_expires_at"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("heard_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sentence", sa.String(length=2000), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("engine", sa.String(length=64), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("band", sa.String(length=8), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("resolved_intent", sa.String(length=64), nullable=True),
        sa.Column("tool", sa.String(length=64), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("meant", sa.String(length=2000), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "heard_at", name="uq_misheard_utterances_session_heard"),
    )
    op.create_index(_EXPIRES_INDEX, _TABLE, ["expires_at"])


def downgrade() -> None:
    op.drop_index(_EXPIRES_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
