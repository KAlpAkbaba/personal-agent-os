"""B32 req 139-142, 148, 150-152, 169: pictures, archives, full text, duplicates,
preview and the two-document compare - through the real relay, the real tools and the
real DocumentService against the fake device that serves the oracle fixtures.

Every answer is read back from what the fake device (or the index) actually holds; the
Recycle Bin move is proven by the fake's own record of what it trashed and by the plan
having been heard first.
"""

from __future__ import annotations

from pathlib import Path

from app.documents import service as documents_service
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_DOCUMENT
from app.security import step_up
from app.voice.intents import Intent, resolve_intent
from tests import documents_support
from tests.documents_support import doc_id_for, file_id_for
from tests.voice_corpus.corpus import (
    CTX_ARCHIVE_FOCUSED,
    CTX_DOCUMENT_FOCUSED,
    CTX_IMAGE_FOCUSED,
    CTX_NONE,
)
from tests.voice_corpus.harness import build_harness

REPO = Path(__file__).resolve().parents[4]


def _run(context: str, utterance: str, tool: str, arguments: dict | None = None):
    h = build_harness()
    h.seed(context)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, utterance)
    return h, sid, h.tool(sid, "c-1", tool, arguments or {})


# ------------------------------------------------------------------ vocabulary


def test_the_tools_are_declared_governed_and_the_contract_halves_agree() -> None:
    for name in ("document.preview", "document.find_text", "document.duplicates"):
        assert step_up.tier_of(name) == step_up.TIER_SENSITIVE, name
    assert step_up.tier_of("document.dedup") == step_up.TIER_CRITICAL
    assert resolve_intent("Bu iki dokümanı karşılaştır.").intent is Intent.DOCUMENT_COMPARE
    assert resolve_intent("Görseldeki metni oku.").intent is Intent.IMAGE_TEXT
    assert resolve_intent("Ekrandaki metni oku.").intent is Intent.UI_READ
    assert resolve_intent("Dosyayı sil.").intent is not Intent.DOCUMENT_DEDUP
    # Both halves read each other: the device's kinds and its documents family.
    kinds = (
        REPO / "devices/windows-agent/src/PagentOS.SessionCompanion/Documents/FileKinds.cs"
    ).read_text("utf-8")
    assert 'public const string Image = "image"' in kinds
    assert 'public const string Archive = "archive"' in kinds
    protocol = (REPO / "packages/protocol/DEVICE_PROTOCOL.md").read_text("utf-8")
    assert "`file.trash`" in protocol and "image | archive" in protocol
    constants = (
        REPO / "devices/windows-agent/src/PagentOS.Agent.Core/Protocol/ProtocolConstants.cs"
    ).read_text("utf-8")
    assert 'FileTrash = "file.trash"' in constants
    assert documents_service.CAPABILITY_FILE_TRASH == "file.trash"


# ---------------------------------------------------------------- 141 / 139 / 142


def test_the_pictures_text_is_read_from_its_ocr_lines() -> None:
    h, _sid, call = _run(CTX_IMAGE_FOCUSED, "Görseldeki metni oku.", "document.read")
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["doc_id"] == doc_id_for("metin.png")
    assert "Merhaba Dünya 1234" in body["speech"]
    assert "görselinde şu yazıyor" in body["speech"]
    assert h.device.capabilities_called() == [], "already read: the index answers"


def test_a_picture_not_yet_read_is_extracted_through_the_devices_ocr() -> None:
    h = build_harness()
    h.seed(CTX_NONE)
    with h.factory() as db:
        from app.operator.models import FOCUS_KIND_FILE

        focus_module.set_focus(
            db, FOCUS_KIND_FILE, file_id_for("metin.png"), label="metin.png", source="test"
        )
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Resimdeki yazıyı oku.")
    call = h.tool(sid, "c-1", "document.read", {})
    assert call["status"] == "succeeded", call
    assert h.device.capabilities_called() == ["document.extract"]
    assert "Merhaba" in call["result"]["speech"]
    with h.factory() as db:
        assert focus_module.current(db, FOCUS_KIND_DOCUMENT).object_id == doc_id_for("metin.png")


def test_an_archive_is_inspected_from_its_directory_and_never_extracted() -> None:
    h, _sid, call = _run(CTX_ARCHIVE_FOCUSED, "Arşivin içinde ne var?", "document.inspect")
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert h.device.capabilities_called() == ["file.inspect"]
    assert "2 öğe" in body["speech"]
    assert "notlar.md" in body["speech"] and "veri.csv" in body["speech"]
    assert body["structure"]["archive"]["entry_count"] == 2


def test_a_pictures_headers_are_spoken_from_file_inspect() -> None:
    h = build_harness()
    h.seed(CTX_NONE)
    with h.factory() as db:
        from app.operator.models import FOCUS_KIND_FILE

        focus_module.set_focus(
            db, FOCUS_KIND_FILE, file_id_for("metin.png"), label="metin.png", source="test"
        )
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Fotoğrafın bilgilerini oku.")
    call = h.tool(sid, "c-1", "document.inspect", {})
    assert call["status"] == "succeeded", call
    assert h.device.capabilities_called() == ["file.inspect"]
    assert "600x120" in call["result"]["speech"]


# ------------------------------------------------------------------------ 152


def test_preview_speaks_the_kind_its_size_and_the_first_words() -> None:
    h, _sid, call = _run(CTX_DOCUMENT_FOCUSED, "Bu belgeyi önizle.", "document.preview")
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["preview"]["kind"] == "pdf"
    assert "sayfalık PDF" in body["speech"]
    assert "Başı şöyle:" in body["speech"]
    assert "Yıllık Rapor" in body["preview"]["text"]
    assert h.device.capabilities_called() == []


