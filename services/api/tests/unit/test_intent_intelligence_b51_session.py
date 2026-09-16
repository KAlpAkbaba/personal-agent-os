"""B51 through the REAL application object (req 744, 745; docs/DECISIONS.md ADR-0158).

The corpus harness (``tests/voice_corpus/harness.py``) is ``create_app`` with its real
routes, relay, router, tool registry and document service, a fake device behind the broker
port and an in-memory database. Nothing here calls a handler directly:

* 744 - an ambiguous sentence gets its clarification question (recorded on the turn and,
  under the owner's flag, pushed as ONE turn-tagged ``say`` frame in that turn's response),
  the owner answers, and the answer's own tool completes - while the question is never
  re-delivered into, or recorded on, any other turn.
* 745 - "bunu" after a search is the file just found, not the older document that is still
  the current DOCUMENT focus: the resolved reference is READ by the document tools.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from app.voice.intent_router import CompositeIntentRouter, get_intent_router, set_intent_router
from tests.documents_support import file_id_for
from tests.voice_corpus.corpus import CTX_DOCUMENT_FOCUSED
from tests.voice_corpus.harness import Harness, build_harness

CLARIFY_TEXT = "Belge ve alarm konusunda."
CLARIFY_QUESTION = "Hangisini kastettiniz efendim: alarm ya da belge?"


@pytest.fixture()
def harness() -> Iterator[Harness]:
    h = build_harness()
    h.seed(CTX_DOCUMENT_FOCUSED)
    h.device.reset()
    yield h


@pytest.fixture()
def clarify_aloud() -> Iterator[None]:
    """The owner's ``voice_clarify_aloud_enabled`` flag, on for this test only (the router
    ``create_app`` installed reads it from settings; this swaps in the same router with the
    flag on and restores the installed one afterwards)."""
    installed = get_intent_router()
    set_intent_router(CompositeIntentRouter(clarify_aloud=True))
    try:
        yield
    finally:
        set_intent_router(installed)


class SayFrames:
    """Every ``say`` frame this session emitted, by EITHER delivery path: pushed to the
    session's device (the harness's session is device-bound, ``RecordingSideband``) or
    queued on the response's ``pending_sideband`` (a pull-only client)."""

    def __init__(self, h: Harness, sid: str) -> None:
        self._recorder = h.runtime._sideband
        self._sid = sid
        self._seen = 0

    def new(self, response: dict) -> list[dict]:
        pushed = [
            frame
            for _device, frame in self._recorder.frames[self._seen :]
            if frame.get("session_id") == self._sid and frame.get("event") == "say"
        ]
        self._seen = len(self._recorder.frames)
        queued = [f for f in response.get("pending_sideband") or [] if f.get("event") == "say"]
        return pushed + queued


def _turn_record(h: Harness, sid: str) -> dict:
    from sqlalchemy import select

    from app.voice.realtime_sessions.models import RealtimeSessionRow

    with h.factory() as db:
        row = db.execute(
            select(RealtimeSessionRow).where(RealtimeSessionRow.id == uuid.UUID(sid))
        ).scalar_one()
        return dict((row.context_json or {}).get("last_utterance") or {})


# ------------------------------------------------------------------------------ 744


def test_a_clarification_is_asked_answered_and_the_answer_completes_its_tool(
    harness: Harness, clarify_aloud: None
) -> None:
    sid = harness.new_session()
    says = SayFrames(harness, sid)

    # Turn 1: two families named, nothing routed -> a question, recorded and spoken once.
    asked = harness.say(sid, CLARIFY_TEXT, turn=1)
    assert asked["resolved_intents"][0]["intent"] == "none"
    frames = says.new(asked)
    assert len(frames) == 1, frames
    assert frames[0]["payload"] == {
        "text": CLARIFY_QUESTION,
        "turn": 1,
        "purpose": "clarification",
    }
    first = _turn_record(harness, sid)
    assert first["turn"] == 1 and first["clarification_question"] == CLARIFY_QUESTION
    assert first["route_source"] == "none" and first["route_confidence"] == 0.0

    # The client's speech lifecycle for turn 1 (the question was said).
    harness.speak(sid, turn=1, chars=len(CLARIFY_QUESTION))

    # Turn 2: the answer routes by itself, carries no question, and nothing is re-sent.
    answered = harness.say(sid, "Belgeyi özetle.", turn=2)
    assert answered["resolved_intents"][0]["intent"] == "document_summarize"
    assert says.new(answered) == []
    second = _turn_record(harness, sid)
    assert second["turn"] == 2
    assert second["clarification_question"] is None
    assert second["route_source"] == "rule" and second["route_confidence"] == 0.95

    call = harness.tool(sid, "c-2", "document.summarize", {})
    assert call["status"] == "succeeded", call
    result = call["result"]
    assert result["speech"] and "Hangisini" not in result["speech"]

    # The durable session record keeps both turns in order, each with its own intent.
    intents = [i.get("intent") for i in harness.activity(sid).get("intents", [])]
    assert intents == ["none", "document_summarize"], intents

    # A later poll delivers nothing stale: the question rode turn 1's response only.
    later = harness.client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "end_of_turn", "t_ms": 5000, "turn": 3}]},
    ).json()
    assert says.new(later) == []


