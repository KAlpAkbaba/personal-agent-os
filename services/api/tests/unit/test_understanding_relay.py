"""ADR-0224 layer 3 through the REAL relay: utterance -> router -> policy -> tool -> device.

The owner's three trial sentences (2026-09-30 20:11 UTC) as the STT rendered them, plus the
three findings the inspection of layer 2 carried into this task. Nothing here builds a turn
record by hand: ``POST .../events`` writes it, ``POST .../tool-calls`` reads it, and the device
port is the real ``BrokerDeviceAction`` over real enrolled rows (only the socket is a recorder).

Every relay test runs twice: with no layer-2 engine (the rule tables and layer 1 alone, which
is what a process whose start-up configured none runs) and with the ``DeterministicEmbedder``
engine seeded from the corpus.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.broker.models import AuditEvent
from app.memory.embedding import DeterministicEmbedder
from app.voice.realtime_sessions.models import RealtimeSessionRow
from app.voice.understanding import combine
from app.voice.understanding.semantic import SemanticEngine, exemplars_from_cases
from tests.unit.test_operator_open_application_fallback import (
    HOME_CAPABILITIES,
    OFFICE_CAPABILITIES,
    _both_online,
    _bound_session,
    _say,
    _tool,
    _wired,
)
from tests.voice_corpus.corpus import all_cases

TRIAL_APP = "Ofisü bilgisayarında hesap makinesini açın."
TRIAL_PLAIN = "Hesap makinesini aç."
TRIAL_LANGUAGE = "Bundan sonra araştırma raporlarını her zaman Türkçe oku."
UNBOUND = "Mutfaktaki bilgisayarda hesap makinesini aç."


@pytest.fixture(scope="module")
def corpus_engine() -> SemanticEngine:
    return SemanticEngine(DeterministicEmbedder(), exemplars_from_cases(all_cases()))


@pytest.fixture(params=["rules_and_layer_one", "with_semantic_engine"])
def layers(request, corpus_engine, monkeypatch) -> str:
    monkeypatch.setattr(
        combine,
        "_default_engine",
        corpus_engine if request.param == "with_semantic_engine" else None,
    )
    return request.param


def _session(monkeypatch, tmp_path, *, world=None):
    world = world or _both_online(monkeypatch, tmp_path)
    return world, _bound_session(world, "MAIL")


def _record(world: Any, sid: str) -> dict[str, Any]:
    with world.factory() as session:
        row = session.get(RealtimeSessionRow, uuid.UUID(sid))
        return dict((row.context_json or {}).get("last_utterance") or {})


def _intent_audits(world: Any) -> list[dict[str, Any]]:
    with world.factory() as session:
        rows = session.execute(
            select(AuditEvent)
            .where(AuditEvent.action == "voice_intent_resolved")
            .order_by(AuditEvent.id)
        ).scalars()
        return [dict(row.metadata_json) for row in rows]


def _launched_on(world: Any) -> set[uuid.UUID]:
    return {call["device_id"] for call in world.commands.calls}


# --- the three trial sentences -----------------------------------------------------------


def test_the_office_sentence_launches_at_the_office_and_reads_it_back(
    layers, monkeypatch, tmp_path
) -> None:
    """Red before: one question (ADR-0233), and before that a launch on MAIL."""
    world, sid = _session(monkeypatch, tmp_path)
    said = _say(world.client, sid, TRIAL_APP)
    resolved = said["resolved_intents"][0]
    assert resolved["intent"] == "app_open" and resolved["tool"] == "operator.app_open"
    assert resolved["band"] == "medium"
    assert resolved["confidence"] == 0.75
    call = _tool(world.client, sid, "operator.app_open", {"application": "Hesap Makinesi"})
    assert call["status"] == "succeeded", call
    assert _launched_on(world) == {world.ids["GMKADIRAKBABA"]}  # never the session's MAIL
    speech = call["result"]["speech"]
    assert speech.startswith("Ofis cihazında Hesap Makinesi açıyorum efendim."), speech
    assert "?" not in speech  # a read-back, never a second confirmation
    assert "açtım" in speech  # ... and the outcome, in the same breath
    record = _record(world, sid)
    assert record["device_targets"] == ["ofis"]
    assert record["machine_named_unbound"] is False


def test_the_plain_sentence_is_high_and_its_receipt_is_unchanged(
    layers, monkeypatch, tmp_path
) -> None:
    world, sid = _session(monkeypatch, tmp_path)
    resolved = _say(world.client, sid, TRIAL_PLAIN)["resolved_intents"][0]
    assert resolved["band"] == "high" and resolved["confidence"] == 1.0
    call = _tool(world.client, sid, "operator.app_open", {"application": "Hesap Makinesi"})
    assert call["status"] == "succeeded", call
    assert call["result"]["speech"] == "Hesap Makinesi açtım efendim."
    assert _launched_on(world) == {world.ids["MAIL"]}
    assert _record(world, sid)["device_targets"] == []


def test_the_language_preference_changes_no_answer_mode(layers, monkeypatch, tmp_path) -> None:
    world, sid = _session(monkeypatch, tmp_path)
    resolved = _say(world.client, sid, TRIAL_LANGUAGE)["resolved_intents"][0]
    assert resolved["intent"] == "none"
    assert resolved["tool"] is None
    record = _record(world, sid)
    assert record["intent"] == "none" and record["answer_level"] is None
    assert record["understanding"].get("question") is None  # nobody is asked anything
    call = _tool(world.client, sid, "research.answer_mode", {"level": "detail"})
    assert call["status"] == "failed", call  # the model's own guess is refused (ADR-0232)
    assert world.commands.calls == []


# --- LOW: exactly one question, nothing dispatched -----------------------------------------


def test_a_low_decision_is_exactly_one_question_and_no_dispatch(
    layers, monkeypatch, tmp_path
) -> None:
    world, sid = _session(monkeypatch, tmp_path)
    said = _say(world.client, sid, UNBOUND)
    resolved = said["resolved_intents"][0]
    assert resolved["band"] == "low"
    assert _record(world, sid)["understanding"]["question"] == (
        "Hangi bilgisayarda: ev mi, ofis mi, iş mi?"
    )
    # The relay itself says nothing: the question rides the tool result, once.
    assert [f for f in said["pending_sideband"] if f.get("event") == "say"] == []
    call = _tool(world.client, sid, "operator.app_open", {"application": "Hesap Makinesi"})
    assert call["result"]["status"] == "needs_clarification", call
    assert call["result"]["refused"] == "understanding_low"  # the relay asked, no handler ran
    assert call["result"]["speech"] == "Hangi bilgisayarda: ev mi, ofis mi, iş mi?"
    assert call["result"]["speech"].count("?") == 1
    assert world.commands.calls == []
    assert _record(world, sid)["device_targets"] == []  # no silent default either


def test_a_low_turn_runs_no_tool_at_all(layers, monkeypatch, tmp_path) -> None:
    """The question belongs to the turn, not to one tool: whatever the model reaches for
    while it is open gets the question and runs nothing."""
    world, sid = _session(monkeypatch, tmp_path)
    _say(world.client, sid, UNBOUND)
    call = _tool(world.client, sid, "operator.shell", {"query": "hostname"})
    assert call["result"]["status"] == "needs_clarification", call
    assert call["result"]["speech"].count("?") == 1
    assert world.commands.calls == []


def test_the_owners_answer_binds_the_device_and_launches_there(
    layers, monkeypatch, tmp_path
) -> None:
    world, sid = _session(monkeypatch, tmp_path)
    _say(world.client, sid, UNBOUND)
    _tool(world.client, sid, "operator.app_open", {"application": "Hesap Makinesi"})
    answered = _say(world.client, sid, "Ofis.")["resolved_intents"][0]
    assert answered["intent"] == "app_open" and answered["tool"] == "operator.app_open"
    assert answered["band"] == "high"
    record = _record(world, sid)
    assert record["device_targets"] == ["ofis"] and record["application"] == "calc"
    assert record["understanding"]["layer"] == "answer"
    call = _tool(world.client, sid, "operator.app_open", {})
    assert call["status"] == "succeeded", call
    assert _launched_on(world) == {world.ids["GMKADIRAKBABA"]}


def test_a_question_is_answered_once(layers, monkeypatch, tmp_path) -> None:
    world, sid = _session(monkeypatch, tmp_path)
    _say(world.client, sid, UNBOUND)
    _say(world.client, sid, "Saat kaç?")  # the owner moved on: the question is closed
    later = _say(world.client, sid, "Ofis.")["resolved_intents"][0]
    assert later["intent"] == "none"
    assert _record(world, sid)["device_targets"] == []


# --- the audit row (KVKK: names and numbers, never the owner's words) ----------------------


def test_the_audit_row_carries_the_understanding_block_and_no_word(
    layers, monkeypatch, tmp_path
) -> None:
    world, sid = _session(monkeypatch, tmp_path)
    _say(world.client, sid, TRIAL_APP)
    audit = _intent_audits(world)[-1]
    block = audit["understanding"]
    assert set(block) == {"layer", "band", "confidence", "candidates"}
    assert block["layer"] == "normalize"
    assert block["band"] == "medium" and block["confidence"] == 0.75
    assert block["candidates"][0] == {"intent": "app_open", "confidence": 1.0}
    assert len(block["candidates"]) <= 3
    dumped = json.dumps(audit, ensure_ascii=False).casefold()
    for word in ("ofisü", "ofisu", "bilgisayar", "hesap", "makine", "açın", "acin"):
        assert word not in dumped, word


def test_the_turn_record_carries_the_new_fields(layers, monkeypatch, tmp_path) -> None:
    """A ResolvedIntent field not copied into the turn record never reaches a tool."""
    world, sid = _session(monkeypatch, tmp_path)
    _say(world.client, sid, TRIAL_APP)
    record = _record(world, sid)
    assert record["confidence"] == 0.75 and record["band"] == "medium"
    assert record["candidates"][0] == ["app_open", 1.0]
    dumped = json.dumps(record["understanding"], ensure_ascii=False).casefold()
    assert "hesap" not in dumped and "açın" not in dumped


# --- carried from the inspection of layer 2 ------------------------------------------------


def test_a_bare_computer_word_is_this_machine_and_asks_nothing(
    layers, monkeypatch, tmp_path
) -> None:
    """ "bilgisayarda" ~ "ev bilgisayarı" scored 0.62-0.64 and yielded the device "ev"."""
    world = _wired(
        monkeypatch,
        tmp_path,
        {
            "MAIL": {"capabilities": HOME_CAPABILITIES, "aliases": ["laptop"]},
            "EVPC": {"capabilities": HOME_CAPABILITIES, "aliases": ["ev", "ev bilgisayarı"]},
            "GMKADIRAKBABA": {"capabilities": OFFICE_CAPABILITIES, "aliases": ["ofis", "iş"]},
        },
    )
    sid = _bound_session(world, "MAIL")
    resolved = _say(world.client, sid, "Bilgisayarda hesap makinesini aç.")["resolved_intents"][0]
    assert resolved["band"] == "high"
    record = _record(world, sid)
    assert record["device_targets"] == [] and record["machine_named_unbound"] is False
    call = _tool(world.client, sid, "operator.app_open", {"application": "Hesap Makinesi"})
    assert call["status"] == "succeeded", call
    assert "?" not in call["result"]["speech"]
    assert _launched_on(world) == {world.ids["MAIL"]}


def test_dont_cancel_the_research_cancels_nothing(layers, monkeypatch, tmp_path) -> None:
    world, sid = _session(monkeypatch, tmp_path)
    resolved = _say(world.client, sid, "Araştırmayı iptal etme.")["resolved_intents"][0]
    assert resolved["intent"] == "none"
    assert resolved["tool"] is None
    assert resolved["band"] != "high"
    block = _intent_audits(world)[-1]["understanding"]
    assert block["band"] != "high"
    if layers == "with_semantic_engine":
        assert block["layer"] == "semantic"
        assert block["candidates"][0]["intent"] == "research_cancel"
        assert block["band"] == "medium"


# --- the model may not name a device --------------------------------------------------------


@pytest.mark.parametrize("key", ["device", "device_id", "cihaz", "bilgisayar"])
def test_a_device_the_model_names_is_refused_and_nothing_runs(
    layers, monkeypatch, tmp_path, key
) -> None:
    world, sid = _session(monkeypatch, tmp_path)
    _say(world.client, sid, TRIAL_PLAIN)
    call = _tool(
        world.client, sid, "operator.app_open", {"application": "Hesap Makinesi", key: "ofis"}
    )
    assert call["status"] == "failed", call
    assert call["error"]["refused"] == "device_slot_forbidden"
    assert call["error"]["keys"] == [key]
    assert call["error"]["speech"].count("?") == 1
    assert world.commands.calls == []
