"""Memory subsystem ORM models (frozen foundation).

Canonical schema lives in alembic/versions/20260831_0005_memory.py. Types are
portable (generic Uuid, JSON with JSONB variant) like the other modules so the
service layer unit-tests on SQLite, EXCEPT the pgvector embedding column which
is PostgreSQL-only: unit tests exercise embeddings via the in-memory path and
integration tests hit the real vector column.

Key semantics baked into the model (M5 brief):
- Forgetting is a HARD delete: memory_versions, memory_evidence and
  memory_embeddings all cascade on memory deletion, so a forgotten memory
  cannot resurface from any index or history. The audit row (which stores
  class/key/actor but NOT the content) is the only trace.
- Superseding keeps the old row (status=superseded, superseded_by set) for
  history; retrieval excludes non-active rows by default.
- Explicit owner memories (explicit=True) may only be changed by Actor.OWNER;
  the write policy must never silently rewrite them from inference.
- memory_embeddings carries (model_id, model_version, dim) so re-embedding
  with a new model is a rebuild of index rows, never a canonical-data change.
"""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
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

from app.memory.types import EMBEDDING_DIM
from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")


class Entity(Base):
    """Project/person/device/document/decision/system/task/capability nodes."""

    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    attrs_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("kind", "name", name="uq_entities_kind_name"),)


class EntityEdge(Base):
    __tablename__ = "entity_edges"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    src_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dst_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation: Mapped[str] = mapped_column(String(64), nullable=False)
    attrs_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("src_id", "dst_id", "relation", name="uq_entity_edges_src_dst_rel"),
    )


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    memory_class: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Optional stable key for keyed memories (e.g. preference "response.detail").
    key: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    # Canonical natural-language statement of the memory — what gets embedded.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)

    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", index=True
    )
    explicit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retention_class: Mapped[str] = mapped_column(String(16), nullable=False, default="standard")

    # Relationship links (task -> artifact -> conversation -> project).
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("entities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # Where this memory came from (owner statement refs, observation refs, ...).
    provenance_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )

    # Temporal validity (semantic facts) and event time (episodic memories).
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MemoryVersion(Base):
    """Immutable snapshot of a memory at each version (edit history)."""

    __tablename__ = "memory_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    memory_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    explicit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    edited_by: Mapped[str] = mapped_column(String(16), nullable=False)  # Actor value
    change_reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("memory_id", "version", name="uq_memory_versions_memory_version"),
    )


class MemoryEvidence(Base):
    """Individual evidence observations backing an inferred memory."""

    __tablename__ = "memory_evidence"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    memory_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MemoryEmbedding(Base):
    """pgvector index rows. PostgreSQL-only column; cascades on memory delete
    so forgetting removes the vector representation too."""

    __tablename__ = "memory_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    memory_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("memory_id", "model_id", name="uq_memory_embeddings_memory_model"),
    )


class MemoryAuditEvent(Base):
    """Append-only audit of memory mutations. Stores identifiers and reasons,
    NEVER memory content after a forget (so audit cannot resurrect content)."""

    __tablename__ = "memory_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    memory_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    memory_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
