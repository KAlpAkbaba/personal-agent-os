"""The undo journal of managed file mutations (B34 req 160, 162, 163, 164, 166).

Revision ID: 0047_file_mutations
Revises: 0046_research_owner_preferences
Create Date: 2026-09-15

Chains from ``0046_research_owner_preferences`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why a table.** Every change the system makes to one of the owner's files is a row here
BEFORE it is proposed to the owner and AFTER the device carried it out: what was there
(path, sha256, size), what is there now, the backup the device kept, and the inverse plan
that undoes it. The rows for one path ARE its version history; the pending rows ARE the
approval queue the Cockpit lists beside mail drafts and calendar proposals; a receipt
without a row is not a receipt.

**Expand-only and reversible.** One new table; the downgrade drops it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_file_mutations"
down_revision: str | None = "0046_research_owner_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_mutations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.String(200), nullable=False),
        sa.Column("session_id", sa.String(200), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("risk", sa.String(16), nullable=False),
        sa.Column("file_id", sa.String(200), nullable=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("path_before", sa.String(1024), nullable=True),
        sa.Column("path_after", sa.String(1024), nullable=True),
        sa.Column("sha_before", sa.String(64), nullable=True),
        sa.Column("sha_after", sa.String(64), nullable=True),
        sa.Column("size_before", sa.BigInteger(), nullable=True),
        sa.Column("size_after", sa.BigInteger(), nullable=True),
        sa.Column("backup_id", sa.String(64), nullable=True),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("undo_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("summary", sa.String(512), nullable=False),
        sa.Column("error_class", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(1024), nullable=True),
        sa.Column("undo_of", sa.Uuid(), nullable=True),
        sa.Column("read_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_back_session_id", sa.String(200), nullable=True),
        sa.Column("read_back_turn", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_file_mutations_state_created", "file_mutations", ["state", "created_at"])
    op.create_index("ix_file_mutations_path_after", "file_mutations", ["path_after"])
    op.create_index("ix_file_mutations_path_before", "file_mutations", ["path_before"])


def downgrade() -> None:
    op.drop_index("ix_file_mutations_path_before", table_name="file_mutations")
    op.drop_index("ix_file_mutations_path_after", table_name="file_mutations")
    op.drop_index("ix_file_mutations_state_created", table_name="file_mutations")
    op.drop_table("file_mutations")
