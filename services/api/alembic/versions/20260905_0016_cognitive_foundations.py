"""M17 cognitive foundations: experience, goals, self model, evolution backlog.

Revision ID: 0016_cognitive_foundations
Revises: 0015_activity_ledger
Create Date: 2026-09-05

Reversible. ORM models: app/goals/models.py, app/experience/models.py,
app/selfmodel/models.py, app/evolution/models.py.

Why (docs/M17_COGNITIVE_FOUNDATIONS_SPEC.md): the ledger records what happened;
these tables record what was LEARNED from it (experience_lessons), what the system
is trying to achieve (goals, goal_tasks), what the system IS and which of the four
truths each part of it stands on (code_modules, code_symbols, code_edges,
module_provenance), and what it proposes to become (evolution_opportunities, whose
lifecycle only an owner may advance past OWNER_APPROVED).

One migration for all four packages because they ship as one milestone and an
operator should not have to sequence them; each table is independent, so the
downgrade simply drops them in reverse.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_cognitive_foundations"
down_revision: str | None = "0015_activity_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "goals",
        sa.Column("goal_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "parent_goal_id",
            sa.Uuid(),
            sa.ForeignKey("goals.goal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("horizon", sa.String(length=16), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("success_criteria", json_type, nullable=False),
        sa.Column("blockers", json_type, nullable=False),
        sa.Column("depends_on", json_type, nullable=False),
        sa.Column("requires_owner_approval", sa.Boolean(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_refs", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=256), nullable=False),
        sa.Column("detail_json", json_type, nullable=False),
        sa.UniqueConstraint("source", "source_ref", name="uq_goals_source_ref"),
    )
    op.create_index("ix_goals_status", "goals", ["status"], unique=False)
    op.create_index("ix_goals_parent_goal_id", "goals", ["parent_goal_id"], unique=False)

    op.create_table(
        "goal_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "goal_id", sa.Uuid(), sa.ForeignKey("goals.goal_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("goal_id", "task_id", name="uq_goal_tasks_goal_task"),
    )
    op.create_index("ix_goal_tasks_task_id", "goal_tasks", ["task_id"], unique=False)
    op.create_index("ix_goal_tasks_goal_id", "goal_tasks", ["goal_id"], unique=False)

    op.create_table(
        "experience_lessons",
        sa.Column("lesson_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("incident_refs", json_type, nullable=False),
        sa.Column("evidence_refs", json_type, nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("recurrence", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("generalizability", sa.Float(), nullable=False),
        sa.Column("owner_relevance", sa.Float(), nullable=False),
        sa.Column("risk_overgeneralization", sa.Float(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("promoted_memory_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=256), nullable=False),
        sa.Column("detail_json", json_type, nullable=False),
        sa.UniqueConstraint("source", "source_ref", name="uq_experience_lessons_source_ref"),
    )
    op.create_index("ix_experience_lessons_status", "experience_lessons", ["status"], unique=False)

    op.create_table(
        "code_modules",
        sa.Column("module_id", sa.String(length=200), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=400), nullable=False),
        sa.Column("language", sa.String(length=24), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("adr_refs", json_type, nullable=False),
        sa.Column("spec_refs", json_type, nullable=False),
        sa.Column("owner_area", sa.String(length=64), nullable=False),
        sa.Column("production_state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail_json", json_type, nullable=False),
    )
    op.create_index("ix_code_modules_kind", "code_modules", ["kind"], unique=False)
    op.create_index(
        "ix_code_modules_production_state", "code_modules", ["production_state"], unique=False
    )
    op.create_index("ix_code_modules_owner_area", "code_modules", ["owner_area"], unique=False)

    op.create_table(
        "code_symbols",
        sa.Column("symbol_id", sa.String(length=320), primary_key=True, nullable=False),
        sa.Column(
            "module_id",
            sa.String(length=200),
            sa.ForeignKey("code_modules.module_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("lineno", sa.Integer(), nullable=False),
        sa.Column("docstring_summary", sa.Text(), nullable=True),
        sa.Column("tags", json_type, nullable=False),
    )
    op.create_index("ix_code_symbols_kind", "code_symbols", ["kind"], unique=False)
    op.create_index("ix_code_symbols_name", "code_symbols", ["name"], unique=False)
    op.create_index("ix_code_symbols_module_id", "code_symbols", ["module_id"], unique=False)

    op.create_table(
        "code_edges",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("from_module", sa.String(length=200), nullable=False),
        sa.Column("to_module", sa.String(length=400), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("detail_json", json_type, nullable=False),
        sa.UniqueConstraint("from_module", "to_module", "kind", name="uq_code_edges_triple"),
    )
    op.create_index("ix_code_edges_from_module", "code_edges", ["from_module"], unique=False)
    op.create_index("ix_code_edges_kind", "code_edges", ["kind"], unique=False)
    op.create_index("ix_code_edges_to_module", "code_edges", ["to_module"], unique=False)

    op.create_table(
        "module_provenance",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("module_id", sa.String(length=200), nullable=False),
        sa.Column("truth_kind", sa.String(length=16), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("digest", sa.String(length=128), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_refs", json_type, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("module_id", "truth_kind", name="uq_module_provenance_module_truth"),
    )
    op.create_index(
        "ix_module_provenance_truth_kind", "module_provenance", ["truth_kind"], unique=False
    )
    op.create_index(
        "ix_module_provenance_module_id", "module_provenance", ["module_id"], unique=False
    )

    op.create_table(
        "evolution_opportunities",
        sa.Column("opportunity_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("origin_json", json_type, nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("owner_relevance", sa.Float(), nullable=False),
        sa.Column("expected_utility", sa.Float(), nullable=False),
        sa.Column("recurrence", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("engineering_cost", sa.Float(), nullable=False),
        sa.Column("operational_risk", sa.Float(), nullable=False),
        sa.Column("composite", sa.Float(), nullable=False),
        sa.Column("workspace_ref", sa.String(length=512), nullable=True),
        sa.Column("candidate_ref", sa.String(length=512), nullable=True),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=256), nullable=False),
        sa.Column("detail_json", json_type, nullable=False),
        sa.UniqueConstraint("source", "source_ref", name="uq_evolution_opportunities_source_ref"),
    )
    op.create_index(
        "ix_evolution_opportunities_status", "evolution_opportunities", ["status"], unique=False
    )
    op.create_index(
        "ix_evolution_opportunities_status_composite",
        "evolution_opportunities",
        ["status", "composite"],
        unique=False,
    )
    op.create_index(
        "ix_evolution_opportunities_composite",
        "evolution_opportunities",
        ["composite"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("evolution_opportunities")
    op.drop_table("module_provenance")
    op.drop_table("code_edges")
    op.drop_table("code_symbols")
    op.drop_table("code_modules")
    op.drop_table("experience_lessons")
    op.drop_table("goal_tasks")
    op.drop_table("goals")