def test_preview_of_an_archive_falls_back_to_its_directory() -> None:
    h, _sid, call = _run(CTX_ARCHIVE_FOCUSED, "Bu dosyayı önizle.", "document.preview")
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["preview"]["kind"] == "headers"
    assert "2 öğe" in body["speech"]
    assert h.device.capabilities_called() == ["document.extract", "file.inspect"]


# ------------------------------------------------------------------------ 148


def test_full_text_finds_the_document_whose_text_carries_the_word() -> None:
    h, _sid, call = _run(
        CTX_DOCUMENT_FOCUSED, "İçinde Hetzner geçen belgeyi bul.", "document.find_text"
    )
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["hits"], body
    assert body["hits"][0]["name"] == "rapor.pdf"
    assert body["hits"][0]["ref"] == "p2"
    assert "rapor" in body["speech"] and "2. sayfa" in body["speech"]
    assert h.device.capabilities_called() == [], "the index answers; no crawl"
    with h.factory() as db:
        assert focus_module.current(db, FOCUS_KIND_DOCUMENT).object_id == doc_id_for("rapor.pdf")


def test_full_text_says_how_many_it_searched_when_nothing_matches() -> None:
    h, _sid, call = _run(
        CTX_DOCUMENT_FOCUSED, "İçinde zürafa geçen belgeyi bul.", "document.find_text"
    )
    body = call["result"]
    assert body["hits"] == []
    assert body["execution_status"] == "executed"
    assert "bulamadım" in body["speech"] and "2 okunmuş belgeye" in body["speech"]


def test_full_text_without_a_word_is_a_question() -> None:
    h = build_harness()
    sid = h.new_session()
    call = h.tool(sid, "c-1", "document.find_text", {})
    assert call["status"] == "needs_clarification"


# ------------------------------------------------------------------ 151 / 150


def test_duplicates_are_found_by_hash_and_proposed_never_moved() -> None:
    documents_support.TRASHED.clear()
    h, sid, call = _run(CTX_NONE, "Yinelenen dosyaları bul.", "document.duplicates")
    assert call["status"] == "succeeded", call
    body = call["result"]
    called = h.device.capabilities_called()
    assert called[0] == "file.search"
    assert set(called[1:]) == {"file.locate"}, "every hashable hit is located, nothing else"
    assert len(body["groups"]) == 1
    group = body["groups"][0]
    names = sorted([group["keep"]["path"], *[m["path"] for m in group["remove"]]])
    assert names == ["veri.csv", "yedek/veri-kopya.csv"]
    assert "1 yinelenen grup" in body["speech"]
    assert documents_support.TRASHED == [], "a proposal moves nothing"


def test_dedup_needs_the_proposal_first_then_trashes_exactly_it() -> None:
    documents_support.TRASHED.clear()
    h = build_harness()
    h.seed(CTX_NONE)
    sid = h.new_session()
    h.say(sid, "Kopyaları çöp kutusuna gönder.")
    refused = h.tool(sid, "c-0", "document.dedup", {})
    assert refused["status"] == "needs_clarification", refused
    assert documents_support.TRASHED == []

    h.say(sid, "Yinelenen dosyaları bul.", turn=2)
    proposal = h.tool(sid, "c-1", "document.duplicates", {})
    assert proposal["status"] == "succeeded"
    remove = [m["path"] for g in proposal["result"]["groups"] for m in g["remove"]]
    h.device.reset()
    h.say(sid, "Kopyaları çöp kutusuna gönder.", turn=3)
    done = h.tool(sid, "c-2", "document.dedup", {})
    assert done["status"] == "succeeded", done
    body = done["result"]
    assert body["execution_status"] == "executed"
    assert body["terminal_status"] == "verified"
    assert h.device.capabilities_called() == ["file.trash"] * len(remove)
    assert documents_support.TRASHED == remove
    assert "çöp kutusuna gönderdim" in body["speech"] and "geri alınabilir" in body["speech"]
    # The plan is spent: a second confirmation is a question again, nothing trashed twice.
    h.device.reset()
    again = h.tool(sid, "c-3", "document.dedup", {})
    assert again["status"] == "needs_clarification"
    assert h.device.capabilities_called() == []


def test_a_trash_the_device_could_not_verify_is_not_a_success() -> None:
    from app.routines.dispatch import DeviceRunResult

    documents_support.TRASHED.clear()
    h = build_harness()
    h.seed(CTX_NONE)
    sid = h.new_session()
    h.say(sid, "Yinelenen dosyaları bul.")
    h.tool(sid, "c-1", "document.duplicates", {})
    h.device.results["file.trash"] = DeviceRunResult(
        True, result={"trashed": False, "method": "recycle_bin", "observed": {"exists": True}}
    )
    h.say(sid, "Kopyaları çöp kutusuna gönder.", turn=2)
    done = h.tool(sid, "c-2", "document.dedup", {})
    body = done["result"]
    assert body["execution_status"] == "failed"
    assert body["trashed"] == [] and len(body["failed"]) == 1
    assert "gönderemedim" in body["speech"]


# ------------------------------------------------------------------------ 169


def test_two_named_documents_are_compared_by_the_executive_step() -> None:
    from app.executive import activities

    source = Path(activities.__file__).read_text("utf-8")
    assert 'kwargs = {"target_a": named[0], "target_b": named[1]}' in source
    assert "service.compare(db, get_device_action(), **kwargs)" in source


def test_bu_iki_dokumani_karsilastir_compares_current_and_previous() -> None:
    h, _sid, call = _run(CTX_DOCUMENT_FOCUSED, "Bu iki dokümanı karşılaştır.", "document.compare")
    assert call["status"] == "succeeded", call
    assert h.device.capabilities_called() == ["file.compare"]