def test_without_the_flag_the_question_is_recorded_but_never_spoken(harness: Harness) -> None:
    sid = harness.new_session()
    says = SayFrames(harness, sid)
    asked = harness.say(sid, CLARIFY_TEXT, turn=1)
    assert says.new(asked) == []
    assert _turn_record(harness, sid)["clarification_question"] == CLARIFY_QUESTION


def test_a_routed_turn_never_speaks_a_question_even_under_the_flag(
    harness: Harness, clarify_aloud: None
) -> None:
    sid = harness.new_session()
    says = SayFrames(harness, sid)
    said = harness.say(sid, "Bu belgeyi özetle.", turn=1)
    assert said["resolved_intents"][0]["intent"] == "document_summarize"
    assert says.new(said) == []


def test_a_polite_repair_travels_into_the_turn_record(harness: Harness) -> None:
    """A new ResolvedIntent field only reaches a tool if the session copies it: the repair
    label must be on the turn record, and the polite request must complete its tool."""
    sid = harness.new_session()
    said = harness.say(sid, "Bu belgeyi özetler misin acaba?", turn=1)
    resolved = said["resolved_intents"][0]
    assert resolved["intent"] == "document_summarize"
    record = _turn_record(harness, sid)
    assert record["route_repair"] == resolved["route_repair"]
    polite = harness.say(sid, "Bu dosyayı okur musun lütfen?", turn=2)
    assert polite["resolved_intents"][0]["intent"] == "document_read"
    assert (
        _turn_record(harness, sid)["route_repair"] == polite["resolved_intents"][0]["route_repair"]
    )
    kopya = harness.say(sid, "Bu dosyayı kopyalar mısın?", turn=3)
    assert kopya["resolved_intents"][0]["intent"] == "document_copy"
    assert kopya["resolved_intents"][0]["route_repair"] == "polite"
    record = _turn_record(harness, sid)
    assert record["turn"] == 3 and record["route_repair"] == "polite"
    assert record["route_confidence"] == 0.85


# ------------------------------------------------------------------------------ 745


SEARCHED = "notlar.md"


def test_bunu_after_a_search_summarises_the_file_just_found(harness: Harness) -> None:
    """Regression (745): the deictic reference was recorded and read by nothing, so
    "Bunu özetle." after finding a notes file summarised rapor.pdf - the older document that
    was still the current DOCUMENT focus - instead of the file the owner had just heard."""
    sid = harness.new_session()
    harness.say(sid, "notlar dosyasını bul.", turn=1)
    found = harness.tool(sid, "c-1", "file.search", {})
    assert found["status"] == "succeeded", found
    assert [f["file_id"] for f in found["result"]["files"]] == [file_id_for(SEARCHED)]

    harness.device.reset()
    said = harness.say(sid, "Bunu özetle.", turn=2)
    assert said["resolved_intents"][0]["intent"] == "document_summarize"
    reference = _turn_record(harness, sid)["deictic_reference"]
    assert reference["kind"] == "file" and reference["object_id"] == file_id_for(SEARCHED)

    summary = harness.tool(sid, "c-2", "document.summarize", {})
    assert summary["status"] == "succeeded", summary
    assert summary["result"]["observed_after"]["server"]["file_id"] == file_id_for(SEARCHED)
    # The notes file was read from the device (it had never been extracted) - once.
    extracted = [c for c in harness.device.calls if c["capability"] == "document.extract"]
    assert [c["payload"].get("file_id") for c in extracted] == [file_id_for(SEARCHED)]

    # ...and the next "bunu" is the same file, now from the index, no device call.
    harness.device.reset()
    harness.say(sid, "Bunu bir daha özetle.", turn=3)
    again = harness.tool(sid, "c-3", "document.summarize", {})
    assert again["result"]["observed_after"]["server"]["file_id"] == file_id_for(SEARCHED)
    assert not [c for c in harness.device.calls if c["capability"] == "document.extract"]


def test_bunu_with_nothing_newer_is_still_the_current_document(harness: Harness) -> None:
    sid = harness.new_session()
    harness.say(sid, "Bunu özetle.", turn=1)
    summary = harness.tool(sid, "c-1", "document.summarize", {})
    assert summary["status"] == "succeeded", summary
    assert summary["result"]["observed_after"]["server"]["file_id"] == file_id_for("rapor.pdf")
    assert not [c for c in harness.device.calls if c["capability"] == "document.extract"]


def test_inspect_through_a_reference_never_falls_back_to_an_older_document(
    harness: Harness,
) -> None:
    """A found-but-unread file is inspected by its own headers, not answered from the index
    entry of the older document."""
    sid = harness.new_session()
    harness.say(sid, "notlar dosyasını bul.", turn=1)
    harness.tool(sid, "c-1", "file.search", {})
    harness.device.reset()
    harness.say(sid, "Bunda ne var?", turn=2)
    if _turn_record(harness, sid).get("deictic_reference") is None:
        pytest.fail("'Bunda' must resolve to the fresh file focus")
    inspected = harness.tool(sid, "c-2", "document.inspect", {})
    calls = [c for c in harness.device.calls if c["capability"] == "file.inspect"]
    assert [c["payload"].get("file_id") for c in calls] == [file_id_for(SEARCHED)], inspected
