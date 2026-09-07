"""File & Document Intelligence's voice tools, through the REAL application object
(docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3, §4) — the same relay/router/tool path
the corpus uses (``tests/voice_corpus``), narrowed here to the specific contracts that
category covers in aggregate: an utterance -> the tool -> ``DocumentService`` -> the fake
device -> the receipt and its speech; the "sil" negative (ADR-0083 decision 7); the
secret-bearing refusal; ``document.previous`` swapping the focus stack; no device ->
``capability_missing``.

Reuses ``tests.voice_corpus.harness.build_harness`` (the same wiring
``test_owner_utterance_corpus.py`` drives 686 cases through) rather than re-deriving the
identity/broker/session boilerplate ``test_operator_tools.py``'s own ``_wired`` keeps —
one harness, one seam, for every voice-tool test suite in this repository.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.documents.service import DocumentService
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_DOCUMENT
from app.voice.realtime_sessions.tools import ToolContext
from app.voice.realtime_sessions.tools_documents import document_read
from tests.documents_support import doc_id_for, file_id_for
from tests.voice_corpus.corpus import (
    CTX_DOCUMENT_FOCUSED,
    CTX_DOCX_FOCUSED,
    CTX_FILE_FOCUSED,
    CTX_NONE,
    CTX_SECRET_FILE_FOCUSED,
)
from tests.voice_corpus.harness import build_harness

# --------------------------------------------------------------------------- read


def test_document_read_extracts_a_focused_but_unread_file_and_indexes_it() -> None:
    """Spec §3's own deixis branch: a FILE focused (a search hit) but not yet extracted
    -> ``document.read`` extracts it first, through the real ``document.extract`` fake
    device capability — never a cached guess."""
    h = build_harness()
    h.seed(CTX_FILE_FOCUSED)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Bu dosyayı oku.")
    call = h.tool(sid, "c-1", "document.read", {})

    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert body["file_id"] == file_id_for("rapor.pdf")
    assert body["doc_id"] == doc_id_for("rapor.pdf")
    assert "rapor" in body["speech"] and "rapor.pdf" not in body["speech"]
    assert h.device.capabilities_called() == ["document.extract"]

    with h.factory() as db:
        current = focus_module.current(db, FOCUS_KIND_DOCUMENT)
    assert current is not None
    assert current.object_id == doc_id_for("rapor.pdf")


def test_document_read_of_the_already_current_document_never_re_extracts() -> None:
    """ "No background crawling" (ADR-0083 decision 3): a re-read of the current document
    is served from the index, not re-fetched from the device."""
    h = build_harness()
    h.seed(CTX_DOCUMENT_FOCUSED)  # rapor.pdf already indexed and current
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Bu belgeyi tekrar oku.")
    call = h.tool(sid, "c-1", "document.read", {})

    assert call["status"] == "succeeded", call
    assert call["result"]["file_id"] == file_id_for("rapor.pdf")
    assert h.device.calls == []


# ------------------------------------------------------------------------- answer


def test_document_answer_names_the_place_and_cites_a_ref_with_an_excerpt() -> None:
    """ "Ödeme süresi kaç gün?" against the focused payment contract: the SAME oracle
    truth.json's own ``docx-clause-3`` question checks (spec §3's own reference scheme)."""
    h = build_harness()
    h.seed(CTX_DOCX_FOCUSED)
    sid = h.new_session()
    h.say(sid, "Ödeme süresi kaç gün?")
    call = h.tool(sid, "c-1", "document.answer", {"question": "Ödeme süresi kaç gün?"})

    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["found"] is True
    assert "45 gün" in body["speech"]
    refs = body["refs"]
    assert any(r["ref"] == "p8" and "45 gün" in r["excerpt"] for r in refs), refs


def test_document_answer_with_no_document_focused_is_a_clarification() -> None:
    h = build_harness()
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Ödeme süresi kaç gün?")
    call = h.tool(sid, "c-1", "document.answer", {"question": "Ödeme süresi kaç gün?"})
    assert call["status"] == "needs_clarification", call
    assert call["result"]["speech"] == "Hangi belge?"
    assert h.device.calls == []


# ------------------------------------------------------------------------ inspect


def test_document_inspect_of_a_focused_but_unread_file_uses_file_inspect_not_extract() -> None:
    """Spec §3: inspect reads STRUCTURE only ("İçeriği OKUMAZ, yapıyı söyler" - the
    tool's own description) - a file focused but never extracted must be inspected
    through the lightweight ``file.inspect`` capability, never a full
    ``document.extract``, and the index must stay untouched (inspecting is not reading)."""
    h = build_harness()
    h.seed(CTX_FILE_FOCUSED)  # rapor.pdf focused as a FILE, never extracted
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Bu dosyada ne var?")
    call = h.tool(sid, "c-1", "document.inspect", {})

    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    # The name is SAID without its extension (loopback finding: an extension is not a word).
    assert "rapor" in body["speech"] and "rapor.pdf" not in body["speech"]
    assert "5" in body["speech"]  # rapor.pdf has 5 pages (truth.json)
    assert h.device.capabilities_called() == ["file.inspect"]

    with h.factory() as db:
        from app.documents.index import DocumentIndex

        inspected_file_id = body["observed_after"]["server"]["file_id"]
        assert (
            DocumentIndex().get_by_file_id(
                db, device_id="device:default", file_id=inspected_file_id
            )
            is None
        )


# ---------------------------------------------------------------------- summarize


def test_document_summarize_with_no_focus_asks_which_document_and_touches_nothing() -> None:
    h = build_harness()
    h.seed(CTX_NONE)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Bu belgeyi özetle.")
    call = h.tool(sid, "c-1", "document.summarize", {})
    assert call["status"] == "needs_clarification", call
    assert call["result"]["speech"] == "Hangi belge?"
    assert h.device.calls == []


# ------------------------------------------------------------------- the "sil" negative


def test_delete_reaches_no_tool_and_no_device_capability() -> None:
    """ADR-0083 decision 7: there is no delete/move/write tool in M20 at all - "Bu dosyayı
    sil." must resolve to no intent this family owns, and the fake device must never see
    a single call, whether or not a file happens to be focused."""
    h = build_harness()
    h.seed(CTX_FILE_FOCUSED)
    sid = h.new_session()
    h.device.reset()
    said = h.say(sid, "Bu dosyayı sil.")
    resolved = said["resolved_intents"][0]
    assert resolved.get("intent") == "none"
    assert not resolved.get("capability")
    assert h.device.calls == []


# ------------------------------------------------------------------- secret refusal


def test_a_secret_bearing_focused_file_is_refused_without_leaking_content() -> None:
    """Spec §2's confinement rule: the tool IS called (the owner asked, honestly), and the
    fake device refuses with ``permission_denied`` - no content leaked, no invented
    summary of what the file might hold."""
    h = build_harness()
    h.seed(CTX_SECRET_FILE_FOCUSED)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Şifre dosyamı oku.")
    call = h.tool(sid, "c-1", "document.read", {})

    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "permission_denied"
    assert "izin verilen klasörlerin dışında" in body["speech"]
    assert h.device.capabilities_called() == ["document.extract"]


# ----------------------------------------------------------------------- previous


def test_document_previous_swaps_the_focus_stack() -> None:
    h = build_harness()
    h.seed(CTX_DOCUMENT_FOCUSED)  # current: rapor.pdf, previous: sunum-q3.pptx
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Bir önceki belgeye dön.")
    call = h.tool(sid, "c-1", "document.previous", {})

    assert call["status"] == "succeeded", call
    body = call["result"]
    assert "sunum q3" in body["speech"] and "pptx" not in body["speech"]
    assert h.device.calls == []

    with h.factory() as db:
        current = focus_module.current(db, FOCUS_KIND_DOCUMENT)
        previous = focus_module.previous(db, FOCUS_KIND_DOCUMENT)
    assert current is not None and current.object_id == doc_id_for("sunum-q3.pptx")
    assert previous is not None and previous.object_id == doc_id_for("rapor.pdf")


# ------------------------------------------------------------------ capability_missing


def test_no_device_action_is_a_capability_missing_receipt() -> None:
    """No ``device_action`` on ``ctx.live`` at all (the honest production answer today:
    the deployed 0.1.0 agent advertises no ``documents`` capability). Exercises the real
    tool handler and ``DocumentService`` directly (a hand-built ``ToolContext``, the same
    seam ``tools_operator``'s own no-device test reaches by omitting ``device_action``
    from a real app's live sources) rather than standing up a second full application.
    """
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        ctx = ToolContext(
            session_id=uuid4(),
            owner_session_id=uuid4(),
            device_id=None,
            client_kind="desktop",
            context={},
            db=db,
            now=datetime.now(UTC),
            live={"document_service": DocumentService()},
        )
        result = document_read(ctx, {})
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "capability_missing"
    assert "belge okuma yetkisi yok" in result["speech"]
