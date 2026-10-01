"""Memory class ``vocabulary``: a synonym the owner taught by a correction (ADR-0224).

Revision ID: 0064_memory_vocabulary_class
Revises: 0063_team_state
Create Date: 2026-10-01

Chains from ``0063_team_state`` -- the chain tip, confirmed against every ``down_revision`` in
this directory before writing this file.

**Why.** ``ck_memories_class`` (0005) names the six classes of M5. A correction writes a
seventh, ``vocabulary`` ("ofüs = ofis (cihaz)"), and PostgreSQL refused the row: ``learn()``
answered ``write_failed`` and the owner was asked the same question again every time. The unit
suite runs on SQLite, which builds the tables from the ORM models and has no such constraint;
``tests/integration/test_understanding_vocabulary_postgres.py`` is the proof.

**Expand-only and reversible.** The constraint is widened by one value: every row the old
colour of a blue-green release writes still satisfies it, and the old colour never writes the
new class. The downgrade deletes the vocabulary rows first (their versions, evidence and
embeddings go with them, ON DELETE CASCADE) - a synonym is re-taught by one sentence - and
restores the six-value constraint.

Enum: app/memory/types.py::MemoryClass.VOCABULARY.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0064_memory_vocabulary_class"
down_revision: str | None = "0063_team_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "memories"
_CONSTRAINT = "ck_memories_class"
_OLD = ("preference", "episodic", "project", "semantic", "procedural", "voice_preference")
_NEW = (*_OLD, "vocabulary")


def _in_list(values: Sequence[str]) -> str:
    return "memory_class IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _in_list(_NEW))


def downgrade() -> None:
    op.execute("DELETE FROM memories WHERE memory_class = 'vocabulary'")
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _in_list(_OLD))
