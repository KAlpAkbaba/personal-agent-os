"""Browser research pipeline + device metadata/health (M13 track C).

Revision ID: 0012_browser_research
Revises: 0011_realtime_voice
Create Date: 2026-09-03

Reversible. ORM models: app/broker/models.py (Device.metadata_json,
Device.software_version), app/research/models.py (ResearchRunRow,
ResearchCandidateRow, ResearchEvidenceRow, ResearchReportRow).

Why: the devices layer (app.devices) needs somewhere durable to keep owner
aliases/labels/policy per device (``devices.metadata_json``) and the
software_version last reported by a connected agent (``hello.software_version``,
today only recorded per-session); the browser-research workflow needs durable
rows so a worker/API restart cannot lose plan, discovered candidates, fetched
evidence or the synthesized report mid-run (spec §5).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_browser_research"
down_revision: str | None = "0011_realtime_voice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STAGES = (
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


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    # ---------------------------------------------------------------- devices
    op.add_column(
        "devices",
        sa.Column("metadata_json", json_type, nullable=False, server_default="{}"),
    )
    op.add_column(
        "devices",
        sa.Column("software_version", sa.String(length=64), nullable=True),
    )

    # ------------------------------------------------------------ research_runs
    op.create_table(
        "research_runs",
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("plan_json", json_type, nullable=True),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("stage", sa.String(length=32), nullable=False, server_default="planned"),
        sa.Column("progress_json", json_type, nullable=False, server_default="{}"),
        sa.Column("events_json", json_type, nullable=False, server_default="[]"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(_in_list("stage", _STAGES), name="ck_research_runs_stage"),
    )
    op.create_index("ix_research_runs_device_id", "research_runs", ["device_id"])

    # ------------------------------------------------------- research_candidates
    op.create_table(
        "research_candidates",
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
        sa.Column("publisher", sa.String(length=256), nullable=True),
        sa.Column("discovered_by", sa.String(length=64), nullable=False),
        sa.Column("query_id", sa.String(length=64), nullable=False),
        sa.Column("published_hint", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("task_id", "url", name="uq_research_candidates_task_url"),
    )
    op.create_index("ix_research_candidates_task_id", "research_candidates", ["task_id"])

    # --------------------------------------------------------- research_evidence
    op.create_table(
        "research_evidence",
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
        sa.Column("evidence_json", json_type, nullable=False, server_default="{}"),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "injection_suspected", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("syndicated_of", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("task_id", "url", name="uq_research_evidence_task_url"),
    )
    op.create_index("ix_research_evidence_task_id", "research_evidence", ["task_id"])

    # ---------------------------------------------------------- research_reports
    op.create_table(
        "research_reports",
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("report_json", json_type, nullable=False, server_default="{}"),
        sa.Column("synthesis_provider", sa.String(length=32), nullable=False),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("research_reports")
    op.drop_index("ix_research_evidence_task_id", table_name="research_evidence")
    op.drop_table("research_evidence")
    op.drop_index("ix_research_candidates_task_id", table_name="research_candidates")
    op.drop_table("research_candidates")
    op.drop_index("ix_research_runs_device_id", table_name="research_runs")
    op.drop_table("research_runs")
    op.drop_column("devices", "software_version")
    op.drop_column("devices", "metadata_json")
