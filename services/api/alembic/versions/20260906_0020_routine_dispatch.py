"""M18 routine dispatch visibility: RoutineFiring.dispatch_results/dispatch_status.

Revision ID: 0020_routine_dispatch
Revises: 0019_routines
Create Date: 2026-09-06

Reversible. ORM model: app/routines/models.py::RoutineFiring.

Adds two columns to the existing ``routine_firings`` table (created by 0019, empty until
now — no production data exists yet for this milestone): ``dispatch_results`` (JSON list,
NOT NULL, one ``{"kind","status","ok","reason","detail"}`` entry per dispatched action) and
``dispatch_status`` (the AGGREGATE across those entries — ``succeeded | partial | failed |
refused | none``, NULL for a ``skipped`` firing that never dispatched anything at all).

SQLite cannot ``ALTER TABLE ... ADD CONSTRAINT`` a CHECK after the fact — the same limit
migration 0019 never had to face because it created ``routine_firings`` fresh, and the one
migration 0018's fix commit (``e5b3346``, "the status CHECK constraint never existed, and the
column was too small") ran into on POSTGRES, where the workaround was a plain
``ALTER TABLE ... DROP CONSTRAINT IF EXISTS`` because Postgres supports altering constraints
directly. Neither of those patterns is portable to SQLite for an ADD. ``op.batch_alter_table``
is: on SQLite it transparently recreates the table (copy, drop, rename) to add the column and
the CHECK constraint in one pass; on every other dialect (Postgres included) each operation
inside the block runs as the ordinary, efficient ``ALTER TABLE`` it would have been without
batch mode. That is what makes this migration verifiable: it was run end to end against a
throwaway SQLite database (``PAGENTOS_DATABASE_URL=sqlite:///...`` + ``alembic upgrade head``
then ``alembic downgrade -1``) as part of authoring it, which a Postgres-only migration in
this codebase cannot be in the unit-test sandbox.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_routine_dispatch"
down_revision: str | None = "0019_routines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DISPATCH_STATUSES = ("succeeded", "partial", "failed", "refused", "none")
_CHECK_NAME = "ck_routine_firings_dispatch_status"


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    with op.batch_alter_table("routine_firings") as batch_op:
        # A temporary server_default backfills any existing row (there are none yet for
        # this pre-release milestone, but the column is NOT NULL and the ALTER must still
        # be valid on a non-empty table). The ORM model has no server_default for this
        # column (Python-side `default=list`, like `actions_snapshot` beside it), so the
        # default is dropped again once the ADD COLUMN itself has applied.
        batch_op.add_column(
            sa.Column("dispatch_results", json_type, nullable=False, server_default="[]")
        )
        batch_op.add_column(sa.Column("dispatch_status", sa.String(length=16), nullable=True))
        batch_op.create_check_constraint(
            _CHECK_NAME, _in_list("dispatch_status", _DISPATCH_STATUSES)
        )

    with op.batch_alter_table("routine_firings") as batch_op:
        batch_op.alter_column("dispatch_results", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("routine_firings") as batch_op:
        batch_op.drop_constraint(_CHECK_NAME, type_="check")
        batch_op.drop_column("dispatch_status")
        batch_op.drop_column("dispatch_results")
