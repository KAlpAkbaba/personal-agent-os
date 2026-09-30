"""The agent team's queue, lock and reports on the Cloud Core (pilot-02).

Revision ID: 0063_team_state
Revises: 0062_web_tasks
Create Date: 2026-09-30

Chains from ``0062_web_tasks`` -- the chain tip, confirmed against every ``down_revision`` in
this directory before writing this file.

**Why a table.** The queue and the lock lived in ``team/queue.json`` and ``team/lock.json`` on
one PC: with that PC off, no approval could be given, and the office PC saw its own copy. One
row per task, one lock row and the cycle reports (as text, for the Onay Merkezi) move to the
Cloud Core; both machines read and write the same rows through ``/v1/team/queue``.

**Expand-only and reversible.** One new table; the downgrade drops it. Nothing existing is
touched, so both colours of a blue-green release serve beside it.

ORM model: app/team/models.py::TeamStateRow.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0063_team_state"
down_revision: str | None = "0062_web_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "team_state"
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("kind", sa.String(length=16), primary_key=True),
        sa.Column("key", sa.String(length=80), primary_key=True),
        sa.Column("doc", _JSON, nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
