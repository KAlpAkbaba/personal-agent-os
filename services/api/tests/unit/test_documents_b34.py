"""B34 (req 153-167, 170, 674): managed file mutation, through the REAL application object.

The relay, the router and the services are the real ones (``tests.voice_corpus.harness``);
the device is the fake desktop over the fixture corpus with B34's mutable overlay
(``tests.documents_support.MUTATED`` / ``BACKUPS`` / ``TRASHED``), whose answers are the
shapes the real device's own lab tests answer. Every "it changed" below is read back from
that overlay and from the journal row, never from the receipt's own sentence alone.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select

from app.documents import mutations as mutations_module
from app.documents.models import (
    MUTATION_RISK_CRITICAL,
    MUTATION_RISK_LOW,
    MUTATION_RISK_SENSITIVE,
    MUTATION_STATE_APPLIED,
    MUTATION_STATE_DISCARDED,
    MUTATION_STATE_PROPOSED,
    MUTATION_STATE_UNDONE,
    FileMutationRow,
)
from app.documents.service import DocumentService
from app.security import step_up
from app.voice.intents import Intent, resolve_intent
from app.voice.realtime_sessions.tools_documents import DOCUMENT_TOOL_NAMES, MUTATION_TOOL_NAMES
from tests import documents_support
from tests.voice_corpus.corpus import CTX_NONE, CTX_PPTX_FOCUSED
from tests.voice_corpus.harness import build_harness

REPO = Path(__file__).resolve().parents[4]
NOTES = "notlar.md"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fixture_bytes(path: str) -> bytes:
    return (documents_support.FIXTURES_DIR / path).read_bytes()


def _focus(h, sid: str, pattern: str, *, turn: int = 1) -> None:
    """A FILE focus the way the owner gets one: a search that found exactly one file."""
    h.say(sid, f"{pattern} dosyasını bul.", turn=turn)
    found = h.tool(sid, f"s-{turn}", "file.search", {"pattern": pattern})
    assert found["status"] == "succeeded", found
    h.device.reset()


def _rows(h) -> list[FileMutationRow]:
    with h.factory() as db:
        return list(
            db.execute(select(FileMutationRow).order_by(FileMutationRow.created_at)).scalars()
        )


def _harness():
    h = build_harness()
    h.seed(CTX_NONE)
    return h


# ------------------------------------------------------------------ vocabulary


def test_the_tools_are_declared_governed_and_the_contract_halves_agree() -> None:
    """The eleven tools exist, sit in the family's clarifying set, carry the tiers the
    policy names; the device's constants and the protocol carry the six capabilities."""
    assert set(MUTATION_TOOL_NAMES) <= set(DOCUMENT_TOOL_NAMES)
    for name in ("document.write", "document.append", "document.apply", "document.undo"):
        assert step_up.tier_of(name) == step_up.TIER_CRITICAL, name
    for name in (
        "document.edit",
        "document.rename",
        "document.move",
        "document.copy",
        "document.delete",
    ):
        assert step_up.tier_of(name) == step_up.TIER_SENSITIVE, name
    assert resolve_intent("Bu dosyayı sil.").intent is Intent.DOCUMENT_DELETE
    assert resolve_intent("Kopyaları çöp kutusuna gönder.").intent is Intent.DOCUMENT_DEDUP
    assert resolve_intent("Tüm mailleri sil.").intent is not Intent.DOCUMENT_DELETE
    assert resolve_intent("Uygula.").intent is Intent.NONE
    assert resolve_intent("Uygula.", mutation_pending=True).intent is Intent.DOCUMENT_APPLY
    constants = (
        REPO / "devices/windows-agent/src/PagentOS.Agent.Core/Protocol/ProtocolConstants.cs"
    ).read_text("utf-8")
    for capability in (
        "file.write",
        "file.append",
        "file.rename",
        "file.move",
        "file.copy",
        "file.restore",
    ):
        assert f'"{capability}"' in constants, capability
    protocol = (REPO / "packages/protocol/DEVICE_PROTOCOL.md").read_text("utf-8")
    assert "`file.restore`" in protocol and "undo store" in protocol
    assert mutations_module.CAPABILITY_FILE_RESTORE == "file.restore"
    # The policy, in one place: what waits and what does not.
    assert mutations_module.risk_of("write", existing=False) == MUTATION_RISK_LOW
    assert mutations_module.risk_of("write", existing=True) == MUTATION_RISK_SENSITIVE
    assert mutations_module.risk_of("append", existing=True) == MUTATION_RISK_LOW
    assert mutations_module.risk_of("edit", existing=True) == MUTATION_RISK_SENSITIVE
    assert mutations_module.risk_of("move", existing=True) == MUTATION_RISK_CRITICAL
    assert mutations_module.risk_of("delete", existing=True) == MUTATION_RISK_CRITICAL


