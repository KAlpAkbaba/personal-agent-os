"""The memory's word leg: pg_trgm and two GIN indexes on ``memories.text``.

Revision ID: 0073_memory_text_trgm
Revises: 0072_money_ledger
Create Date: 2026-10-06

Card memory-lexical-turkish-rrf. Written on ``0070_household_stock`` (main's head); the
integration re-points ``down_revision`` at the tree's head at merge (migration ids from the
tree).

- ``CREATE EXTENSION IF NOT EXISTS pg_trgm``; when the image does not ship it the migration
  stops with a plain error instead of a half-built schema. pg_trgm is "trusted" from PG 13.
- ``ix_memories_text_fold_trgm``: GIN ``gin_trgm_ops`` on ``translate(lower(text), 'ı', 'i')``
  - the expression ``app.memory.retrieval`` matches with ``<%``, character for character, or
  the planner never uses it.
- ``ix_memories_text_tsv_tr``: GIN on ``to_tsvector('turkish'::regconfig, text)``.

**Expand-only and reversible.** Two indexes, no column, no data change; the old colour of a
blue-green release never notices them. The downgrade drops both indexes and leaves the
extension: another migration or a hand-made index may use it, and an installed extension
with nothing on it costs nothing. SQLite: no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0073_memory_text_trgm"
down_revision: str | None = "0072_money_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FOLD_INDEX = "ix_memories_text_fold_trgm"
TSV_INDEX = "ix_memories_text_tsv_tr"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm'")
    ).scalar()
    if not available:
        raise RuntimeError(
            "pg_trgm bu PostgreSQL imajında yok (pg_available_extensions): hafızanın kelime "
            "ayağı dizinleri kurulamaz; pgvector/pgvector:pg16 gibi contrib içeren bir "
            "imaj kullanın"
        )
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {FOLD_INDEX} ON memories "
        "USING gin (translate(lower(text), 'ı', 'i') gin_trgm_ops)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {TSV_INDEX} ON memories "
        "USING gin (to_tsvector('turkish'::regconfig, text))"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP INDEX IF EXISTS {TSV_INDEX}")
    op.execute(f"DROP INDEX IF EXISTS {FOLD_INDEX}")
