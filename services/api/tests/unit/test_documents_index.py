"""Unit tests: app.documents.index (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3,
ADR-0083 decision 3). Upsert-from-extract, get-by-id, latest-N, touch, and the ``<= 64 KB
JSON, truncated with a flag, never silently`` bound on ``blocks`` - and that the
hand-written migration (0026_document_index) actually matches the ORM model it claims to.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.documents.index import DocumentIndex
from app.documents.models import MAX_BLOCKS_JSON_BYTES, DocumentIndexRow
from tests.documents_support import doc_id_for, extract_result, file_id_for


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    DocumentIndexRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def test_upsert_from_an_extract_result_writes_every_identity_field(db) -> None:
    index = DocumentIndex()
    row = index.upsert(db, device_id="device:default", extract=extract_result("rapor.pdf"))
    assert row.device_id == "device:default"
    assert row.file_id == file_id_for("rapor.pdf")
    assert row.doc_id == doc_id_for("rapor.pdf")
    assert row.path == "rapor.pdf"
    assert row.name == "rapor.pdf"
    assert row.kind == "pdf"
    assert row.title == "Yıllık Rapor 2026"
    assert row.blocks_truncated is False
    assert len(row.blocks) == 5


def test_a_second_extract_of_the_same_location_replaces_the_row_not_appends(db) -> None:
    """Module docstring: unique on ``(device_id, file_id)`` - the latest content version
    REPLACES the row."""
    index = DocumentIndex()
    first = index.upsert(db, device_id="device:default", extract=extract_result("rapor.pdf"))
    second = index.upsert(db, device_id="device:default", extract=extract_result("rapor.pdf"))
    assert first.id == second.id
    all_rows = db.query(DocumentIndexRow).all()
    assert len(all_rows) == 1


def test_two_files_on_the_same_device_are_two_rows(db) -> None:
    index = DocumentIndex()
    index.upsert(db, device_id="device:default", extract=extract_result("rapor.pdf"))
    index.upsert(db, device_id="device:default", extract=extract_result("veri.csv"))
    all_rows = db.query(DocumentIndexRow).all()
    assert len(all_rows) == 2


def test_get_by_doc_id_and_get_by_file_id(db) -> None:
    index = DocumentIndex()
    written = index.upsert(db, device_id="device:default", extract=extract_result("rapor.pdf"))
    by_doc = index.get_by_doc_id(db, doc_id_for("rapor.pdf"))
    by_file = index.get_by_file_id(db, device_id="device:default", file_id=file_id_for("rapor.pdf"))
    assert by_doc is not None and by_doc.id == written.id
    assert by_file is not None and by_file.id == written.id
    assert index.get_by_doc_id(db, "doc:does-not-exist") is None


def test_latest_orders_by_last_used_at_descending_and_respects_limit(db) -> None:
    index = DocumentIndex()
    now = datetime.now(UTC)
    index.upsert(db, device_id="device:default", extract=extract_result("rapor.pdf"), now=now)
    index.upsert(
        db,
        device_id="device:default",
        extract=extract_result("veri.csv"),
        now=now + timedelta(seconds=1),
    )
    index.upsert(
        db,
        device_id="device:default",
        extract=extract_result("ayarlar.json"),
        now=now + timedelta(seconds=2),
    )
    latest = index.latest(db, device_id="device:default", limit=2)
    assert [r.path for r in latest] == ["ayarlar.json", "veri.csv"]


def test_touch_bumps_last_used_at_without_changing_content(db) -> None:
    index = DocumentIndex()
    now = datetime.now(UTC)
    row = index.upsert(db, device_id="device:default", extract=extract_result("rapor.pdf"), now=now)
    later = now + timedelta(minutes=5)
    touched = index.touch(db, row, now=later)
    # SQLite round-trips a naive datetime (drops tzinfo); compare the wall-clock value.
    assert touched.last_used_at.replace(tzinfo=UTC) == later
    assert touched.extracted_at.replace(tzinfo=UTC) == now
    assert touched.doc_id == doc_id_for("rapor.pdf")


def test_blocks_over_the_byte_cap_are_trimmed_and_flagged_never_silently(db) -> None:
    index = DocumentIndex()
    huge_extract = extract_result("rapor.pdf")
    padding = "x" * 2000
    huge_extract["blocks"] = [{"ref": f"p{n}", "kind": "page", "text": padding} for n in range(200)]
    row = index.upsert(db, device_id="device:default", extract=huge_extract)
    import json

    encoded = json.dumps(row.blocks, ensure_ascii=False)
    assert len(encoded.encode("utf-8")) <= MAX_BLOCKS_JSON_BYTES
    assert row.blocks_truncated is True
    assert len(row.blocks) < 200


def test_the_devices_own_truncated_flag_is_carried_through_even_under_the_byte_cap(db) -> None:
    index = DocumentIndex()
    extract = extract_result("rapor.pdf")
    extract["truncated"] = True
    row = index.upsert(db, device_id="device:default", extract=extract)
    assert row.blocks_truncated is True


def test_upsert_requires_a_file_id(db) -> None:
    index = DocumentIndex()
    with pytest.raises(ValueError):
        index.upsert(db, device_id="device:default", extract={"file": {}, "blocks": []})


# ---------------------------------------------------------- the migration matches the model


def test_migration_creates_every_column_the_model_declares() -> None:
    """A structural check that the hand-written alembic revision (0026_document_index)
    actually matches ``DocumentIndexRow`` - the same discipline ``test_operator_focus.py``
    applies to migration 0025, narrowed to "does it declare the columns the model has"
    rather than re-deriving ``test_migration_compatibility.py``'s whole expand-only gate.
    """
    versions_dir = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    migration_path = next(versions_dir.glob("*_document_index.py"))
    tree = ast.parse(migration_path.read_text(encoding="utf-8"))

    create_call = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
        ):
            create_call = node
            break
    assert create_call is not None, "no op.create_table(...) found in the migration"

    migration_columns: set[str] = set()
    for arg in create_call.args[1:]:
        if (
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "Column"
            and arg.args
            and isinstance(arg.args[0], ast.Constant)
        ):
            migration_columns.add(str(arg.args[0].value))

    model_columns = {c.name for c in DocumentIndexRow.__table__.columns}
    assert migration_columns == model_columns