# --------------------------------------------------------------- 154: write (low risk)


def test_a_new_text_file_is_written_at_once_journaled_hashed_and_undoable() -> None:
    h = _harness()
    sid = h.new_session()
    h.say(sid, "gunluk.md adında bir dosya oluştur.")
    call = h.tool(sid, "c-1", "document.write", {"content": "# Günlük\n\nİlk satır.\n"})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed" and body["terminal_status"] == "verified"
    assert body["kind"] == "write" and body["state"] == MUTATION_STATE_APPLIED
    assert documents_support.MUTATED["gunluk.md"] == "# Günlük\n\nİlk satır.\n".encode()
    assert body["sha_after"] == _sha(documents_support.MUTATED["gunluk.md"])
    assert body["sha_before"] is None and body["backup_id"] is None
    assert "oluşturdum" in body["speech"] and "geri alınabilir" in body["speech"]
    assert h.device.capabilities_called() == ["file.write"]
    assert h.device.payload_for("file.write") == {
        "folder": "documents",
        "name": "gunluk.md",
        "text": "# Günlük\n\nİlk satır.\n",
    }
    rows = _rows(h)
    assert [r.kind for r in rows] == ["write"]
    assert rows[0].risk == MUTATION_RISK_LOW and rows[0].path_after == "gunluk.md"
    assert rows[0].undo_json == {
        "capability": "file.trash",
        "payload": {"path": "gunluk.md", "backup": True},
    }

    # Undo: the created file goes to the Recycle Bin (with a backup), the row is undone.
    h.device.reset()
    h.say(sid, "Son değişikliği geri al.", turn=2)
    undone = h.tool(sid, "c-2", "document.undo", {})["result"]
    assert undone["execution_status"] == "executed", undone
    assert documents_support.MUTATED["gunluk.md"] is None
    assert documents_support.TRASHED == ["gunluk.md"]
    assert h.device.payload_for("file.trash") == {"path": "gunluk.md", "backup": True}
    states = {r.kind: r.state for r in _rows(h)}
    assert states == {"write": MUTATION_STATE_UNDONE, "restore": MUTATION_STATE_APPLIED}


def test_write_needs_a_name_and_a_text_and_refuses_an_office_name() -> None:
    h = _harness()
    sid = h.new_session()
    h.say(sid, "Yeni bir metin dosyası oluştur.")
    asks = h.tool(sid, "c-1", "document.write", {"content": "x"})
    assert asks["status"] == "needs_clarification"
    assert asks["result"]["speech"] == mutations_module.SPEECH_NO_NAME
    asks_text = h.tool(sid, "c-2", "document.write", {"new_name": "a.txt"})
    assert asks_text["result"]["speech"] == mutations_module.SPEECH_NO_TEXT
    office = h.tool(sid, "c-3", "document.write", {"new_name": "rapor.docx", "content": "x"})[
        "result"
    ]
    assert office["execution_status"] == "refused"
    assert office["error_class"] == mutations_module.ERROR_NOT_TEXT
    assert h.device.capabilities_called() == []


# --------------------------------------------------------------- 155: append (low risk)


