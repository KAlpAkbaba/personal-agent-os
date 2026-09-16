"""``document_index``: the Cloud Core's own record of what the device has extracted.

docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3, ADR-0083 decision 3. No background
crawling: this table grows only from owner-initiated reads and searches
(``app.documents.service.DocumentService.read``/``search``). Unique on
``(device_id, file_id)`` — the latest content version replaces the row, so ``doc_id``
(a content hash) can change across upserts while ``file_id`` (a location) stays put; the
row's own ``doc_id`` is always the CURRENT content version at that location.

Portable types throughout (generic ``Uuid``, ``JSON`` with a ``JSONB`` variant on
Postgres) so the service layer unit-tests on SQLite — the same discipline
``app.operator.models.ObjectFocusRow`` already uses.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base

#: A block list is bounded to this many UTF-8 bytes once JSON-encoded (task brief: "<= 64
#: KB; truncated with a flag, never silently"). Enforced by
#: ``app.documents.index.DocumentIndex.upsert``, never by a database constraint (the same
#: reason ``ObjectFocusRow.label`` is truncated in Python, not at the schema).
MAX_BLOCKS_JSON_BYTES = 64 * 1024


class DocumentIndexRow(Base):
    """One indexed document at one location, on one device."""

    __tablename__ = "document_index"
    __table_args__ = (
        UniqueConstraint("device_id", "file_id", name="uq_document_index_device_file"),
        Index("ix_document_index_doc_id", "doc_id"),
        Index("ix_document_index_last_used_at", "last_used_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: The device this was extracted on ("device:<id>" or the broker's own device id as
    #: text) — a single-owner system may still own more than one machine (M19's own
    #: ``device_id`` fields on ``ObjectFocusRow``-adjacent tables use the same shape).
    device_id: Mapped[str] = mapped_column(String(200), nullable=False)
    #: A LOCATION (spec §2): ``"file:" + sha256(volume serial | casefolded path)[:32]``.
    file_id: Mapped[str] = mapped_column(String(200), nullable=False)
    #: A CONTENT VERSION (spec §2): ``"doc:" + sha256(bytes)``. Two files with the same
    #: title and different paths are two ``file_id``s; the same file re-read after an edit
    #: is the same ``file_id`` with a new ``doc_id``.
    doc_id: Mapped[str] = mapped_column(String(200), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: ``document.extract``'s own ``structure`` object, verbatim (spec §2's per-kind shape).
    structure: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )
    #: The extracted blocks, each carrying the ``ref`` an answer will cite (spec §2).
    #: Bounded to :data:`MAX_BLOCKS_JSON_BYTES`; ``truncated`` (in ``structure``-adjacent
    #: bookkeeping, see ``blocks_truncated``) records when a device's own truncation, or
    #: this table's own cap, cut it short.
    blocks: Mapped[list[Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    #: Whether ``blocks`` was cut short of what the device actually extracted (either the
    #: device's own ``truncated`` flag, or this table's :data:`MAX_BLOCKS_JSON_BYTES` cap)
    #: — never a silent cut (task brief).
    blocks_truncated: Mapped[bool] = mapped_column(nullable=False, default=False)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


#: B34 req 160-166: the undo journal. One row per managed mutation, written when it is
#: PROPOSED and completed when the device carried it out; the rows for one path are its
#: version history; the ``proposed`` rows are the approval queue.
MUTATION_KIND_WRITE = "write"
MUTATION_KIND_APPEND = "append"
MUTATION_KIND_EDIT = "edit"
MUTATION_KIND_RENAME = "rename"
MUTATION_KIND_MOVE = "move"
MUTATION_KIND_COPY = "copy"
MUTATION_KIND_DELETE = "delete"
MUTATION_KIND_RESTORE = "restore"
MUTATION_KINDS = (
    MUTATION_KIND_WRITE,
    MUTATION_KIND_APPEND,
    MUTATION_KIND_EDIT,
    MUTATION_KIND_RENAME,
    MUTATION_KIND_MOVE,
    MUTATION_KIND_COPY,
    MUTATION_KIND_DELETE,
    MUTATION_KIND_RESTORE,
)

#: ``proposed`` (waiting for the owner's word) -> ``applied`` -> ``undone``; ``discarded``
#: from ``proposed``; ``failed`` when the device refused or the read-back did not verify.
MUTATION_STATE_PROPOSED = "proposed"
MUTATION_STATE_APPLIED = "applied"
MUTATION_STATE_UNDONE = "undone"
MUTATION_STATE_DISCARDED = "discarded"
MUTATION_STATE_FAILED = "failed"

#: The risk the policy assigned (app.documents.mutations.risk_of): ``low`` applies at once
#: (journaled, undoable), ``sensitive`` and ``critical`` wait for the owner's word.
MUTATION_RISK_LOW = "low"
MUTATION_RISK_SENSITIVE = "sensitive"
MUTATION_RISK_CRITICAL = "critical"


class FileMutationRow(Base):
    __tablename__ = "file_mutations"
    __table_args__ = (
        Index("ix_file_mutations_state_created", "state", "created_at"),
        Index("ix_file_mutations_path_after", "path_after"),
        Index("ix_file_mutations_path_before", "path_before"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[str] = mapped_column(String(200), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=MUTATION_STATE_PROPOSED)
    risk: Mapped[str] = mapped_column(String(16), nullable=False, default=MUTATION_RISK_SENSITIVE)
    #: The device's location id of the file BEFORE the act (None for a file not yet created).
    file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    path_before: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    path_after: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    sha_before: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sha_after: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_before: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    size_after: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: The device's undo-store entry for what was displaced (write/append/edit/delete).
    backup_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: ``{"capability": "file.write", "payload": {...}}`` - exactly what the device is asked.
    plan_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
    )
    #: The inverse plan, derived from the device's ANSWER after the act (never from hope).
    undo_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    #: What the device answered.
    result_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    #: One Turkish sentence describing the change, the same one the owner heard.
    summary: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: For a ``restore`` row: the mutation it undid.
    undo_of: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: The proposal was spoken / listed to the owner (the same read-back gate mail drafts keep).
    read_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_back_session_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    read_back_turn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "MAX_BLOCKS_JSON_BYTES",
    "MUTATION_KINDS",
    "MUTATION_KIND_APPEND",
    "MUTATION_KIND_COPY",
    "MUTATION_KIND_DELETE",
    "MUTATION_KIND_EDIT",
    "MUTATION_KIND_MOVE",
    "MUTATION_KIND_RENAME",
    "MUTATION_KIND_RESTORE",
    "MUTATION_KIND_WRITE",
    "MUTATION_RISK_CRITICAL",
    "MUTATION_RISK_LOW",
    "MUTATION_RISK_SENSITIVE",
    "MUTATION_STATE_APPLIED",
    "MUTATION_STATE_DISCARDED",
    "MUTATION_STATE_FAILED",
    "MUTATION_STATE_PROPOSED",
    "MUTATION_STATE_UNDONE",
    "DocumentIndexRow",
    "FileMutationRow",
]
