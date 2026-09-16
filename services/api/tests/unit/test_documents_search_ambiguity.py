"""The two ``DocumentService.search`` branches the M20 core track left without a dedicated
test (its own report named them): several hits that share ONE NAME are answered by their
paths and focus nothing (ADR-0076's ambiguity rule, ADR-0083 decision 4 — a title or a name
is never an identity); a folder search with several different names focuses the FOLDER,
never a file. The oracle already carries the same-named pair: ``sozlesmeler/2025/sozlesme.docx``
and ``sozlesmeler/2026/sozlesme.docx``.
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
from app.operator.models import FOCUS_KIND_FILE, FOCUS_KIND_FOLDER, ObjectFocusRow
from tests.documents_support import build_fake_device_action


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


def test_two_files_sharing_one_name_are_named_by_path_and_nothing_is_focused(db) -> None:
    service = DocumentService(index=DocumentIndex())
    device = build_fake_device_action()

    # Only the two .docx: B52 put sozlesme.odt/.rtf/.epub/.doc beside them, and this test is
    # about two hits that share ONE name (the mixed case has its own test below).
    receipt = service.search(db, device, pattern="sozlesme", extensions=[".docx"])

    speech = receipt["speech"]
    assert "Aynı isimde birden fazla dosya var" in speech
    assert "sozlesmeler/2025/sozlesme.docx" in speech
    assert "sozlesmeler/2026/sozlesme.docx" in speech
    assert "Hangisini" in speech
    # Neither hit was guessed into focus: the owner is asked, not second-guessed.
    assert focus_module.current(db, FOCUS_KIND_FILE) is None
    assert focus_module.current(db, FOCUS_KIND_FOLDER) is None


def test_a_shared_name_among_different_names_is_still_spoken_by_path(db) -> None:
    """B52 regression: with sozlesme.odt/.rtf/.epub/.doc beside the two sozlesme.docx, the
    reply used to list "sozlesme.docx, sozlesme.docx" - two words the owner cannot choose
    between."""
    service = DocumentService(index=DocumentIndex())
    device = build_fake_device_action()

    speech = service.search(db, device, pattern="sozlesme")["speech"]

    assert speech.startswith("Şunları buldum")
    assert "sozlesmeler/2025/sozlesme.docx" in speech
    assert "sozlesmeler/2026/sozlesme.docx" in speech
    assert "sozlesme.docx, sozlesme.docx" not in speech
    assert "sozlesme.odt" in speech and "hangisini" in speech
    assert focus_module.current(db, FOCUS_KIND_FILE) is None


def test_a_folder_search_with_several_different_names_focuses_the_folder_not_a_file(db) -> None:
    service = DocumentService(index=DocumentIndex())
    device = build_fake_device_action()

    receipt = service.search(db, device, folder="Masaüstü", extensions=[".pdf", ".pptx", ".xlsx"])

    speech = receipt["speech"]
    assert speech.startswith("Şunları buldum")
    for name in ("rapor.pdf", "sunum-q3.pptx", "butce-2026.xlsx"):
        assert name in speech
    # The named folder reached the device as a search root, and became the folder focus.
    payload = device.payload_for("file.search")
    # The bucket name the device knows, not the owner's word (B03 req 3,
    # packages/protocol/file-search-roots.json). The spoken form still decides the focus.
    assert payload["roots"] == ["desktop"]
    folder = focus_module.current(db, FOCUS_KIND_FOLDER)
    assert folder is not None and folder.object_id == "Masaüstü"
    assert focus_module.current(db, FOCUS_KIND_FILE) is None
