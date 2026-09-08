"""M22 Artifact Factory: validation_json + state on artifact_renders (ADR-0085 §3-4).

Revision ID: 0028_artifact_validation
Revises: 0029_confirmation_binding
Create Date: 2026-09-08

Reversible, expand-only. ``artifact_renders`` (M13: ``app/artifacts/models.py``) is
the SAME table both the pre-M22 research-report render pipeline and the new
Artifact Factory write to (Task -> Artifact -> Presentation, ADR-0020; the factory
reuses artifacts/artifact_versions/artifact_renders rather than a parallel schema —
ADR-0085 decision 3/4). This migration adds:

* ``validation_json`` — the independent reader's :class:`ValidationReport`
  (``app.artifacts.validation``), ``NULL`` for a render nobody has validated (every
  pre-M22 row, and any M22 row before its first ``validate()`` call);
* ``state`` — ``"valid" | "invalid"``, defaulted to ``"valid"`` for every EXISTING
  row. This is a deliberate reading, not a claim those renders were literally
  validated: the pre-M22 pipeline (PDF/DOCX/HTML/TXT research reports) has its own
  battle-tested test suite and ships today; "valid" here means "not flagged
  invalid by the new validator", which is the honest default for content nobody
  has re-opened with an independent reader yet. Every NEW render the factory
  creates gets an explicit ``"valid"``/``"invalid"`` from a real
  ``validate()`` call before this column is ever written for that row (never left
  to the default) — see ``app/artifacts/factory.py``.

No column is dropped, narrowed, or renamed, so an old release reading this table
sees every row it already understood, exactly as before, plus two new columns it
never asked for.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_artifact_validation"
down_revision: str | None = "0029_confirmation_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.add_column("artifact_renders", sa.Column("validation_json", json_type, nullable=True))
    op.add_column(
        "artifact_renders",
        sa.Column("state", sa.String(length=16), nullable=False, server_default="valid"),
    )


def downgrade() -> None:
    op.drop_column("artifact_renders", "state")
    op.drop_column("artifact_renders", "validation_json")
