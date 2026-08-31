"""Research + artifact schema (M3): tasks, task_runs, artifacts,
artifact_versions, artifact_renders, research_sources.

Revision ID: 0003_research_artifact
Revises: 0002_device_broker
Create Date: 2026-08-31

Reversible: downgrade drops all M3 tables and leaves the M0/M1 schema intact.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_research_artifact"
down_revision: str | None = "0002_device_broker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TASK_STATUSES = (
    "CREATED",
    "PLANNED",
    "RUNNING",
    "WAITING_EXTERNAL",
    "RENDERING",
    "READY",
    "PRESENTING",
    "COMPLETED",
    "FAILED_RECOVERABLE",
    "FAILED_TERMINAL",
)

_ARTIFACT_STATES = (
    "DRAFT",
    "CANONICAL_READY",
    "RENDERS_PENDING",
    "READY",
    "ARCHIVED",
)


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CREATED"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_from_device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_id", sa.String(length=255), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("error_class", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in_list("status", _TASK_STATUSES), name="ck_tasks_status"),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_conversation_id", "tasks", ["conversation_id"])

    op.create_table(
        "task_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("plan_json", postgresql.JSONB(), nullable=True),
        sa.Column("telemetry_json", postgresql.JSONB(), nullable=True),
        sa.Column("error_class", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("task_id", "attempt", name="uq_task_runs_task_attempt"),
    )
    op.create_index("ix_task_runs_task_id", "task_runs", ["task_id"])

    op.create_table(
        "artifacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column(
            "kind", sa.String(length=64), nullable=False, server_default="research_report"
        ),
        sa.Column(
            "canonical_format", sa.String(length=32), nullable=False, server_default="markdown"
        ),
        sa.Column("executive_summary", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(_in_list("state", _ARTIFACT_STATES), name="ck_artifacts_state"),
    )
    op.create_index("ix_artifacts_task_id", "artifacts", ["task_id"])
    op.create_index("ix_artifacts_state", "artifacts", ["state"])
    op.create_index("ix_artifacts_conversation_id", "artifacts", ["conversation_id"])
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])

    op.create_table(
        "artifact_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("canonical_body", sa.Text(), nullable=False),
        sa.Column("canonical_object_key", sa.String(length=512), nullable=True),
        sa.Column("source_manifest_json", postgresql.JSONB(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("artifact_id", "version", name="uq_artifact_versions_version"),
    )
    op.create_index("ix_artifact_versions_artifact_id", "artifact_versions", ["artifact_id"])

    op.create_table(
        "artifact_renders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "artifact_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifact_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "artifact_version_id", "format", name="uq_artifact_renders_format"
        ),
    )
    op.create_index(
        "ix_artifact_renders_artifact_version_id", "artifact_renders", ["artifact_version_id"]
    )

    op.create_table(
        "research_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=1000), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False, server_default=""),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_research_sources_task_id", "research_sources", ["task_id"])


def downgrade() -> None:
    op.drop_table("research_sources")
    op.drop_table("artifact_renders")
    op.drop_table("artifact_versions")
    op.drop_table("artifacts")
    op.drop_table("task_runs")
    op.drop_table("tasks")
