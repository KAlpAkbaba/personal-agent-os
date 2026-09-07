"""M19 object focus: the generic durable object the owner is pointing at (ADR-0082).

Revision ID: 0025_object_focus
Revises: 0024_ambient_hardening
Create Date: 2026-09-07

Reversible. ORM model: app/operator/models.py::ObjectFocusRow.

``object_focus`` — append-only, one row per time the focus moved, mirroring
``research_focus`` (migration 0022): the current focus of a ``kind`` is the most recent
row, "the previous one" is the most recent row naming a different object. ``kind`` is an
open string column rather than a CHECK-constrained enum on purpose — M19 starts it with
``window``/``app`` only, and M20-M28 add kinds without a migration touching this table
again (the Python-side guard, ``app.operator.focus.UnknownFocusKind``, is what stays
closed; the schema stays additive).

Portable types throughout (generic ``Uuid``, ``JSON`` with a ``JSONB`` variant on
Postgres) so the service layer unit-tests on SQLite, the same discipline 0022 documents.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_object_focus"
down_revision: str | None = "0024_ambient_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "object_focus",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("owner_session_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("object_id", sa.String(length=200), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta_json", json_type, nullable=False, server_default="{}"),
    )
    op.create_index("ix_object_focus_kind_selected_at", "object_focus", ["kind", "selected_at"])


def downgrade() -> None:
    op.drop_index("ix_object_focus_kind_selected_at", table_name="object_focus")
    op.drop_table("object_focus")
