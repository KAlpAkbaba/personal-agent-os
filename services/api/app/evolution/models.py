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

`evolution_opportunities` (added later, for the Evolution Engine backlog) has
NO migration in this branch on purpose — the integrator owns the alembic
revision, so this module is the schema source until that lands. The table
creates from metadata, which is all the SQLite unit tests need. Its lifecycle
and the legal transitions between its statuses live in
`app/evolution/backlog.py`; the column list here deliberately keeps the six
scoring inputs as real columns rather than JSON so the backlog can be ordered
and filtered in SQL.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
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
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed", index=True)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
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
        UniqueConstraint("capability_id", "version", name="uq_skill_versions_capability_version"),
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


#: The Evolution Engine backlog lifecycle, in order. The legal transitions
#: between them (and which of them only an owner action may enter) are declared
#: once, in `app/evolution/backlog.py`; this tuple exists so the database
#: refuses an unknown status even if a caller bypasses the service layer.
OPPORTUNITY_STATUSES = (
    "idea",
    "researching",
    "design_ready",
    "building",
    "testing",
    "evaluating",
    "shadow_ready",
    # The owner-authorised release path (ADR-0055 §5, realised in M18). "owner_approved"
    # is kept because rows carry it and it means exactly what "owner_authorized" now means;
    # new work uses the explicit chain.
    "owner_approval_required",
    "owner_approved",
    "owner_authorized",
    "qualifying",
    "deploying",
    "verifying",
    "live",
    "failed",
    "rolling_back",
    "rejected",
    "superseded",
    "quarantined",
    "rolled_back",
)


class EvolutionOpportunity(Base):
    """One thing the system believes it could become better at.

    Created only from evidence (ledger events, incidents, experience lessons) —
    never from an unprompted idea — and advanced only along the legal
    transitions in `app/evolution/backlog.py`. `approved_by`/`approved_at` are
    the durable record of the single human authority gate: they are set by an
    owner action and by nothing else.
    """

    __tablename__ = "evolution_opportunities"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    #: The evidence that produced this opportunity:
    #: [{"kind": "ledger_event"|"incident"|"lesson", "ref": "<id>", ...}, ...].
    #: Never empty — an opportunity with no origin is refused at creation.
    origin_json: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        # 32, because "owner_approval_required" is 23 characters and this column was 24 in
        # the migration while the model said something else again. A status that does not
        # fit is not a typo the metadata-built SQLite of the unit suite will ever show you
        # (M18 release lifecycle, 2026-09-05).
        String(32),
        nullable=False,
        default="idea",
        index=True,
    )

    owner_relevance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expected_utility: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recurrence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    engineering_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    operational_risk: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    composite: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, index=True)

    #: Isolated lab workspace holding the candidate's source (never product source).
    workspace_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: The packaged candidate this opportunity produced, if it got that far.
    candidate_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: Set ONLY by an owner action carrying an owner-session capability.
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    #: Idempotency: "incident", "ledger_event", "lesson", "owner" ...
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The identifier within `source`; unique with it, so the same incident
    #: cannot spawn two competing opportunities.
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    #: Transition history, scoring rationale, refusal reasons, release refs.
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            _in_list("status", OPPORTUNITY_STATUSES), name="ck_evolution_opportunities_status"
        ),
        UniqueConstraint("source", "source_ref", name="uq_evolution_opportunities_source_ref"),
        Index("ix_evolution_opportunities_status_composite", "status", "composite"),
    )


__all__ = [
    "CAPABILITY_STATUSES",
    "GAP_RESOLUTIONS",
    "GAP_STATUSES",
    "OPPORTUNITY_STATUSES",
    "SKILL_VERSION_STATUSES",
    "Capability",
    "CapabilityGap",
    "EvolutionOpportunity",
    "SkillVersion",
]
