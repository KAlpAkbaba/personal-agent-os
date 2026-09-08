"""Mail & Calendar's voice tools, through the REAL application object
(docs/M21_MAIL_CALENDAR_SPEC.md §3, §4) — the same relay/router/tool path the corpus uses
(``tests/voice_corpus``), narrowed here to precise end-to-end flows the corpus's generic
routing checks do not pin: the exact draft/proposal content read back, the fake sender/
writer recording exactly what was confirmed, and the DISCARD disambiguation between a
pending draft and a pending proposal.

Reuses ``tests.voice_corpus.harness.build_harness`` — one harness, one seam, for every
voice-tool test suite in this repository (the same discipline ``test_documents_tools.py``
already establishes).
"""

from __future__ import annotations

from tests.voice_corpus.corpus import CTX_DRAFT_READ_BACK, CTX_EVENT_FOCUSED, CTX_PROPOSAL_READ_BACK
from tests.voice_corpus.harness import build_harness

# ------------------------------------------------------------------------------- mail


def test_draft_reply_read_send_is_exactly_one_fake_send() -> None:
    h = build_harness()
    h.seed(CTX_DRAFT_READ_BACK)  # focuses Ali's latest message + prepares a reply draft
    sid = h.new_session()

    read_back = h.tool(sid, "c-1", "mail.read_draft", {})
    assert read_back["status"] == "succeeded", read_back
    speech = read_back["result"]["speech"]
    assert "ayse.kaya@example.com" not in speech  # this draft replies to Ali, not Ayşe
    assert "Yarın 10'da uygunum" in speech

    h.say(sid, "Gönder.", turn=2)
    sent = h.tool(sid, "c-2", "mail.send", {})
    assert sent["status"] == "succeeded", sent
    assert sent["result"]["execution_status"] == "executed"
    assert len(h.mail._sender.sent) == 1  # type: ignore[attr-defined]
    assert sent["result"]["draft"]["state"] == "sent"

    # A second confirmation never sends twice (spec §1, ADR-0084 decision 1).
    again = h.tool(sid, "c-3", "mail.send", {})
    assert again["result"]["execution_status"] == "refused"
    assert len(h.mail._sender.sent) == 1  # type: ignore[attr-defined]


def test_draft_new_names_the_recipient_and_never_sends_on_its_own() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Ali'ye mail gönder.")
    result = h.tool(
        sid,
        "c-1",
        "mail.draft",
        {"to": "ali.yilmaz@example.com", "subject": "Merhaba", "body": "Merhaba Ali,"},
    )
    assert result["status"] == "succeeded", result
    draft = result["result"]["draft"]
    assert draft["to"] == ["ali.yilmaz@example.com"]
    assert draft["state"] == "prepared"
    assert h.mail._sender.sent == []  # type: ignore[attr-defined]


def test_edit_draft_changes_the_subject_and_re_reads_back() -> None:
    h = build_harness()
    h.seed(CTX_DRAFT_READ_BACK)
    sid = h.new_session()
    result = h.tool(sid, "c-1", "mail.edit_draft", {"subject": "Plan onayı"})
    assert result["status"] == "succeeded", result
    assert result["result"]["draft"]["subject"] == "Plan onayı"
    assert "Plan onayı" in result["result"]["speech"]


def test_mail_send_with_nothing_prepared_clarifies_and_never_touches_the_sender() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Gönder.")
    result = h.tool(sid, "c-1", "mail.send", {})
    assert result["status"] == "needs_clarification", result
    assert result["result"]["speech"] == "Neyi göndereyim?"
    assert h.mail._sender.sent == []  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- calendar


def test_propose_read_commit_is_exactly_one_fake_create() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Perşembe 15'e diş hekimi ekle.")
    proposed = h.tool(
        sid, "c-1", "calendar.propose", {"when_spoken": "Perşembe 15'e", "summary": "Diş hekimi"}
    )
    assert proposed["status"] == "succeeded", proposed
    assert proposed["result"]["proposal"]["summary"] == "Diş hekimi"

    h.say(sid, "Onayla.", turn=2)
    committed = h.tool(sid, "c-2", "calendar.commit", {})
    assert committed["status"] == "succeeded", committed
    assert committed["result"]["execution_status"] == "executed"
    assert len(h.calendar._writer.created) == 1  # type: ignore[attr-defined]

    again = h.tool(sid, "c-3", "calendar.commit", {})
    assert again["result"]["execution_status"] == "refused"
    assert len(h.calendar._writer.created) == 1  # type: ignore[attr-defined]


def test_reschedule_proposal_names_the_focused_event() -> None:
    h = build_harness()
    h.seed(CTX_EVENT_FOCUSED)  # "Diş hekimi" (ev-dis@fixture.example) is the current event
    sid = h.new_session()
    h.say(sid, "Bunu bir saat ertele.")
    result = h.tool(sid, "c-1", "calendar.propose", {"when_spoken": "bir saat"})
    assert result["status"] == "succeeded", result
    proposal = result["result"]["proposal"]
    assert proposal["kind"] == "reschedule"
    assert proposal["event_uid"] == "ev-dis@fixture.example"


def test_calendar_commit_with_nothing_prepared_clarifies() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Onayla.")
    result = h.tool(sid, "c-1", "calendar.commit", {})
    assert result["status"] == "needs_clarification", result
    assert h.calendar._writer.created == []  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------- discard


def test_discard_targets_the_draft_when_a_draft_is_pending() -> None:
    h = build_harness()
    h.seed(CTX_DRAFT_READ_BACK)
    sid = h.new_session()
    said = h.say(sid, "Vazgeç.")
    assert said["resolved_intents"][0]["capability"] == "mail.discard"
    result = h.tool(sid, "c-1", "mail.discard", {})
    assert result["result"]["draft"]["state"] == "discarded"


def test_discard_targets_the_proposal_when_a_proposal_is_pending() -> None:
    h = build_harness()
    h.seed(CTX_PROPOSAL_READ_BACK)
    sid = h.new_session()
    said = h.say(sid, "Vazgeç.")
    assert said["resolved_intents"][0]["capability"] == "calendar.discard"
    result = h.tool(sid, "c-1", "calendar.discard", {})
    assert result["result"]["proposal"]["state"] == "discarded"
