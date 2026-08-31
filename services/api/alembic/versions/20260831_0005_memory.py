"""Memory subsystem schema (M5): entities, entity_edges, memories,
memory_versions, memory_evidence, memory_embeddings (pgvector),
memory_audit_events.

Revision ID: 0005_memory
Revises: 0004_voice
Create Date: 2026-08-31

Reversible: downgrade drops all M5 tables; the pgvector extension itself was
created in 0001 and is left in place.

Lead-authored frozen foundation — ORM models in app/memory/models.py must
match. Forgetting a memory is a hard DELETE on `memories`; versions, evidence
and embeddings cascade so no index or history representation survives.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0005_memory"
down_revision: str | None = "0004_voice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEMORY_CLASSES = (
    "preference",
    "episodic",
    "project",
    "semantic",
    "procedural",
    "voice_preference",
)
_STAGES = ("session", "candidate", "durable")
_STATUSES = ("active", "superseded")
_RETENTION = ("session", "short", "standard", "pinned")
_ENTITY_KINDS = (
    "project",
    "person",
    "device",
    "document",
    "decision",
    "system",
    "task",
    "capability",
)
_EMBEDDING_DIM = 256


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column(
            "attrs_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(_in_list("kind", _ENTITY_KINDS), name="ck_entities_kind"),
        sa.UniqueConstraint("kind", "name", name="uq_entities_kind_name"),
    )
    op.create_index("ix_entities_kind", "entities", ["kind"])

    op.create_table(
        "entity_edges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "src_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dst_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation", sa.String(length=64), nullable=False),
        sa.Column(
            "attrs_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("src_id", "dst_id", "relation", name="uq_entity_edges_src_dst_rel"),
    )
    op.create_index("ix_entity_edges_src_id", "entity_edges", ["src_id"])
    op.create_index("ix_entity_edges_dst_id", "entity_edges", ["dst_id"])

    op.create_table(
        "memories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("memory_class", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=256), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "value_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("stage", sa.String(length=16), nullable=False, server_default="candidate"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("explicit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "retention_class", sa.String(length=16), nullable=False, server_default="standard"
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "provenance_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "superseded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in_list("memory_class", _MEMORY_CLASSES), name="ck_memories_class"),
        sa.CheckConstraint(_in_list("stage", _STAGES), name="ck_memories_stage"),
        sa.CheckConstraint(_in_list("status", _STATUSES), name="ck_memories_status"),
        sa.CheckConstraint(
            _in_list("retention_class", _RETENTION), name="ck_memories_retention"
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_memories_confidence"
        ),
    )
    op.create_index("ix_memories_memory_class", "memories", ["memory_class"])
    op.create_index("ix_memories_key", "memories", ["key"])
    op.create_index("ix_memories_status", "memories", ["status"])
    op.create_index("ix_memories_project_id", "memories", ["project_id"])
    op.create_index("ix_memories_conversation_id", "memories", ["conversation_id"])
    op.create_index("ix_memories_task_id", "memories", ["task_id"])
    op.create_index("ix_memories_artifact_id", "memories", ["artifact_id"])
    op.create_index("ix_memories_occurred_at", "memories", ["occurred_at"])

    op.create_table(
        "memory_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "value_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("explicit", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("edited_by", sa.String(length=16), nullable=False),
        sa.Column("change_reason", sa.String(length=512), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("memory_id", "version", name="uq_memory_versions_memory_version"),
    )
    op.create_index("ix_memory_versions_memory_id", "memory_versions", ["memory_id"])

    op.create_table(
        "memory_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column(
            "source_ref_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1"),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_memory_evidence_memory_id", "memory_evidence", ["memory_id"])

    op.create_table(
        "memory_embeddings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=32), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(_EMBEDDING_DIM), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("memory_id", "model_id", name="uq_memory_embeddings_memory_model"),
    )
    op.create_index("ix_memory_embeddings_memory_id", "memory_embeddings", ["memory_id"])
    # ANN index for cosine retrieval. hnsw is available on pgvector >= 0.5
    # (pgvector/pgvector:pg16 image ships it).
    op.execute(
        "CREATE INDEX ix_memory_embeddings_hnsw ON memory_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "memory_audit_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("memory_class", sa.String(length=32), nullable=True),
        sa.Column("key", sa.String(length=256), nullable=True),
        sa.Column("actor", sa.String(length=16), nullable=False),
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
    )
    op.create_index("ix_memory_audit_events_action", "memory_audit_events", ["action"])
    op.create_index("ix_memory_audit_events_memory_id", "memory_audit_events", ["memory_id"])


def downgrade() -> None:
    op.drop_table("memory_audit_events")
    op.drop_table("memory_embeddings")
    op.drop_table("memory_evidence")
    op.drop_table("memory_versions")
    op.drop_table("memories")
    op.drop_table("entity_edges")
    op.drop_table("entities")
