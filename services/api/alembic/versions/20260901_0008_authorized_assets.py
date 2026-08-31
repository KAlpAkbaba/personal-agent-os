"""Authorized asset registry + security findings (M8).

Revision ID: 0008_authorized_assets
Revises: 0007_evolution
Create Date: 2026-09-01

Reversible. Lead-authored frozen foundation for M8 — ORM models in
app/security/models.py must match.

The registry is the single source of truth for "is this target/action within
the owner's stored authorization scope?" (SECURITY_MODEL §7-8). It is also the
verification source the M7 evolution permission model already depends on
(ADR-0025 security addendum): an `AuthorizationProvider` backed by this table
replaces the deny-everything default.

Design notes:
- An asset records WHAT is authorized (locator/CIDR/device identity), WHICH
  testing classes are allowed, the disruption/maintenance constraints, and the
  authorization evidence metadata + validity window. Scope questions are
  answered from this row, never from a caller's claim.
- `security_assessments` are the runs; `security_findings` are what they
  produced. Both link to the asset so the audit trail can show that every
  action stayed inside a recorded scope.
- `authorization_events` is append-only: enrollment, scope change, revocation
  and every REFUSED out-of-scope attempt, so silent scope drift is impossible
  to hide.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_authorized_assets"
down_revision: str | None = "0007_evolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ASSET_KINDS = ("host", "network", "device", "service", "domain", "repository")
_ENVIRONMENTS = ("lab", "dev", "staging", "prod")
_ASSET_STATUSES = ("active", "suspended", "revoked", "expired")
_ASSESSMENT_STATUSES = ("planned", "running", "completed", "failed", "refused")
_SEVERITIES = ("info", "low", "medium", "high", "critical")
_FINDING_STATUSES = ("open", "remediating", "remediated", "accepted", "false_positive")
_EVENT_ACTIONS = (
    "enrolled",
    "scope_changed",
    "suspended",
    "revoked",
    "expired",
    "assessment_authorized",
    "assessment_refused",
    "remediation_authorized",
    "remediation_refused",
)


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    op.create_table(
        "authorized_assets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("asset_ref", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("locator", sa.String(length=512), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False, server_default="lab"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column(
            "allowed_testing_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "allowed_permissions_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "constraints_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "evidence_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in_list("kind", _ASSET_KINDS), name="ck_authorized_assets_kind"),
        sa.CheckConstraint(
            _in_list("environment", _ENVIRONMENTS), name="ck_authorized_assets_env"
        ),
        sa.CheckConstraint(
            _in_list("status", _ASSET_STATUSES), name="ck_authorized_assets_status"
        ),
        sa.UniqueConstraint("asset_ref", name="uq_authorized_assets_asset_ref"),
    )
    op.create_index("ix_authorized_assets_status", "authorized_assets", ["status"])
    op.create_index("ix_authorized_assets_kind", "authorized_assets", ["kind"])

    op.create_table(
        "security_assessments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("authorized_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("testing_class", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="planned"),
        sa.Column("target", sa.String(length=512), nullable=False),
        sa.Column(
            "scope_decision_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "result_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            _in_list("status", _ASSESSMENT_STATUSES), name="ck_security_assessments_status"
        ),
    )
    op.create_index("ix_security_assessments_asset_id", "security_assessments", ["asset_id"])
    op.create_index("ix_security_assessments_status", "security_assessments", ["status"])

    op.create_table(
        "security_findings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "assessment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("security_assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("authorized_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="open"),
        sa.Column(
            "evidence_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "remediation_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            _in_list("severity", _SEVERITIES), name="ck_security_findings_severity"
        ),
        sa.CheckConstraint(
            _in_list("status", _FINDING_STATUSES), name="ck_security_findings_status"
        ),
        sa.UniqueConstraint(
            "asset_id", "fingerprint", name="uq_security_findings_asset_fingerprint"
        ),
    )
    op.create_index("ix_security_findings_assessment_id", "security_findings", ["assessment_id"])
    op.create_index("ix_security_findings_asset_id", "security_findings", ["asset_id"])

    op.create_table(
        "authorization_events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("asset_ref", sa.String(length=128), nullable=True),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("authorized_assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("requested_target", sa.String(length=512), nullable=True),
        sa.Column("testing_class", sa.String(length=64), nullable=True),
        sa.Column("allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(length=512), nullable=False, server_default=""),
        sa.Column(
            "detail_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            _in_list("action", _EVENT_ACTIONS), name="ck_authorization_events_action"
        ),
    )
    op.create_index("ix_authorization_events_action", "authorization_events", ["action"])
    op.create_index("ix_authorization_events_asset_id", "authorization_events", ["asset_id"])


def downgrade() -> None:
    op.drop_table("authorization_events")
    op.drop_table("security_findings")
    op.drop_table("security_assessments")
    op.drop_table("authorized_assets")
