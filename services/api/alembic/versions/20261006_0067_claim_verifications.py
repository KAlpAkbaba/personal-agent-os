"""The verify mode's table: one claim the owner asked to check, its verdict and sources.

Revision ID: 0067_claim_verifications
Revises: 0066_watches
Create Date: 2026-10-06

Chains from ``0066_watches`` -- the chain tip on this branch's base. Another branch of the
same cycle may also write a 0067; the lead re-chains at merge (card migration-rechain-on-merge).

**One table.** ``claim_verifications`` holds the sentence as the owner said it, the claim,
``pending`` until the research run behind it is terminal, then the verdict code
(dogru/yanlis/kismen/belirsiz), the confidence, at most five sources (url, title, date, the
quote that decides, the stance), the counter-argument and the sentence spoken. Recall reads it
by date (``created_at`` index) and by text (in Python, Turkish-folded).

**Expand-only and reversible.** A new table and its indexes; nothing existing is touched, so
both colours of a blue-green release serve beside it. ``task_id`` is SET NULL when a task is
deleted - the verdict outlives the run. The downgrade drops the table.

ORM model: app/research/models.py::ClaimVerificationRow.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0067_claim_verifications"
down_revision: str | None = "0066_watches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "claim_verifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("said", sa.String(length=2000), nullable=False),
        sa.Column("claim", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("verdict", sa.String(length=16), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("sources_json", _JSON, nullable=False),
        sa.Column("counter_json", _JSON, nullable=True),
        sa.Column("spoken", sa.String(length=1000), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'settled')", name="ck_claim_verifications_status"
        ),
    )
    op.create_index("ix_claim_verifications_created_at", "claim_verifications", ["created_at"])
    op.create_index("ix_claim_verifications_task_id", "claim_verifications", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_claim_verifications_task_id", table_name="claim_verifications")
    op.drop_index("ix_claim_verifications_created_at", table_name="claim_verifications")
    op.drop_table("claim_verifications")
