"""Self-healing ORM models (frozen foundation).

Canonical schema lives in alembic/versions/20260831_0006_selfhealing.py
(lead-authored, applied). Types are portable (generic Uuid, JSON with JSONB
variant) like the other modules so the service layer unit-tests on SQLite.

Semantics baked in (M6 brief):
- releases are immutable records of versioned deployments — never in-place
  overwrite; (component, version) is unique;
- incidents carry a stable fingerprint (sha256 over component + error class +
  failing check) for dedup: a recurring failure increments occurrence_count on
  the same row instead of creating a new incident;
- introduced_release_id / fixed_release_id link an incident to the release
  that shipped the bug and the candidate that fixed it.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
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

RELEASE_STATUSES = (
    "candidate",
    "staging",
    "active",
    "rejected",
    "rolled_back",
    "superseded",
)
INCIDENT_STATUSES = ("open", "recovered", "fix_in_progress", "fixed", "closed")
SEVERITIES = ("info", "warning", "critical")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class Release(Base):
    __tablename__ = "releases"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    component: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="candidate", index=True
    )
    health_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(_in_list("status", RELEASE_STATUSES), name="ck_releases_status"),
        UniqueConstraint("component", "version", name="uq_releases_component_version"),
    )


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    component: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open", index=True)
    introduced_release_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("releases.id", ondelete="SET NULL"), nullable=True
    )
    fixed_release_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("releases.id", ondelete="SET NULL"), nullable=True
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        CheckConstraint(_in_list("status", INCIDENT_STATUSES), name="ck_incidents_status"),
        CheckConstraint(_in_list("severity", SEVERITIES), name="ck_incidents_severity"),
        UniqueConstraint("component", "fingerprint", name="uq_incidents_component_fingerprint"),
    )
