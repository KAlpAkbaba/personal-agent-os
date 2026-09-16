"""Artifact + task + research ORM models (M3).

Canonical schema lives in alembic/versions/20260831_0003_research_artifact.py.
Types are portable (generic Uuid, JSON with a JSONB variant on PostgreSQL) so
unit tests exercise the service layer against SQLite while integration tests run
against the real PostgreSQL schema (same pattern as app/broker/models.py).

Architecture (ADR-0020): Task -> Artifact -> Presentation. The canonical
semantic body is Markdown and is the source of truth; it lives in Postgres
(`artifact_versions.canonical_body`). Renders (PDF/DOCX/HTML/TXT) are derived
bytes stored in the ObjectStore and recorded in `artifact_renders`. Each
artifact records an `executive_summary` separately so a task can reach READY and
be presented without dumping the whole body (Executive layer, MASTER_SPEC §G).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
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

# ---------------------------------------------------------------- task states
# ARCHITECTURE.md §4 minimum states (subset realized in M3).
TASK_STATUS_CREATED = "CREATED"
TASK_STATUS_PLANNED = "PLANNED"
TASK_STATUS_RUNNING = "RUNNING"
TASK_STATUS_WAITING_EXTERNAL = "WAITING_EXTERNAL"
TASK_STATUS_RENDERING = "RENDERING"
TASK_STATUS_READY = "READY"
TASK_STATUS_PRESENTING = "PRESENTING"
TASK_STATUS_COMPLETED = "COMPLETED"
TASK_STATUS_FAILED_RECOVERABLE = "FAILED_RECOVERABLE"
TASK_STATUS_FAILED_TERMINAL = "FAILED_TERMINAL"

TASK_STATUSES = (
    TASK_STATUS_CREATED,
    TASK_STATUS_PLANNED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_EXTERNAL,
    TASK_STATUS_RENDERING,
    TASK_STATUS_READY,
    TASK_STATUS_PRESENTING,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED_RECOVERABLE,
    TASK_STATUS_FAILED_TERMINAL,
)

# ------------------------------------------------------------ artifact states
# ARCHITECTURE.md §5.
ARTIFACT_STATE_DRAFT = "DRAFT"
ARTIFACT_STATE_CANONICAL_READY = "CANONICAL_READY"
ARTIFACT_STATE_RENDERS_PENDING = "RENDERS_PENDING"
ARTIFACT_STATE_READY = "READY"
ARTIFACT_STATE_ARCHIVED = "ARCHIVED"

ARTIFACT_STATES = (
    ARTIFACT_STATE_DRAFT,
    ARTIFACT_STATE_CANONICAL_READY,
    ARTIFACT_STATE_RENDERS_PENDING,
    ARTIFACT_STATE_READY,
    ARTIFACT_STATE_ARCHIVED,
)

ARTIFACT_KIND_RESEARCH_REPORT = "research_report"
CANONICAL_FORMAT_MARKDOWN = "markdown"

# M22 Artifact Factory (docs/M22_ARTIFACT_FACTORY_SPEC.md §1, ADR-0085 decisions 1/3/4):
# the SAME Task -> Artifact -> Presentation tables (ADR-0020) the research pipeline
# uses, reused rather than duplicated. A factory artifact's ``kind`` is one of
# ``app.artifacts.spec.ARTIFACT_KINDS`` (never ``ARTIFACT_KIND_RESEARCH_REPORT``); its
# canonical body is the spec's own deterministic JSON, not Markdown.
CANONICAL_FORMAT_ARTIFACT_SPEC_JSON = "artifact_spec_json"

#: ``artifact_renders.state`` (migration 20260908_0028): whether the render's
#: independent-reader validation passed. Existing pre-M22 rows default to "valid"
#: (see the migration's own docstring for why that default is honest, not a claim of
#: having literally been validated) — every NEW factory render always gets an
#: explicit value from a real ``app.artifacts.validation.validate()`` call.
RENDER_STATE_VALID = "valid"
RENDER_STATE_INVALID = "invalid"
RENDER_STATES = (RENDER_STATE_VALID, RENDER_STATE_INVALID)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TASK_STATUS_CREATED, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_from_device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # M9: stamped by the API's artifact-ready announcer once the readiness push
    # has been delivered. The Temporal worker never touches it — it only makes
    # the task READY; see app/mobile/announcer.py for why the two processes
    # communicate through this column instead of a worker-held credential.
    #: B07 req 14/15/16/17: bounded delivery. Before these three columns existed the
    #: announcer had no way to know it had already tried, so a permanently failing item was
    #: re-attempted on every sweep for ever. `attempts` counts failures, `next_attempt_at`
    #: holds the backoff, and `quarantined_at` is the announcer giving up on ONE item so the
    #: queue behind it can move (app.notifications.delivery).
    announce_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    announce_next_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    announce_quarantined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    announced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskRun(Base):
    __tablename__ = "task_runs"
    __table_args__ = (UniqueConstraint("task_id", "attempt", name="uq_task_runs_task_attempt"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    telemetry_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(64), nullable=False, default=ARTIFACT_KIND_RESEARCH_REPORT
    )
    canonical_format: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CANONICAL_FORMAT_MARKDOWN
    )
    # Executive layer (MASTER_SPEC §G): short decision/answer, stored apart from
    # the full body so READY presentation never forces the whole report.
    executive_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ARTIFACT_STATE_DRAFT, index=True
    )
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"
    __table_args__ = (
        UniqueConstraint("artifact_id", "version", name="uq_artifact_versions_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Canonical semantic body (Markdown) is the source of truth and lives in
    # Postgres (lead decision). canonical_object_key is a reserved seam for
    # offloading very large bodies to the ObjectStore later.
    canonical_body: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_manifest_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    #: B42 (req 405-407, migration 0054): who asked, what rendered it, what it came from.
    provenance_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ArtifactRender(Base):
    __tablename__ = "artifact_renders"
    __table_args__ = (
        UniqueConstraint("artifact_version_id", "format", name="uq_artifact_renders_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("artifact_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # M22 (migration 20260908_0028_artifact_validation, ADR-0085 decision 3): the
    # independent reader's ValidationReport.to_dict(), NULL for a render nobody has
    # validated (every pre-M22 row).
    validation_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RENDER_STATE_VALID, server_default=RENDER_STATE_VALID
    )


class ResearchSource(Base):
    __tablename__ = "research_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "ARTIFACT_KIND_RESEARCH_REPORT",
    "CANONICAL_FORMAT_ARTIFACT_SPEC_JSON",
    "CANONICAL_FORMAT_MARKDOWN",
    "RENDER_STATE_INVALID",
    "RENDER_STATE_VALID",
    "RENDER_STATES",
    "Artifact",
    "ArtifactRender",
    "ArtifactVersion",
    "ResearchSource",
    "Task",
    "TaskRun",
]
