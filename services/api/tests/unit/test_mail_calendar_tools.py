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


def test_gonder_in_a_new_session_for_a_draft_read_back_in_the_old_one_never_sends() -> None:
    """M21 security review, ADR-0084 addendum 2 — the second of the two negatives the
    brief names: the read-back happened for real, through the real tool, in session A;
    a DIFFERENT session B then says "Gönder." for the very same draft (the corpus's
    generic per-case single-session shape cannot express this, hence a dedicated test
    here rather than a declarative UtteranceCase). Session B never heard this draft read
    back to IT — the confirmation gate refuses the same way an unread draft would,
    never a guess that the owner remembers a different conversation."""
    h = build_harness()
    h.seed(CTX_DRAFT_READ_BACK)  # focuses Ali's latest message + prepares a reply draft
    session_a = h.new_session()
    read_back = h.tool(session_a, "c-1", "mail.read_draft", {})
    assert read_back["status"] == "succeeded", read_back

    session_b = h.new_session()
    h.say(session_b, "Gönder.", turn=1)
    sent = h.tool(session_b, "c-2", "mail.send", {})
    assert sent["result"]["execution_status"] == "refused"
    assert sent["result"]["error_class"] in ("not_read_back", "confirmation_not_owner")
    assert h.mail._sender.sent == []  # type: ignore[attr-defined]

    # The draft is still exactly where session A left it - read back, never sent - so
    # session A itself can still legitimately confirm it afterward.
    h.say(session_a, "Gönder.", turn=2)
    sent_for_real = h.tool(session_a, "c-3", "mail.send", {})
    assert sent_for_real["result"]["execution_status"] == "executed"
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


def test_a_proposal_is_committed_in_the_same_turn_and_exactly_once() -> None:
    """Owner decision 2026-09-19 ("Tüm 2. ses onaylarını kaldır, mail hariç"): "Perşembe
    15'e diş hekimi ekle." is on the calendar when the sentence ends. The gate was walked,
    not skipped: read back on this session and turn, then committed under the owner's
    standing decision - one fake create, and a later "Onayla." finds nothing pending."""
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Perşembe 15'e diş hekimi ekle.")
    done = h.tool(
        sid, "c-1", "calendar.propose", {"when_spoken": "Perşembe 15'e", "summary": "Diş hekimi"}
    )
    assert done["status"] == "succeeded", done
    assert done["result"]["execution_status"] == "executed", done["result"]
    assert done["result"]["capability"] == "calendar.commit"
    assert len(h.calendar._writer.created) == 1  # type: ignore[attr-defined]

    h.say(sid, "Onayla.", turn=2)
    again = h.tool(sid, "c-2", "calendar.commit", {})
    assert again["result"].get("execution_status") != "executed", again
    assert len(h.calendar._writer.created) == 1  # type: ignore[attr-defined]


def test_without_an_account_the_proposal_stands_and_is_spoken_as_before() -> None:
    """B46 is deferred - no calendar account. The first-word commit cannot happen, so the
    proposal is kept and read back exactly as it always was; nothing is lost."""
    h = build_harness()
    h.calendar._account_configured = False  # type: ignore[attr-defined]
    sid = h.new_session()
    h.say(sid, "Perşembe 15'e diş hekimi ekle.")
    proposed = h.tool(
        sid, "c-1", "calendar.propose", {"when_spoken": "Perşembe 15'e", "summary": "Diş hekimi"}
    )
    assert proposed["status"] == "succeeded", proposed
    assert proposed["result"]["proposal"]["summary"] == "Diş hekimi"
    assert h.calendar._writer.created == []  # type: ignore[attr-defined]


def test_a_model_issued_propose_with_no_owner_sentence_does_not_commit() -> None:
    """The standing decision replaces the owner's SECOND word, never the first: a
    calendar.propose the model calls on its own (no CALENDAR_PROPOSE turn behind it) is
    still a proposal, and reaches the writer zero times."""
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Bugün hava nasıl?")
    proposed = h.tool(
        sid, "c-1", "calendar.propose", {"when_spoken": "Perşembe 15'e", "summary": "Diş hekimi"}
    )
    assert proposed["status"] == "succeeded", proposed
    assert (
        proposed["result"].get("execution_status") != "executed"
        or proposed["result"].get("capability") != "calendar.commit"
    )
    assert h.calendar._writer.created == []  # type: ignore[attr-defined]


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