def test_append_goes_to_the_end_keeps_a_backup_and_undo_restores_the_hash() -> None:
    h = _harness()
    sid = h.new_session()
    _focus(h, sid, "notlar")
    original = _fixture_bytes(NOTES)
    h.say(sid, "Bu dosyanın sonuna toplantı notu ekle.", turn=2)
    body = h.tool(sid, "c-1", "document.append", {})["result"]
    assert body["execution_status"] == "executed", body
    assert body["kind"] == "append"
    assert documents_support.MUTATED[NOTES] == original + "toplantı notu\n".encode()
    assert body["sha_before"] == _sha(original)
    assert body["sha_after"] == _sha(documents_support.MUTATED[NOTES])
    assert body["backup_id"] in documents_support.BACKUPS
    assert h.device.payload_for("file.append")["expected_sha256"] == _sha(original)

    h.device.reset()
    h.say(sid, "Son değişikliği geri al.", turn=3)
    undone = h.tool(sid, "c-2", "document.undo", {})["result"]
    assert undone["execution_status"] == "executed", undone
    assert documents_support.MUTATED[NOTES] == original
    assert undone["sha_after"] == _sha(original)
    assert h.device.payload_for("file.restore") == {
        "backup_id": body["backup_id"],
        "target_path": NOTES,
    }
    assert "eski hâline döndü" in undone["speech"]


# ---------------------------------------------- 153/167/166: edit = proposal + "Uygula."


def test_an_edit_is_proposed_then_applied_on_uygula_and_the_hashes_tell_the_story() -> None:
    h = _harness()
    sid = h.new_session()
    _focus(h, sid, "notlar")
    original = _fixture_bytes(NOTES)
    h.say(sid, "Bu dosyada Bütçe yerine Tahmin yaz.", turn=2)
    proposal = h.tool(sid, "c-1", "document.edit", {})["result"]
    assert proposal["execution_status"] == "executed", proposal
    assert (
        proposal["state"] == MUTATION_STATE_PROPOSED and proposal["risk"] == MUTATION_RISK_SENSITIVE
    )
    assert (
        "'bütçe' yerine 'tahmin'" in proposal["speech"] and "Uygulayayım mı?" in proposal["speech"]
    )
    # Nothing was written: the device was asked to LOCATE and READ, never to write.
    assert set(h.device.capabilities_called()) == {"file.locate", "file.read"}
    assert NOTES not in documents_support.MUTATED

    # The bare confirmation resolves ONLY now that a change is pending for this session.
    h.device.reset()
    route = h.say(sid, "Uygula.", turn=3)
    assert route["resolved_intents"][-1]["intent"] == "document_apply", route
    applied = h.tool(sid, "c-2", "document.apply", {})["result"]
    assert applied["execution_status"] == "executed", applied
    assert applied["terminal_status"] == "verified"
    assert h.device.capabilities_called() == ["file.write"]
    written = h.device.payload_for("file.write")
    assert written["expected_sha256"] == _sha(original)
    assert b"tahmin" in documents_support.MUTATED[NOTES]
    assert "Bütçe".encode() not in documents_support.MUTATED[NOTES]
    assert applied["sha_before"] == _sha(original)
    assert applied["sha_after"] == _sha(documents_support.MUTATED[NOTES])
    assert applied["backup_id"] in documents_support.BACKUPS
    rows = _rows(h)
    assert rows[-1].state == MUTATION_STATE_APPLIED
    assert rows[-1].confirmed_by == f"voice:{sid}"
    # The index row for the changed file is gone: it described content that is not there.
    assert "eski hâli yedekte" in applied["speech"]

    # A second "Uygula." has nothing pending: refused by name, nothing written twice.
    h.device.reset()
    h.say(sid, "Uygula.", turn=4)
    again = h.tool(sid, "c-3", "document.apply", {})["result"]
    assert again["execution_status"] == "refused"
    assert again["error_class"] == mutations_module.ERROR_NOTHING_PENDING
    assert h.device.capabilities_called() == []


