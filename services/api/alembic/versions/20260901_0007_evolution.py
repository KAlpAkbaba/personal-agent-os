"""Evolution schema (M7): capabilities, skill_versions, capability_gaps.

Revision ID: 0007_evolution
Revises: 0006_selfhealing
Create Date: 2026-09-01

Reversible. Lead-authored frozen foundation for M7 — ORM models in
app/evolution/models.py must match.

Design notes baked in:
- `capabilities` is the machine-readable registry (EVOLUTION_ENGINE_SPEC §2);
  the runtime may only dispatch to capabilities whose status is `production`
  and whose current_skill_version_id points at a passing evaluation.
- `skill_versions` records each generated/extended skill version with its
  git/worktree provenance, evaluation results and gate status. A version
  becomes `registered` ONLY after tests + independent review + evals pass;
  rejected candidates stay for history and never affect production.
- `capability_gaps` records the gap-detection decision trail (compose vs
  configure vs extend vs generate) so "composition was attempted first" is
  auditable rather than a claim.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_evolution"
down_revision: str | None = "0006_selfhealing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CAPABILITY_STATUSES = ("proposed", "experimental", "production", "deprecated")
_SKILL_STATUSES = (
    "draft",
    "built",
    "tested",
    "reviewed",
    "evaluated",
    "registered",
    "rejected",
    "superseded",
)
_GAP_RESOLUTIONS = (
    "existing_capability",
    "composition",
    "configuration",
    "extension",
    "generation",
    "product_change_required",
    "unresolved",
)
_GAP_STATUSES = ("open", "resolving", "resolved", "abandoned")


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    op.create_table(
        "capabilities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("capability_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False, server_default="0.0.0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="proposed"),
        sa.Column(
            "manifest_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("current_skill_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            _in_list("status", _CAPABILITY_STATUSES), name="ck_capabilities_status"
        ),
        sa.UniqueConstraint("capability_id", name="uq_capabilities_capability_id"),
    )
    op.create_index("ix_capabilities_status", "capabilities", ["status"])

    op.create_table(
        "skill_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("capability_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("source_ref", sa.String(length=512), nullable=True),
        sa.Column("git_commit", sa.String(length=64), nullable=True),
        sa.Column("manifest_digest", sa.String(length=128), nullable=True),
        sa.Column(
            "evaluation_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "review_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("rejected_reason", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in_list("status", _SKILL_STATUSES), name="ck_skill_versions_status"),
        sa.UniqueConstraint(
            "capability_id", "version", name="uq_skill_versions_capability_version"
        ),
    )
    op.create_index("ix_skill_versions_capability_id", "skill_versions", ["capability_id"])
    op.create_index("ix_skill_versions_status", "skill_versions", ["status"])

    op.create_table(
        "capability_gaps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("requested_capability", sa.String(length=256), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("resolution", sa.String(length=32), nullable=False, server_default="unresolved"),
        sa.Column(
            "decision_trail_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "resolved_skill_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skill_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in_list("status", _GAP_STATUSES), name="ck_capability_gaps_status"),
        sa.CheckConstraint(
            _in_list("resolution", _GAP_RESOLUTIONS), name="ck_capability_gaps_resolution"
        ),
    )
    op.create_index("ix_capability_gaps_status", "capability_gaps", ["status"])
    op.create_index("ix_capability_gaps_task_id", "capability_gaps", ["task_id"])


def downgrade() -> None:
    op.drop_table("capability_gaps")
    op.drop_table("skill_versions")
    op.drop_table("capabilities")
