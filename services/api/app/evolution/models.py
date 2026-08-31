"""Evolution ORM models (M7) — mirror of the frozen migration 0007_evolution.

Canonical schema lives in alembic/versions/20260901_0007_evolution.py
(lead-authored, applied, FROZEN). Column types are portable (generic Uuid,
JSON with a JSONB variant) like every other module so the service layer
unit-tests on SQLite while integration tests run the real PostgreSQL schema.

Semantics baked in (EVOLUTION_ENGINE_SPEC §2/§10, ADR-0025):
- `capabilities` is the machine-readable registry; dispatch may only resolve a
  capability whose status is `production` AND whose current_skill_version_id
  points at a `registered` skill version;
- `skill_versions` is append-only history: a candidate reaches `registered`
  only after generated tests + evals + INDEPENDENT review pass; a rejected
  candidate keeps its row (with a reason) and never touches production;
- `capability_gaps` stores the gap-detection decision trail, which is what
  makes "composition was attempted first" auditable evidence, not a claim.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

CAPABILITY_STATUSES = ("proposed", "experimental", "production", "deprecated")
SKILL_VERSION_STATUSES = (
    "draft",
    "built",
    "tested",
    "reviewed",
    "evaluated",
    "registered",
    "rejected",
    "superseded",
)
GAP_RESOLUTIONS = (
    "existing_capability",
    "composition",
    "configuration",
    "extension",
    "generation",
    "product_change_required",
    "unresolved",
)
GAP_STATUSES = ("open", "resolving", "resolved", "abandoned")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class Capability(Base):
    __tablename__ = "capabilities"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    capability_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="0.0.0")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="proposed", index=True
    )
    manifest_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    current_skill_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in_list("status", CAPABILITY_STATUSES), name="ck_capabilities_status"),
        UniqueConstraint("capability_id", name="uq_capabilities_capability_id"),
    )


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    capability_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    source_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evaluation_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    review_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    rejected_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            _in_list("status", SKILL_VERSION_STATUSES), name="ck_skill_versions_status"
        ),
        UniqueConstraint(
            "capability_id", "version", name="uq_skill_versions_capability_version"
        ),
    )


class CapabilityGap(Base):
    __tablename__ = "capability_gaps"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    requested_capability: Mapped[str] = mapped_column(String(256), nullable=False)
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)
    resolution: Mapped[str] = mapped_column(String(32), nullable=False, default="unresolved")
    decision_trail_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONColumn, nullable=False, default=list
    )
    resolved_skill_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("skill_versions.id", ondelete="SET NULL"), nullable=True
    )
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(_in_list("status", GAP_STATUSES), name="ck_capability_gaps_status"),
        CheckConstraint(
            _in_list("resolution", GAP_RESOLUTIONS), name="ck_capability_gaps_resolution"
        ),
    )


__all__ = [
    "CAPABILITY_STATUSES",
    "GAP_RESOLUTIONS",
    "GAP_STATUSES",
    "SKILL_VERSION_STATUSES",
    "Capability",
    "CapabilityGap",
    "SkillVersion",
]
