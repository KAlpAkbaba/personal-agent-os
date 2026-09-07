"""M20's own kinds on the M19 durable object-focus stack (docs/
M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3, ADR-0083 decision 4): ``file`` (a LOCATION),
``document`` (a CONTENT VERSION) and ``folder`` (a search root) are additive kinds on the
SAME ``app.operator.focus``/``ObjectFocusRow`` table ``test_operator_focus.py`` already
covers generically for ``window``/``app`` — this file narrows to what is specific to the
three new kinds: they are independent stacks, and two documents that share a TITLE are
still two distinct ``file_id``/``doc_id`` identities (ADR-0076's ambiguity rule, carried
by ``DocumentService._is_ambiguous_title``, not by this table).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.documents.index import DocumentIndex
from app.documents.models import DocumentIndexRow
from app.documents.service import DocumentService
from app.operator import focus as focus_module
from app.operator.models import (
    FOCUS_KIND_DOCUMENT,
    FOCUS_KIND_FILE,
    FOCUS_KIND_FOLDER,
    FOCUS_KINDS,
    ObjectFocusRow,
)
from tests.documents_support import doc_id_for, extract_result, file_id_for


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ObjectFocusRow.__table__.create(engine)
    DocumentIndexRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def test_file_document_and_folder_are_known_focus_kinds() -> None:
    assert FOCUS_KIND_FILE in FOCUS_KINDS
    assert FOCUS_KIND_DOCUMENT in FOCUS_KINDS
    assert FOCUS_KIND_FOLDER in FOCUS_KINDS


def test_file_and_document_focus_are_independent_stacks(db) -> None:
    focus_module.set_focus(
        db, FOCUS_KIND_FILE, file_id_for("rapor.pdf"), label="rapor.pdf", source="test"
    )
    focus_module.set_focus(
        db,
        FOCUS_KIND_DOCUMENT,
        doc_id_for("butce-2026.xlsx"),
        label="butce-2026.xlsx",
        source="test",
    )
    file_current = focus_module.current(db, FOCUS_KIND_FILE)
    doc_current = focus_module.current(db, FOCUS_KIND_DOCUMENT)
    assert file_current is not None and file_current.object_id == file_id_for("rapor.pdf")
    assert doc_current is not None and doc_current.object_id == doc_id_for("butce-2026.xlsx")
    assert focus_module.current(db, FOCUS_KIND_FOLDER) is None


def test_reading_a_document_sets_both_document_and_file_focus() -> None:
    """``DocumentService._extract`` (spec §3): every ``document.extract`` sets BOTH the
    content-version focus (``document``) and the location focus (``file``) - a later
    "bu dosyayı..." and a later "bu belgeyi..." must both resolve to what was just read."""
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ObjectFocusRow.__table__.create(engine)
    DocumentIndexRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    class _FakeDeviceAction:
        def run(self, *, capability, payload, idempotency_key, timeout_s):
            from app.routines.dispatch import DeviceRunResult

            assert capability == "document.extract"
            return DeviceRunResult(True, result=extract_result("rapor.pdf"))

    service = DocumentService(index=DocumentIndex())
    with factory() as db:
        # A FILE is already focused (as if a search just found it) but not yet extracted
        # - the exact branch spec §3 names ("current file not yet extracted -> extract it
        # first"); target="current" reads that.
        focus_module.set_focus(
            db, FOCUS_KIND_FILE, file_id_for("rapor.pdf"), label="rapor.pdf", source="test"
        )
        result = service.read(db, _FakeDeviceAction(), target="current")
        assert result["file_id"] == file_id_for("rapor.pdf")
        doc_entry = focus_module.current(db, FOCUS_KIND_DOCUMENT)
        file_entry = focus_module.current(db, FOCUS_KIND_FILE)
    assert doc_entry is not None and doc_entry.object_id == doc_id_for("rapor.pdf")
    assert file_entry is not None and file_entry.object_id == file_id_for("rapor.pdf")


def test_two_documents_sharing_a_title_are_two_distinct_identities_named_by_path(db) -> None:
    """ADR-0076's ambiguity rule (spec §3): ``sozlesmeler/2025/sozlesme.docx`` and
    ``sozlesmeler/2026/sozlesme.docx`` share the title "Hizmet Sözleşmesi" but are two
    ``file_id``s/``doc_id``s - the focus TABLE holds them as two ordinary rows; the
    ambiguity itself is ``DocumentService``'s own query over ``document_index.title``,
    not something this table encodes."""
    index = DocumentIndex()
    a = index.upsert(
        db, device_id="device:default", extract=extract_result("sozlesmeler/2025/sozlesme.docx")
    )
    b = index.upsert(
        db, device_id="device:default", extract=extract_result("sozlesmeler/2026/sozlesme.docx")
    )
    assert a.title == b.title == "Hizmet Sözleşmesi"
    assert a.file_id != b.file_id
    assert a.doc_id != b.doc_id

    service = DocumentService(index=index)
    assert service._is_ambiguous_title(db, a) is True
    assert service._is_ambiguous_title(db, b) is True

    # Focusing both, in order, still gives a genuine current/previous pair by identity.
    focus_module.set_focus(db, FOCUS_KIND_DOCUMENT, a.doc_id, label=a.title, source="test")
    focus_module.set_focus(db, FOCUS_KIND_DOCUMENT, b.doc_id, label=b.title, source="test")
    assert focus_module.current(db, FOCUS_KIND_DOCUMENT).object_id == b.doc_id
    assert focus_module.previous(db, FOCUS_KIND_DOCUMENT).object_id == a.doc_id


def test_folder_focus_is_set_by_a_search_over_a_folder_pattern(db) -> None:
    focus_module.set_focus(
        db, FOCUS_KIND_FOLDER, "Desktop", label="Desktop", source="document_search"
    )
    entry = focus_module.current(db, FOCUS_KIND_FOLDER)
    assert entry is not None
    assert entry.object_id == "Desktop"
    assert entry.source == "document_search"
