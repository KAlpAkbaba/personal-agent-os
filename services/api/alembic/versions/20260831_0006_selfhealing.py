"""Self-healing schema (M6): releases, incidents.

Revision ID: 0006_selfhealing
Revises: 0005_memory
Create Date: 2026-08-31

Reversible. Lead-authored frozen foundation for M6 — ORM models in
app/selfhealing/models.py must match. Releases are immutable records of
versioned deployments (never in-place overwrite); incidents carry a stable
fingerprint for dedup and link the introducing/fixing release.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_selfhealing"
down_revision: str | None = "0005_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RELEASE_STATUSES = (
    "candidate",
    "staging",
    "active",
    "rejected",
    "rolled_back",
    "superseded",
)
_INCIDENT_STATUSES = ("open", "recovered", "fix_in_progress", "fixed", "closed")
_SEVERITIES = ("info", "warning", "critical")


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    op.create_table(
        "releases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("component", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("git_commit", sa.String(length=64), nullable=True),
        sa.Column("manifest_digest", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="candidate"),
        sa.Column(
            "health_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in_list("status", _RELEASE_STATUSES), name="ck_releases_status"),
        sa.UniqueConstraint("component", "version", name="uq_releases_component_version"),
    )
    op.create_index("ix_releases_component", "releases", ["component"])
    op.create_index("ix_releases_status", "releases", ["status"])

    op.create_table(
        "incidents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("component", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="warning"),
        sa.Column("fingerprint", sa.String(length=128), nullable=False),
        sa.Column(
            "evidence_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="open"),
        sa.Column(
            "introduced_release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("releases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "fixed_release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("releases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint(_in_list("status", _INCIDENT_STATUSES), name="ck_incidents_status"),
        sa.CheckConstraint(_in_list("severity", _SEVERITIES), name="ck_incidents_severity"),
        sa.UniqueConstraint("component", "fingerprint", name="uq_incidents_component_fingerprint"),
    )
    op.create_index("ix_incidents_component", "incidents", ["component"])
    op.create_index("ix_incidents_status", "incidents", ["status"])
    op.create_index("ix_incidents_fingerprint", "incidents", ["fingerprint"])


def downgrade() -> None:
    op.drop_table("incidents")
    op.drop_table("releases")
