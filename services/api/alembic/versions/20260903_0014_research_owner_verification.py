"""M13 §5a: research_runs.stage widens for the owner-handoff state.

Revision ID: 0014_research_owner_verification
Revises: 0013_research_query_id_text
Create Date: 2026-09-03

The persistent-session/owner-handoff design (M13_RESEARCH_SPEC.md §5a) adds a
new run stage, ``waiting_for_owner_verification``, entered when Google (or
another provider) shows an interstitial during an interactive run and the
worker hands the Chrome window back to the owner instead of solving or
falling back. ``ck_research_runs_stage`` must allow it or every such
transition fails the CHECK constraint.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0014_research_owner_verification"
down_revision = "0013_research_query_id_text"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STAGES = (
    "planned",
    "selecting_device",
    "discovering",
    "fetching",
    "ranking",
    "synthesizing",
    "persisting",
    "ready",
    "failed",
    "cancelled",
)
_NEW_STAGES = (
    "planned",
    "selecting_device",
    "discovering",
    "waiting_for_owner_verification",
    "fetching",
    "ranking",
    "synthesizing",
    "persisting",
    "ready",
    "failed",
    "cancelled",
)


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    # compat: widening (the CHECK is replaced by a superset; M18.4 spec §10)
    op.drop_constraint("ck_research_runs_stage", "research_runs", type_="check")
    op.create_check_constraint(
        "ck_research_runs_stage",
        "research_runs",
        sa.text(_in_list("stage", _NEW_STAGES)),
    )


def downgrade() -> None:
    op.drop_constraint("ck_research_runs_stage", "research_runs", type_="check")
    op.create_check_constraint(
        "ck_research_runs_stage",
        "research_runs",
        sa.text(_in_list("stage", _OLD_STAGES)),
    )
