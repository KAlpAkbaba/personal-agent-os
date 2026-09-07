"""M20 document index: the Cloud Core's record of what a device has extracted (ADR-0083).

Revision ID: 0026_document_index
Revises: 0025_object_focus
Create Date: 2026-09-08

Reversible. ORM model: app/documents/models.py::DocumentIndexRow.

``document_index`` — one row per ``(device_id, file_id)``, the latest content version
replacing the row (module docstring of the ORM model): no background crawling, this table
grows only from owner-initiated reads and searches. Portable types throughout (generic
``Uuid``, ``JSON`` with a ``JSONB`` variant on Postgres), the same discipline migration
0025 already documents.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_document_index"
down_revision: str | None = "0025_object_focus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "document_index",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("device_id", sa.String(length=200), nullable=False),
        sa.Column("file_id", sa.String(length=200), nullable=False),
        sa.Column("doc_id", sa.String(length=200), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("mtime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("structure", json_type, nullable=False, server_default="{}"),
        sa.Column("blocks", json_type, nullable=False, server_default="[]"),
        sa.Column(
            "blocks_truncated", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "file_id", name="uq_document_index_device_file"),
    )
    op.create_index("ix_document_index_doc_id", "document_index", ["doc_id"])
    op.create_index("ix_document_index_last_used_at", "document_index", ["last_used_at"])


def downgrade() -> None:
    op.drop_index("ix_document_index_last_used_at", table_name="document_index")
    op.drop_index("ix_document_index_doc_id", table_name="document_index")
    op.drop_table("document_index")
