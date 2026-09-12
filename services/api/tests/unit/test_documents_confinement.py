"""Cloud Core's own second line of defence over ``folder``/``pattern`` (security review,
MEDIUM — single-layer confinement): the device confines every path for real (spec §2), but
``DocumentService`` used to forward a model-supplied ``folder``/``pattern`` straight to
``file.search``/every named-target resolution path with no check of its own. This file
proves the guard (``app.documents.service._validate_folder`` / ``_validate_pattern``,
exercised through the public ``search``/``answer``/``inspect`` surface) refuses an
out-of-bounds value BEFORE the fake device ever sees a call, while a known alias or an
ordinary relative folder still reaches it — and that a folder focus is persisted only from
a name this layer recognises or from the device's own ``searched_roots``, never a raw
model string (the accompanying LOW finding).
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
from app.routines.dispatch import DeviceRunResult
from tests.alarms_support import FakeDeviceAction
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


@pytest.fixture()
def service() -> DocumentService:
    return DocumentService(index=DocumentIndex())


# --------------------------------------------------------------------- folder refusals

_BAD_FOLDERS = [
    pytest.param("C:\\Windows\\System32", id="drive_letter_absolute"),
    pytest.param("..\\..\\secrets", id="parent_traversal_backslash"),
    pytest.param("../../secrets", id="parent_traversal_forward_slash"),
    pytest.param("\\\\server\\share", id="unc_share"),
    pytest.param("\\\\?\\C:\\x", id="device_prefix"),
]


@pytest.mark.parametrize("bad_folder", _BAD_FOLDERS)
def test_an_out_of_bounds_folder_is_refused_before_any_device_call(db, service, bad_folder) -> None:
    device = build_fake_device_action()

    receipt = service.search(db, device, folder=bad_folder)

    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == "invalid_argument"
    assert "klasörü tanımıyorum" in receipt["speech"]
    assert bad_folder not in receipt["speech"]  # never echoes the rejected value
    assert device.calls == []
    assert focus_module.current(db, FOCUS_KIND_FOLDER) is None


# -------------------------------------------------------------------- pattern refusals


def test_an_overlong_pattern_is_refused_before_any_device_call(db, service) -> None:
    device = build_fake_device_action()
    huge = "a" * 5000

    receipt = service.search(db, device, pattern=huge)

    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == "invalid_argument"
    assert "deseni kullanamam" in receipt["speech"]
    assert huge not in receipt["speech"]
    assert device.calls == []


def test_a_pattern_with_a_path_separator_is_refused_before_any_device_call(db, service) -> None:
    device = build_fake_device_action()

    receipt = service.search(db, device, pattern="sozlesmeler/2026")

    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == "invalid_argument"
    assert "deseni kullanamam" in receipt["speech"]
    assert device.calls == []


def test_a_named_target_with_a_bad_pattern_is_refused_without_reaching_the_device(
    db, service
) -> None:
    """The SAME guard on the named-target resolution path ``document.answer``/``read``/
    ``summarize`` use when the owner names a document by a spoken word rather than
    current/previous (``_resolve_document``'s own "a spoken name" branch)."""
    device = build_fake_device_action()

    receipt = service.answer(db, device, target="../../secrets", question="Ne yazıyor?")

    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == "invalid_argument"
    assert "deseni kullanamam" in receipt["speech"]
    assert device.calls == []


def test_a_named_target_with_a_bad_pattern_is_refused_for_inspect_too(db, service) -> None:
    """``inspect``'s OWN resolution path (``_resolve_target_file``) is a separate code
    path from ``_resolve_document`` (spec §3: inspect never triggers a full extract) and
    needed its own guard call."""
    device = build_fake_device_action()

    receipt = service.inspect(db, device, target="a" * 300)

    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == "invalid_argument"
    assert "deseni kullanamam" in receipt["speech"]
    assert device.calls == []


# ------------------------------------------------------- known aliases still reach it


def test_a_known_folder_alias_reaches_the_device_as_a_name_the_device_knows(db, service) -> None:
    """B03 req 3. This test used to assert ``roots == ["Masaüstü"]`` - the owner's Turkish
    word, on the wire, to a device that has never known it. That is the defect: the device
    answered ``payload.roots must be absolute paths`` and folder search was broken in
    production for three days with this test green. The alias now becomes the bucket name
    the shared contract declares, and the device resolves it."""
    device = build_fake_device_action()

    receipt = service.search(db, device, folder="Masaüstü", extensions=[".pdf"])

    assert receipt["execution_status"] == "executed"
    assert device.payload_for("file.search")["roots"] == ["desktop"]


def test_a_bucket_with_a_subfolder_reaches_the_device_as_one_entry(db, service) -> None:
    device = build_fake_device_action()

    receipt = service.search(db, device, folder="Belgelerim/Faturalar", pattern="fatura")

    assert receipt["execution_status"] == "executed"
    assert device.payload_for("file.search")["roots"] == ["documents/Faturalar"]


def test_a_relative_folder_that_names_no_bucket_is_refused_here_in_the_owners_words(
    db, service
) -> None:
    """It used to be forwarded, and the device refused it with ``validation_error`` - a class
    the owner never saw a reason for. Cloud Core genuinely does not know which folder
    ``sozlesmeler/2026`` is, and saying so is the honest answer."""
    device = build_fake_device_action()

    receipt = service.search(db, device, folder="sozlesmeler/2026", pattern="sozlesme")

    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == "invalid_argument"
    assert "klasörü tanımıyorum" in receipt["speech"]
    assert device.calls == []


# ---------------------------------------------------- folder focus persistence source


def test_folder_focus_persists_the_known_alias_verbatim(db, service) -> None:
    device = build_fake_device_action()

    service.search(db, device, folder="Masaüstü", extensions=[".pdf", ".pptx", ".xlsx"])

    folder = focus_module.current(db, FOCUS_KIND_FOLDER)
    assert folder is not None and folder.object_id == "Masaüstü"


def test_folder_focus_for_a_raw_relative_folder_comes_from_searched_roots_not_the_argument(
    db, service
) -> None:
    """Security review, LOW: a model-supplied ``folder`` string must never itself become
    the persisted focus identity when it is not a name this layer already recognises —
    only the DEVICE's own ``searched_roots`` may. A fake device that resolves the caller's
    relative folder to a different, absolute, real path proves the persisted focus tracks
    THAT, not the raw argument.

    The folder is bucket-prefixed since 2026-09-12 (B03 req 3): a bare relative folder no
    longer reaches the device at all, so the rule this test protects is now exercised with a
    shape the contract actually admits."""
    resolved_root = "C:\\Users\\owner\\Documents\\sozlesmeler\\2026"

    def _search(payload: dict) -> DeviceRunResult:
        assert payload["roots"] == ["documents/sozlesmeler/2026"]
        return DeviceRunResult(
            True,
            result={
                "files": [
                    {
                        "file_id": "file:aaa",
                        "path": f"{resolved_root}\\a.pdf",
                        "name": "a.pdf",
                        "extension": ".pdf",
                        "size": 10,
                        "mtime": "2026-09-08T00:00:00Z",
                    },
                    {
                        "file_id": "file:bbb",
                        "path": f"{resolved_root}\\b.pptx",
                        "name": "b.pptx",
                        "extension": ".pptx",
                        "size": 20,
                        "mtime": "2026-09-08T00:00:00Z",
                    },
                ],
                "truncated": False,
                "searched_roots": [resolved_root],
            },
        )

    device = FakeDeviceAction(results={"file.search": _search})

    receipt = service.search(db, device, folder="documents/sozlesmeler/2026")

    assert receipt["execution_status"] == "executed"
    folder = focus_module.current(db, FOCUS_KIND_FOLDER)
    assert folder is not None
    assert folder.object_id == resolved_root
    assert folder.object_id != "documents/sozlesmeler/2026"
    assert focus_module.current(db, FOCUS_KIND_FILE) is None


def test_an_invalid_folder_never_reaches_focus_even_indirectly(db, service) -> None:
    device = build_fake_device_action()
    service.search(db, device, folder="..\\..\\secrets")
    assert focus_module.current(db, FOCUS_KIND_FOLDER) is None
    assert device.calls == []
