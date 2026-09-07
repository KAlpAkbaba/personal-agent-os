"""M18.2 tool-call result contract: a clarification is its own terminal status (ADR-0077).

Revision ID: 0023_tool_call_clarification
Revises: 0022_research_focus
Create Date: 2026-09-07

Reversible. ORM model: app/voice/realtime_sessions/models.py::RealtimeToolCall.

``realtime_tool_calls.status`` gains ``needs_clarification``: the terminal status of a
research follow-up (``research.explain`` / ``research.sources`` /
``research.finding_detail``, and ``activity.explain`` on a research-bound turn) whose
target could not be resolved from the turn. Until now such a call was recorded as
``succeeded`` with no ``research_job_id`` and only a question for a result - the "empty
success" of the owner's 2026-09-06 record (session c3d88970, call call_UdBzeEH85slFqbNQ).
The column is widened from 16 to 32 characters because the new word does not fit in the
old width, and the CHECK constraint is re-stated with the four-word vocabulary.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_tool_call_clarification"
down_revision: str | None = "0022_research_focus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "realtime_tool_calls"
_CONSTRAINT = "ck_realtime_tool_calls_status"
_BEFORE = ("running", "succeeded", "failed")
_AFTER = (*_BEFORE, "needs_clarification")


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    # compat: widening (String(16) -> String(32) and a superset CHECK; M18.4 spec §10)
    op.alter_column(
        _TABLE,
        "status",
        type_=sa.String(length=32),
        existing_type=sa.String(length=16),
        existing_nullable=False,
        existing_server_default="running",
    )
    op.create_check_constraint(_CONSTRAINT, _TABLE, _in_list("status", _AFTER))


def downgrade() -> None:
    # A clarification row has no older word for what it is. "failed" is the honest relabel
    # (the call produced no answer) and it is applied here rather than blocking the
    # downgrade: these rows are session evidence, never state a release depends on.
    op.execute(f"UPDATE {_TABLE} SET status = 'failed' WHERE status = 'needs_clarification'")
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.alter_column(
        _TABLE,
        "status",
        type_=sa.String(length=16),
        existing_type=sa.String(length=32),
        existing_nullable=False,
        existing_server_default="running",
    )
    op.create_check_constraint(_CONSTRAINT, _TABLE, _in_list("status", _BEFORE))
