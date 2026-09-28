"""The durable browser task (ADR-0207, PR-B).

Revision ID: 0062_web_tasks
Revises: 0061_voice_macros
Create Date: 2026-09-29

Chains from ``0061_voice_macros`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why a table.** A browser task is a loop of rounds that outlives one tool call and one
worker: it stops for the owner (a login, a read-back, something it cannot see) and takes
up the same round when he answers, and its trail is the record of what was done on which
site. The row is the truth; the Temporal workflow only drives it.

**Expand-only and reversible.** One new table; the downgrade drops it. Nothing existing
is touched, so both colours of a blue-green release serve beside it.

ORM model: app/webtask/models.py::WebTaskRow.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0062_web_tasks"
down_revision: str | None = "0061_voice_macros"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "web_tasks"
_INDEX = "ix_web_tasks_status_created"
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("goal", sa.String(length=600), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("waiting_for", sa.String(length=24), nullable=False, server_default=""),
        sa.Column("round_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failure", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("message", sa.String(length=1200), nullable=False, server_default=""),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="voice"),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("attended", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("read_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_back_session_id", sa.String(length=64), nullable=True),
        sa.Column("read_back_turn", sa.Integer(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("state_json", _JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(_INDEX, _TABLE, ["status", "created_at"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
