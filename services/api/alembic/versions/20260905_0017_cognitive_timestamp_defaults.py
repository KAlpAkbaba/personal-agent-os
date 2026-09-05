"""M17 timestamp defaults: the migration and the models had drifted apart.

Every ``created_at``/``updated_at`` in migration 0016 was created NOT NULL with no
``server_default``, while the mapped classes all declare ``server_default=func.now()``.
SQLAlchemy therefore omits those columns from its INSERT and expects the database to fill
them, which Postgres refused:

    IntegrityError: null value in column "created_at" of relation "experience_lessons"
    violates not-null constraint

Nothing caught it before the first real deployment, and that is the interesting part: the
unit tests build their tables from the MODEL metadata, which carries the defaults, so the
tables under test were never the tables the migration makes. A test suite that creates its
own schema cannot see migration drift by construction. This migration exists because the
M17 combined qualification ran against real Postgres and the compiler died on its first
lesson (2026-09-05).

Adding a server default is safe to re-run and safe to roll back: it changes no existing row
and no column type, only what happens when a value is omitted.

The revision id is short on purpose. ``alembic_version.version_num`` is VARCHAR(32), and
the first spelling of this id was 33 characters: every DDL statement applied, then the
version bookkeeping UPDATE failed and the whole transaction rolled back, so the migration
appeared to do nothing at all while reporting an error about string truncation.

Revision ID: 0017_cognitive_ts_defaults
Revises: 0016_cognitive_foundations
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0017_cognitive_ts_defaults"
down_revision: str | None = "0016_cognitive_foundations"
branch_labels = None
depends_on = None

#: (table, column) pairs created by 0016 whose model declares server_default=func.now().
_TIMESTAMP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("goals", "created_at"),
    ("goals", "updated_at"),
    ("goal_tasks", "created_at"),
    ("experience_lessons", "created_at"),
    ("experience_lessons", "updated_at"),
    ("code_modules", "created_at"),
    ("code_modules", "updated_at"),
    ("evolution_opportunities", "created_at"),
    ("evolution_opportunities", "updated_at"),
)


def upgrade() -> None:
    for table, column in _TIMESTAMP_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=sa.text("now()"),
        )


def downgrade() -> None:
    for table, column in _TIMESTAMP_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=None,
        )
