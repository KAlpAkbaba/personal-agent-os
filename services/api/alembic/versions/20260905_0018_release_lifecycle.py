"""M18: the owner-authorised release lifecycle states.

ADR-0055 §5. The backlog gains the states that make an owner-authorised deployment
watchable and its failure recoverable:

    SHADOW_READY -> OWNER_APPROVAL_REQUIRED -> OWNER_AUTHORIZED -> QUALIFYING
                 -> DEPLOYING -> VERIFYING -> LIVE
      failure:      DEPLOYING|VERIFYING -> FAILED -> ROLLING_BACK -> ROLLED_BACK

``owner_approved`` is kept: rows carry it and it means exactly what ``owner_authorized``
now means. Dropping it would rewrite history to look like the new design.

The CHECK constraint is the database's half of the lifecycle law - the transition table in
``app/evolution/backlog.py`` is the application's half, and they assert against each other
at import time. This migration keeps them in step.

Revision ID: 0018_release_lifecycle
Revises: 0017_cognitive_ts_defaults
"""

from __future__ import annotations

from alembic import op

revision: str = "0018_release_lifecycle"
down_revision: str | None = "0017_cognitive_ts_defaults"
branch_labels = None
depends_on = None

_TABLE = "evolution_opportunities"
_CONSTRAINT = "ck_evolution_opportunities_status"

_BEFORE = (
    "idea", "researching", "design_ready", "building", "testing", "evaluating",
    "shadow_ready", "owner_approved", "qualifying", "live", "rejected", "superseded",
    "quarantined", "rolled_back",
)
_AFTER = (
    "idea", "researching", "design_ready", "building", "testing", "evaluating",
    "shadow_ready", "owner_approval_required", "owner_approved", "owner_authorized",
    "qualifying", "deploying", "verifying", "live", "failed", "rolling_back",
    "rejected", "superseded", "quarantined", "rolled_back",
)


def _in_list(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"status IN ({joined})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _in_list(_AFTER))


def downgrade() -> None:
    # Rows in a state the old constraint does not know would block the downgrade, which is
    # the correct outcome: a release halfway through DEPLOYING must not be quietly
    # relabelled to fit an older vocabulary. Park them first if this ever has to run.
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _in_list(_BEFORE))
