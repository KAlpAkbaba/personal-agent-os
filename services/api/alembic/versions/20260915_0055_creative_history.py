"""Creative run history, semantic check and artifact link (B43 req 498, 509, 511).

Revision ID: 0055_creative_history
Revises: 0054_artifact_provenance
Create Date: 2026-09-15

Chains from ``0054_artifact_provenance`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why.** Undo/redo (511) needs every output a run produced, in order, with a pointer;
the semantic check (498) is a fact about the LAST output; the delivery (509) links the
run to the image artifact it became so the artifact's own provenance names the run.

**Expand-only and reversible.**
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055_creative_history"
down_revision: str | None = "0054_artifact_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("creative_runs", sa.Column("history_json", sa.JSON(), nullable=True))
    op.add_column(
        "creative_runs",
        sa.Column("history_index", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("creative_runs", sa.Column("semantic_json", sa.JSON(), nullable=True))
    op.add_column("creative_runs", sa.Column("artifact_id", sa.Uuid(), nullable=True))
    op.add_column("creative_runs", sa.Column("delivery_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("creative_runs", "delivery_json")
    op.drop_column("creative_runs", "artifact_id")
    op.drop_column("creative_runs", "semantic_json")
    op.drop_column("creative_runs", "history_index")
    op.drop_column("creative_runs", "history_json")
