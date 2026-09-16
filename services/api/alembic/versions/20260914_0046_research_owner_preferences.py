"""The owner's standing register for research answers (B31 req 209).

Revision ID: 0046_research_owner_preferences
Revises: 0045_routine_pause_and_condition
Create Date: 2026-09-14

Chains from ``0045_routine_pause_and_condition`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why a column and not a session flag.** "Bundan sonra teknik anlat" is a decision about
how the owner wants to be answered from now on, not about this turn; a turn-scoped flag
would forget it at the next session, and the owner would say it again (the same shape as
the repeated clarification ADR-0076 closed). ``research_owner_state`` is the one-row
owner-state table the research family already keeps, so the preference lives next to the
pending clarification: ``preferences_json`` = ``{"answer_level": "technical", "set_at": ...}``.

**Expand-only and reversible.** One nullable JSON column; a row that exists today is
unchanged, and the downgrade drops the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_research_owner_preferences"
down_revision: str | None = "0045_routine_pause_and_condition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("research_owner_state") as batch:
        batch.add_column(sa.Column("preferences_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("research_owner_state") as batch:
        batch.drop_column("preferences_json")
