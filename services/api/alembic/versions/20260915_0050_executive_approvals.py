"""The executive run's approval record and the step it waits on (B38 req 544).

Revision ID: 0050_executive_approvals
Revises: 0049_genesis_catalogue
Create Date: 2026-09-15

Chains from ``0049_genesis_catalogue`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why three columns.** A step whose precondition is ``owner_approval`` parks the run until
the owner says yes; the yes must be a durable fact on the run (which step, when), and the
Cockpit must be able to see WHICH step is waiting without asking the workflow. A step's
bounded loop (``repeat``, req 555) is data on the step row the activity reads.

**Expand-only and reversible.** Two nullable run columns and one defaulted step column;
the downgrade drops them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_executive_approvals"
down_revision: str | None = "0049_genesis_catalogue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("executive_runs", sa.Column("approvals_json", sa.JSON(), nullable=True))
    op.add_column("executive_runs", sa.Column("awaiting_step", sa.String(8), nullable=True))
    op.add_column(
        "executive_steps",
        sa.Column("repeat_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("executive_steps", "repeat_json")
    op.drop_column("executive_runs", "awaiting_step")
    op.drop_column("executive_runs", "approvals_json")
