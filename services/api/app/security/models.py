"""Security ORM models (M8) — mirror of the frozen migration 0008_authorized_assets.

Canonical schema lives in alembic/versions/20260901_0008_authorized_assets.py
(lead-authored, applied, FROZEN). Column types are portable (generic Uuid, JSON
with a JSONB variant) like every other module, so the service layer unit-tests
on SQLite while integration tests run the real PostgreSQL schema.

Semantics baked in (SECURITY_MODEL §7-9, ADR-0026):
- `authorized_assets` is the single source of truth for scope. It records WHAT
  is authorized (locator/CIDR/device identity), WHICH testing classes are
  allowed, WHICH evolution permission grants the owner recorded for the asset,
  the disruption/collection constraints, the authorization evidence and a
  validity window. No caller claim ever substitutes for this row.
- `security_assessments` are runs; `security_findings` are what they produced.
  Both link to the asset, so the audit trail shows every action stayed inside a
  recorded scope. `(asset_id, fingerprint)` is unique, which makes a repeated
  in-scope run idempotent rather than duplicating findings.
- `authorization_events` is append-only: enrollment, scope change, suspension,
  revocation, expiry AND every refused out-of-scope attempt.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

# --------------------------------------------------------------- vocabularies
# Kept byte-identical to the frozen migration's CHECK constraint lists.

ASSET_KINDS = ("host", "network", "device", "service", "domain", "repository")
ENVIRONMENTS = ("lab", "dev", "staging", "prod")

ASSET_STATUS_ACTIVE = "active"
ASSET_STATUS_SUSPENDED = "suspended"
ASSET_STATUS_REVOKED = "revoked"
ASSET_STATUS_EXPIRED = "expired"
ASSET_STATUSES = (
    ASSET_STATUS_ACTIVE,
    ASSET_STATUS_SUSPENDED,
    ASSET_STATUS_REVOKED,
    ASSET_STATUS_EXPIRED,
)

ASSESSMENT_STATUS_PLANNED = "planned"
ASSESSMENT_STATUS_RUNNING = "running"
ASSESSMENT_STATUS_COMPLETED = "completed"
ASSESSMENT_STATUS_FAILED = "failed"
ASSESSMENT_STATUS_REFUSED = "refused"
ASSESSMENT_STATUSES = (
    ASSESSMENT_STATUS_PLANNED,
    ASSESSMENT_STATUS_RUNNING,
    ASSESSMENT_STATUS_COMPLETED,
    ASSESSMENT_STATUS_FAILED,
    ASSESSMENT_STATUS_REFUSED,
)

SEVERITY_INFO = "info"
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
SEVERITY_CRITICAL = "critical"
SEVERITIES = (SEVERITY_INFO, SEVERITY_LOW, SEVERITY_MEDIUM, SEVERITY_HIGH, SEVERITY_CRITICAL)
# Descending order for report ordering; index also used as a sort key.
SEVERITY_ORDER = {name: i for i, name in enumerate(SEVERITIES)}

FINDING_STATUS_OPEN = "open"
FINDING_STATUS_REMEDIATING = "remediating"
FINDING_STATUS_REMEDIATED = "remediated"
FINDING_STATUS_ACCEPTED = "accepted"
FINDING_STATUS_FALSE_POSITIVE = "false_positive"
FINDING_STATUSES = (
    FINDING_STATUS_OPEN,
    FINDING_STATUS_REMEDIATING,
    FINDING_STATUS_REMEDIATED,
    FINDING_STATUS_ACCEPTED,
    FINDING_STATUS_FALSE_POSITIVE,
)

EVENT_ENROLLED = "enrolled"
EVENT_SCOPE_CHANGED = "scope_changed"
EVENT_SUSPENDED = "suspended"
EVENT_REVOKED = "revoked"
EVENT_EXPIRED = "expired"
EVENT_ASSESSMENT_AUTHORIZED = "assessment_authorized"
EVENT_ASSESSMENT_REFUSED = "assessment_refused"
EVENT_REMEDIATION_AUTHORIZED = "remediation_authorized"
EVENT_REMEDIATION_REFUSED = "remediation_refused"
EVENT_ACTIONS = (
    EVENT_ENROLLED,
    EVENT_SCOPE_CHANGED,
    EVENT_SUSPENDED,
    EVENT_REVOKED,
    EVENT_EXPIRED,
    EVENT_ASSESSMENT_AUTHORIZED,
    EVENT_ASSESSMENT_REFUSED,
    EVENT_REMEDIATION_AUTHORIZED,
    EVENT_REMEDIATION_REFUSED,
)

# Events that represent the OWNER granting or changing authorization. The
# "no repeated approvals" acceptance bullet is proven by asserting the count of
# these does not grow while assessments run (SECURITY_MODEL §8).
AUTHORIZATION_GRANT_ACTIONS = (EVENT_ENROLLED, EVENT_SCOPE_CHANGED)

# Testing classes an asset may allow (SECURITY_MODEL §7 example keys).
TESTING_CLASS_CONFIGURATION_AUDIT = "configuration_audit"
TESTING_CLASS_VULNERABILITY_SCAN = "vulnerability_scan"
TESTING_CLASS_CONTROLLED_VALIDATION = "controlled_validation"
TESTING_CLASS_REMEDIATION = "remediation"
TESTING_CLASSES = (
    TESTING_CLASS_CONFIGURATION_AUDIT,
    TESTING_CLASS_VULNERABILITY_SCAN,
    TESTING_CLASS_CONTROLLED_VALIDATION,
    TESTING_CLASS_REMEDIATION,
)

# Only configuration_audit is IMPLEMENTED in M8. The other classes may be
# enrolled (the owner's authorization is recorded) but requesting one is
# refused as unimplemented rather than silently downgraded to something else.
IMPLEMENTED_TESTING_CLASSES = (TESTING_CLASS_CONFIGURATION_AUDIT, TESTING_CLASS_REMEDIATION)

# Disruption ladder used by the constraint check (constraints_json.max_disruption).
DISRUPTION_LEVELS = ("none", "low", "medium", "high")
DISRUPTION_ORDER = {name: i for i, name in enumerate(DISRUPTION_LEVELS)}

ARTIFACT_KIND_SECURITY_REPORT = "security_report"


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class AuthorizedAsset(Base):
    __tablename__ = "authorized_assets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    asset_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    locator: Mapped[str] = mapped_column(String(512), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="lab")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ASSET_STATUS_ACTIVE, index=True
    )
    allowed_testing_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    allowed_permissions_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    constraints_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    evidence_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(_in_list("kind", ASSET_KINDS), name="ck_authorized_assets_kind"),
        CheckConstraint(_in_list("environment", ENVIRONMENTS), name="ck_authorized_assets_env"),
        CheckConstraint(_in_list("status", ASSET_STATUSES), name="ck_authorized_assets_status"),
        UniqueConstraint("asset_ref", name="uq_authorized_assets_asset_ref"),
    )


class SecurityAssessment(Base):
    __tablename__ = "security_assessments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("authorized_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    testing_class: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ASSESSMENT_STATUS_PLANNED, index=True
    )
    target: Mapped[str] = mapped_column(String(512), nullable=False)
    scope_decision_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            _in_list("status", ASSESSMENT_STATUSES), name="ck_security_assessments_status"
        ),
    )


class SecurityFinding(Base):
    __tablename__ = "security_findings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("security_assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("authorized_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default=SEVERITY_INFO)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=FINDING_STATUS_OPEN)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    remediation_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(_in_list("severity", SEVERITIES), name="ck_security_findings_severity"),
        CheckConstraint(_in_list("status", FINDING_STATUSES), name="ck_security_findings_status"),
        UniqueConstraint(
            "asset_id", "fingerprint", name="uq_security_findings_asset_fingerprint"
        ),
    )


class AuthorizationEvent(Base):
    __tablename__ = "authorization_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    asset_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("authorized_assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    requested_target: Mapped[str | None] = mapped_column(String(512), nullable=True)
    testing_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in_list("action", EVENT_ACTIONS), name="ck_authorization_events_action"),
    )


__all__ = [
    "ARTIFACT_KIND_SECURITY_REPORT",
    "ASSESSMENT_STATUSES",
    "ASSET_KINDS",
    "ASSET_STATUSES",
    "AUTHORIZATION_GRANT_ACTIONS",
    "DISRUPTION_LEVELS",
    "DISRUPTION_ORDER",
    "ENVIRONMENTS",
    "EVENT_ACTIONS",
    "FINDING_STATUSES",
    "IMPLEMENTED_TESTING_CLASSES",
    "SEVERITIES",
    "SEVERITY_ORDER",
    "TESTING_CLASSES",
    "AuthorizationEvent",
    "AuthorizedAsset",
    "SecurityAssessment",
    "SecurityFinding",
]