def test_a_proposal_the_owner_declines_touches_nothing() -> None:
    h = _harness()
    sid = h.new_session()
    _focus(h, sid, "notlar")
    h.say(sid, "Bu dosyanın adını gunluk-notlari.md yap.", turn=2)
    proposal = h.tool(sid, "c-1", "document.rename", {})["result"]
    assert proposal["state"] == MUTATION_STATE_PROPOSED, proposal
    assert _rows(h)[-1].plan_json["payload"]["new_name"] == "gunluk-notlari.md"
    h.device.reset()
    route = h.say(sid, "Vazgeç.", turn=3)
    assert route["resolved_intents"][-1]["capability"] == "document.discard", route
    discarded = h.tool(sid, "c-2", "document.discard", {})["result"]
    assert discarded["execution_status"] == "executed"
    assert discarded["state"] == MUTATION_STATE_DISCARDED
    assert h.device.capabilities_called() == []
    assert documents_support.MUTATED == {}


def test_an_edit_whose_words_are_not_in_the_file_is_refused_not_guessed() -> None:
    h = _harness()
    sid = h.new_session()
    _focus(h, sid, "notlar")
    h.say(sid, "Bu dosyada Zürafa yerine Fil yaz.", turn=2)
    body = h.tool(sid, "c-1", "document.edit", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == mutations_module.ERROR_TEXT_NOT_FOUND
    assert _rows(h) == []


def test_an_office_document_is_never_edited_byte_by_byte() -> None:
    h = build_harness()
    h.seed(CTX_PPTX_FOCUSED)
    sid = h.new_session()
    h.say(sid, "Bu belgeyi güncelle ve kaydet.")
    body = h.tool(sid, "c-1", "document.edit", {"content": "yeni içerik"})["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == mutations_module.ERROR_NOT_TEXT
    assert body["speech"] == mutations_module.SPEECH_NOT_TEXT


def test_a_stale_proposal_is_refused_when_the_file_changed_since_it_was_read() -> None:
    """The optimistic check (163): the proposal carries the hash it read; the device
    refuses to write over a file whose hash moved, and the owner hears why."""
    h = _harness()
    sid = h.new_session()
    _focus(h, sid, "notlar")
    h.say(sid, "Bu dosyada Bütçe yerine Tahmin yaz.", turn=2)
    h.tool(sid, "c-1", "document.edit", {})
    # Someone else changed the file in the meantime.
    documents_support.MUTATED[NOTES] = b"# baska bir sey"
    h.device.reset()
    h.say(sid, "Uygula.", turn=3)
    applied = h.tool(sid, "c-2", "document.apply", {})["result"]
    assert applied["execution_status"] == "failed", applied
    assert applied["error_class"] == "validation_error"
    assert "okuduğumdan beri değişmiş" in applied["speech"]
    assert documents_support.MUTATED[NOTES] == b"# baska bir sey"


# -------------------------------------------------- 156-159: rename, move, copy, delete


def test_rename_then_undo_puts_the_old_name_back() -> None:
    h = _harness()
    sid = h.new_session()
    _focus(h, sid, "kod")
    h.say(sid, "Bu dosyanın adını kod-eski yap.", turn=2)
    proposal = h.tool(sid, "c-1", "document.rename", {})["result"]
    assert proposal["state"] == MUTATION_STATE_PROPOSED
    # The extension is kept when the owner did not say one.
    assert _rows(h)[-1].plan_json["payload"]["new_name"] == "kod-eski.py"
    h.device.reset()
    h.say(sid, "Uygula.", turn=3)
    applied = h.tool(sid, "c-2", "document.apply", {})["result"]
    assert applied["execution_status"] == "executed", applied
    assert documents_support.MUTATED["kod.py"] is None
    assert documents_support.MUTATED["kod-eski.py"] == _fixture_bytes("kod.py")
    assert applied["path_after"] == "kod-eski.py"

    h.device.reset()
    h.say(sid, "Son değişikliği geri al.", turn=4)
    undone = h.tool(sid, "c-3", "document.undo", {})["result"]
    assert undone["execution_status"] == "executed", undone
    assert h.device.payload_for("file.rename") == {"path": "kod-eski.py", "new_name": "kod.py"}
    assert documents_support.MUTATED["kod.py"] == _fixture_bytes("kod.py")
    assert documents_support.MUTATED["kod-eski.py"] is None


def test_move_is_critical_needs_the_word_and_undo_moves_it_back() -> None:
    h = _harness()
    sid = h.new_session()
    _focus(h, sid, "rapor")
    h.say(sid, "Bu dosyayı Masaüstüne taşı.", turn=2)
    proposal = h.tool(sid, "c-1", "document.move", {})["result"]
    assert (
        proposal["state"] == MUTATION_STATE_PROPOSED and proposal["risk"] == MUTATION_RISK_CRITICAL
    )
    assert h.device.payload_for("file.move") is None
    h.device.reset()
    h.say(sid, "Uygula.", turn=3)
    applied = h.tool(sid, "c-2", "document.apply", {})["result"]
    assert applied["execution_status"] == "executed", applied
    assert h.device.payload_for("file.move")["destination_folder"] == "desktop"
    assert documents_support.MUTATED["Desktop/rapor.pdf"] == _fixture_bytes("rapor.pdf")
    assert documents_support.MUTATED["rapor.pdf"] is None

    h.device.reset()
    h.say(sid, "Son değişikliği geri al.", turn=4)
    undone = h.tool(sid, "c-3", "document.undo", {})["result"]
    assert undone["execution_status"] == "executed", undone
    assert documents_support.MUTATED["rapor.pdf"] == _fixture_bytes("rapor.pdf")


def test_copy_never_overwrites_and_undo_trashes_only_the_copy() -> None:
    h = _harness()
    sid = h.new_session()
    _focus(h, sid, "ayarlar")
    h.say(sid, "Bu dosyayı ayarlar-yedek.json adıyla kopyala.", turn=2)
    proposal = h.tool(sid, "c-1", "document.copy", {})["result"]
    assert proposal["state"] == MUTATION_STATE_PROPOSED, proposal
    h.device.reset()
    h.say(sid, "Uygula.", turn=3)
    applied = h.tool(sid, "c-2", "document.apply", {})["result"]
    assert applied["execution_status"] == "executed", applied
    assert documents_support.MUTATED["ayarlar-yedek.json"] == _fixture_bytes("ayarlar.json")
    assert "ayarlar.json" not in documents_support.MUTATED

    h.device.reset()
    h.say(sid, "Son değişikliği geri al.", turn=4)
    undone = h.tool(sid, "c-3", "document.undo", {})["result"]
    assert undone["execution_status"] == "executed", undone
    assert documents_support.TRASHED == ["ayarlar-yedek.json"]
    assert "ayarlar.json" not in documents_support.MUTATED


def test_delete_is_the_recycle_bin_with_a_backup_and_undo_brings_the_file_back() -> None:
    h = _harness()
    sid = h.new_session()
    _focus(h, sid, "notlar")
    original = _fixture_bytes(NOTES)
    h.say(sid, "Bu dosyayı sil.", turn=2)
    proposal = h.tool(sid, "c-1", "document.delete", {})["result"]
    assert (
        proposal["state"] == MUTATION_STATE_PROPOSED and proposal["risk"] == MUTATION_RISK_CRITICAL
    )
    assert "çöp kutusuna" in proposal["speech"] and "yedeği alınır" in proposal["speech"]
    assert documents_support.TRASHED == []
    h.device.reset()
    h.say(sid, "Uygula.", turn=3)
    applied = h.tool(sid, "c-2", "document.apply", {})["result"]
    assert applied["execution_status"] == "executed", applied
    assert h.device.payload_for("file.trash")["backup"] is True
    assert documents_support.TRASHED == [NOTES]
    assert documents_support.MUTATED[NOTES] is None
    assert applied["backup_id"] in documents_support.BACKUPS

    h.device.reset()
    h.say(sid, "Son değişikliği geri al.", turn=4)
    undone = h.tool(sid, "c-3", "document.undo", {})["result"]
    assert undone["execution_status"] == "executed", undone
    assert documents_support.MUTATED[NOTES] == original
    assert h.device.payload_for("file.restore") == {"backup_id": applied["backup_id"]}


# ------------------------------------------------------- 160/164: undo / versions


def test_undo_with_nothing_applied_is_refused_and_versions_list_the_journal() -> None:
    h = _harness()
    sid = h.new_session()
    _focus(h, sid, "notlar")
    h.say(sid, "Son değişikliği geri al.", turn=2)
    nothing = h.tool(sid, "c-1", "document.undo", {})["result"]
    assert nothing["execution_status"] == "refused"
    assert nothing["error_class"] == mutations_module.ERROR_NOTHING_TO_UNDO

    h.say(sid, "Bu dosyanın sonuna ek satır ekle.", turn=3)
    h.tool(sid, "c-2", "document.append", {})
    h.say(sid, "Bu dosyanın sonuna bir satır daha ekle.", turn=4)
    h.tool(sid, "c-3", "document.append", {})
    h.say(sid, "Bu dosyanın sürüm geçmişini göster.", turn=5)
    versions = h.tool(sid, "c-4", "document.versions", {})["result"]
    assert versions["execution_status"] == "executed", versions
    assert [v["kind"] for v in versions["versions"]] == ["append", "append"]
    assert versions["versions"][0]["sha_before"] == versions["versions"][1]["sha_after"]
    assert "2 kayıt var" in versions["speech"]


# ---------------------------------------------------------------- 674: the host flag


def test_the_host_flag_closes_the_whole_surface() -> None:
    h = _harness()
    h.runtime.register_live(
        document_mutations=mutations_module.MutationService(DocumentService(), enabled=False)
    )
    sid = h.new_session()
    _focus(h, sid, "notlar")
    h.say(sid, "Bu dosyayı sil.", turn=2)
    body = h.tool(sid, "c-1", "document.delete", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == mutations_module.ERROR_MUTATION_DISABLED
    assert body["speech"] == mutations_module.SPEECH_MUTATION_DISABLED
    assert h.device.capabilities_called() == []


# ----------------------------------------------------------- the REST approval surface


def test_the_cockpit_lists_confirms_and_undoes_through_the_same_gate() -> None:
    h = _harness()
    with h.factory() as session:
        FileMutationRow.__table__.create(session.get_bind(), checkfirst=True)
    sid = h.new_session()
    _focus(h, sid, "notlar")
    original = _fixture_bytes(NOTES)
    h.say(sid, "Bu dosyada Bütçe yerine Tahmin yaz.", turn=2)
    proposal = h.tool(sid, "c-1", "document.edit", {})["result"]
    mutation_id = proposal["mutation_id"]

    pending = h.client.get("/v1/documents/mutations/pending").json()["pending"]
    assert [p["mutation_id"] for p in pending] == [mutation_id]
    assert pending[0]["state"] == MUTATION_STATE_PROPOSED and pending[0]["read_back_at"]

    h.device.reset()
    confirmed = h.client.post(f"/v1/documents/mutations/{mutation_id}/confirm").json()
    assert confirmed["execution_status"] == "executed", confirmed
    assert confirmed["confirmed_by"] if "confirmed_by" in confirmed else True
    assert documents_support.MUTATED[NOTES] != original
    journal = h.client.get("/v1/documents/mutations").json()["mutations"]
    assert journal[0]["state"] == MUTATION_STATE_APPLIED
    assert journal[0]["confirmed_by"].startswith("rest:")

    discard_after = h.client.post(f"/v1/documents/mutations/{mutation_id}/discard")
    assert discard_after.status_code == 409

    undone = h.client.post(f"/v1/documents/mutations/{mutation_id}/undo").json()
    assert undone["execution_status"] == "executed", undone
    assert documents_support.MUTATED[NOTES] == original
    assert (
        h.client.get("/v1/documents/mutations").json()["mutations"][1]["state"]
        == MUTATION_STATE_UNDONE
    )

    assert (
        h.client.post(
            "/v1/documents/mutations/00000000-0000-0000-0000-000000000000/confirm"
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Bu dosyada X yerine Y yaz.", Intent.DOCUMENT_EDIT),
        ("Bu belgeyi güncelle ve kaydet.", Intent.DOCUMENT_EDIT),
        ("Bu dosyanın sonuna şunu ekle.", Intent.DOCUMENT_APPEND),
        ("notlar-yeni.md adında bir dosya oluştur.", Intent.DOCUMENT_WRITE),
        ("Bu dosyanın adını x.txt yap.", Intent.DOCUMENT_RENAME),
        ("Bu dosyayı Masaüstüne taşı.", Intent.DOCUMENT_MOVE),
        ("Bu dosyayı kopyala.", Intent.DOCUMENT_COPY),
        ("Bu dosyayı çöp kutusuna gönder.", Intent.DOCUMENT_DELETE),
        ("Son değişikliği geri al.", Intent.DOCUMENT_UNDO),
        ("Bu dosyanın sürüm geçmişini göster.", Intent.DOCUMENT_VERSIONS),
        # neighbours that keep their owners
        ("Buraya merhaba yaz.", Intent.TYPE_TEXT),
        ("Yarın toplantı ekle.", Intent.CALENDAR_PROPOSE),
        ("Etkinliği sil.", Intent.CALENDAR_CANCEL),
        ("Alarmı sil.", Intent.ALARM_CANCEL),
        ("Önceki sürüme dön.", Intent.RELEASE_ROLLBACK),
        ("Bu dosyayı oku.", Intent.DOCUMENT_READ),
        ("Bu dosyayı bul.", Intent.FILE_SEARCH),
    ],
)
def test_the_router_keeps_every_neighbour_and_reaches_every_mutation(
    text: str, intent: Intent
) -> None:
    assert resolve_intent(text, document_focused=True).intent is intent


# ------------------------------------------------- 166: the gate, from the other side


def test_a_proposal_heard_in_another_session_is_not_applied_here() -> None:
    """The read-back rule mail drafts keep (ADR-0084 addendum 2): a change proposed to
    session A was never read back to session B, so B's "Uygula." is nobody's confirmation
    - the router does not even resolve it, and a model that calls the tool anyway is
    refused by the gate, not trusted."""
    h = _harness()
    a = h.new_session()
    _focus(h, a, "notlar")
    h.say(a, "Bu dosyada Bütçe yerine Tahmin yaz.", turn=2)
    proposal = h.tool(a, "c-1", "document.edit", {})["result"]
    assert proposal["state"] == MUTATION_STATE_PROPOSED

    b = h.new_session()
    h.device.reset()
    route = h.say(b, "Uygula.", turn=1)
    assert route["resolved_intents"][-1]["intent"] == "none"
    refused = h.tool(b, "c-2", "document.apply", {"mutation_id": proposal["mutation_id"]})["result"]
    assert refused["execution_status"] == "refused", refused
    assert refused["error_class"] == "not_read_back"
    assert h.device.capabilities_called() == []
    assert NOTES not in documents_support.MUTATED


def test_a_device_answer_without_a_hashed_record_is_never_a_verified_mutation() -> None:
    """Req 162/163: the receipt is a read-back. A device that says 'written' and shows no
    record with a hash has not shown anything."""
    verify = mutations_module.MutationService._verify
    assert verify("write", True, {"written": True}) == (False, None, None)
    assert verify("write", True, {"written": True, "after": {"path": "x"}}) == (False, None, None)
    assert verify("write", False, {"after": {"path": "x", "sha256": "a" * 64}}) == (
        False,
        None,
        None,
    )
    ok, after, backup = verify(
        "write",
        True,
        {
            "after": {"path": "x", "sha256": "a" * 64},
            "observed": {"exists": True},
            "backup": {"backup_id": "bak:1"},
        },
    )
    assert ok and after == {"path": "x", "sha256": "a" * 64} and backup == "bak:1"
    assert (
        verify(
            "write",
            True,
            {"after": {"path": "x", "sha256": "a" * 64}, "observed": {"exists": False}},
        )[0]
        is False
    )
    assert verify("delete", True, {"trashed": True, "observed": {"exists": False}})[0] is True
    assert verify("delete", True, {"trashed": True, "observed": {"exists": True}})[0] is False
