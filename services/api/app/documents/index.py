"""``DocumentIndex``: upsert/read the Cloud Core's record of an extracted document.

docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3. No background crawling (ADR-0083 decision
3): every write here is the direct result of an owner-initiated ``document.extract`` or
``file.search`` call — this module never runs on a schedule and never calls a device
itself.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents.models import MAX_BLOCKS_JSON_BYTES, DocumentIndexRow

#: How many blocks a single upsert keeps once the JSON-encoded list would exceed
#: :data:`app.documents.models.MAX_BLOCKS_JSON_BYTES` — trimmed from the END (later blocks
#: are dropped first), and ``blocks_truncated`` is set so an answer can say so rather than
#: silently missing content (task brief: "truncated with a flag, never silently").
_MIN_BLOCKS_KEPT = 1


def _bounded_blocks(blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Cut ``blocks`` to fit :data:`MAX_BLOCKS_JSON_BYTES` once JSON-encoded."""
    encoded = json.dumps(blocks, ensure_ascii=False)
    if len(encoded.encode("utf-8")) <= MAX_BLOCKS_JSON_BYTES:
        return blocks, False
    kept = list(blocks)
    while len(kept) > _MIN_BLOCKS_KEPT:
        kept = kept[: len(kept) - 1]
        encoded = json.dumps(kept, ensure_ascii=False)
        if len(encoded.encode("utf-8")) <= MAX_BLOCKS_JSON_BYTES:
            return kept, True
    return kept, True


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class DocumentIndex:
    """Upsert-from-extract, get-by-id, latest-N, touch. A thin, testable seam over
    ``document_index`` — ``app.documents.service.DocumentService`` is the only caller in
    production; tests exercise this module directly."""

    def upsert(
        self,
        db: Session,
        *,
        device_id: str,
        extract: dict[str, Any],
        now: datetime | None = None,
    ) -> DocumentIndexRow:
        """Write (or replace) the row for ``extract["file"]["file_id"]`` on ``device_id``.

        ``extract`` is exactly what ``document.extract`` returned (spec §2): ``file``
        (the record: path/name/size/mtime/sha256), ``doc_id``, ``kind``, ``title?``,
        ``blocks``, ``structure``, ``truncated``. Unique on ``(device_id, file_id)`` — a
        second extract of the same location REPLACES the row (module docstring).
        """
        now = now or datetime.now(UTC)
        file_record = dict(extract.get("file") or {})
        file_id = str(file_record.get("file_id") or "")
        if not file_id:
            raise ValueError("extract['file']['file_id'] is required")
        path = str(file_record.get("path") or "")
        name = str(file_record.get("name") or path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
        blocks, cut_by_size = _bounded_blocks(list(extract.get("blocks") or []))
        truncated = bool(extract.get("truncated")) or cut_by_size

        row = (
            db.execute(
                select(DocumentIndexRow).where(
                    DocumentIndexRow.device_id == device_id,
                    DocumentIndexRow.file_id == file_id,
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            row = DocumentIndexRow(id=uuid.uuid4(), device_id=device_id, file_id=file_id)
            db.add(row)
        row.doc_id = str(extract.get("doc_id") or "")
        row.path = path
        row.name = name
        row.kind = str(extract.get("kind") or file_record.get("kind") or "unknown")
        row.title = extract.get("title")
        row.size = int(file_record.get("size") or 0)
        row.mtime = _parse_dt(file_record.get("mtime"))
        row.sha256 = file_record.get("sha256")
        row.structure = dict(extract.get("structure") or {})
        row.blocks = blocks
        row.blocks_truncated = truncated
        row.extracted_at = now
        row.last_used_at = now
        db.commit()
        db.refresh(row)
        return row

    def get_by_doc_id(self, db: Session, doc_id: str) -> DocumentIndexRow | None:
        return (
            db.execute(select(DocumentIndexRow).where(DocumentIndexRow.doc_id == doc_id))
            .scalars()
            .first()
        )

    def get_by_file_id(
        self, db: Session, *, device_id: str, file_id: str
    ) -> DocumentIndexRow | None:
        return (
            db.execute(
                select(DocumentIndexRow).where(
                    DocumentIndexRow.device_id == device_id,
                    DocumentIndexRow.file_id == file_id,
                )
            )
            .scalars()
            .first()
        )

    def latest(
        self, db: Session, *, device_id: str | None = None, limit: int = 20
    ) -> list[DocumentIndexRow]:
        stmt = select(DocumentIndexRow).order_by(DocumentIndexRow.last_used_at.desc())
        if device_id is not None:
            stmt = stmt.where(DocumentIndexRow.device_id == device_id)
        stmt = stmt.limit(max(1, limit))
        return list(db.execute(stmt).scalars().all())

    def touch(
        self, db: Session, row: DocumentIndexRow, *, now: datetime | None = None
    ) -> DocumentIndexRow:
        """Bump ``last_used_at`` without re-extracting (an answer/summary read the row)."""
        row.last_used_at = now or datetime.now(UTC)
        db.commit()
        db.refresh(row)
        return row


__all__ = ["DocumentIndex"]
