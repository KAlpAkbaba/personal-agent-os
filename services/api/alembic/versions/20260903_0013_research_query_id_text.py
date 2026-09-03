"""M13: research_candidates.query_id becomes text.

Revision ID: 0013_research_query_id_text
Revises: 0012_browser_research
Create Date: 2026-09-03

The first live run stored the plan's query text as ``query_id`` and the 64-character
column refused a normal Turkish topic sentence, failing every discovery activity for
that query. Query ids are free text (the query itself); widen the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_research_query_id_text"
down_revision = "0012_browser_research"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "research_candidates",
        "query_id",
        existing_type=sa.String(length=64),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "research_candidates",
        "query_id",
        existing_type=sa.Text(),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
