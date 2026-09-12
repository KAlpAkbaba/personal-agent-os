"""Executive runs: steps that WORKED, and steps that did not (B10 req 558/559).

Revision ID: 0043_executive_steps_failed
Revises: 0042_bounded_delivery
Create Date: 2026-09-13

Chains from ``0042_bounded_delivery`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file. Expand-only and reversible: one
defaulted column, nothing existing is rewritten.

**Why.** ``steps_done`` was computed as "every step in a terminal state" - which includes
``failed``, ``cancelled``, ``compensated`` and ``skipped`` - and then spoken as *"adım tamam"*,
steps completed. A run whose four steps were three failures and one success reported
**"4/4 adım tamam"**. Two production runs were saying it.

Two different questions ("how many are settled?" and "how many worked?") had one answer, and
the answer given was the wrong one for the sentence it appeared in. ``steps_done`` now counts
successes only, and this column carries the other half so no caller has to infer failure by
subtracting - "not done" used to mean either "still going" or "it failed", and nothing said
which.

Existing rows get 0, which is the honest default: their ``steps_done`` was computed under the
old meaning and this migration cannot know how many of those were failures. The next
reconciliation of each run recomputes both from its steps.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_executive_steps_failed"
down_revision: str | None = "0042_bounded_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executive_runs",
        sa.Column("steps_failed", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("executive_runs", "steps_failed")
