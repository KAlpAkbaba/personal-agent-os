"""ADR-0196 (owner note 2, 2026-09-21): "yeni hareket oluştur ... hareketi bitir ... yeni
mail sekmesi ... yeni mail sekmesi aç".

The whole conversation through the REAL relay against the fake device: a recording is
started, two things are done (and kept), the recording is ended, a name is asked for and
given, and the name alone replays the two things - the same device calls, in order. Then
the edges: a replay stops at the first step that did not succeed and says which; every
replayed step meets the step-up gate on its own name; a clarification is not a step; a
name said twice replaces; delete makes the name a plain sentence again.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.macros import service as macros_service
from app.routines.dispatch import DeviceRunResult
from app.security import step_up
from app.voice.intents import Intent, resolve_intent
from app.voice.realtime_sessions import tools_macros
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall
from tests.alarms_support import window_id as window_id_for
from tests.unit.test_operator_tools import _create, _focus_window, _say, _tool, _wired


def _tool_named(answer: dict, intent: str) -> str | None:
    resolved = answer["resolved_intents"]
    assert resolved and resolved[-1]["intent"] == intent, resolved
    return resolved[-1]["tool"]


# ------------------------------------------------------------------- the words


@pytest.mark.parametrize(
    ("text", "intent", "name"),
    [
        ("Yeni hareket oluştur.", Intent.MACRO_RECORD_START, None),
        ("Yeni hareket oluşturalım.", Intent.MACRO_RECORD_START, None),
        ("Yeni bir hareket başlat.", Intent.MACRO_RECORD_START, None),
        ("Hareket kaydet.", Intent.MACRO_RECORD_START, None),
        ("Hareket kaydı başlat.", Intent.MACRO_RECORD_START, None),
        ("Hareketi bitir.", Intent.MACRO_RECORD_END, None),
        ("Hareketi tamamla.", Intent.MACRO_RECORD_END, None),
        ("Hareket bitti.", Intent.MACRO_RECORD_END, None),
        ("Hareketi iptal et.", Intent.MACRO_RECORD_CANCEL, None),
        ("Hangi hareketlerim var?", Intent.MACRO_LIST, None),
        ("Hareketleri listele.", Intent.MACRO_LIST, None),
        ("Yeni mail sekmesi hareketini sil.", Intent.MACRO_DELETE, "yeni mail sekmesi"),
        ("Yeni mail sekmesi hareketini çalıştır.", Intent.MACRO_RUN, "yeni mail sekmesi"),
        ("Hareketi sil.", Intent.MACRO_DELETE, None),
    ],
)
def test_the_macro_words(text: str, intent: Intent, name: str | None) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is intent, resolved
    assert resolved.macro_name == name


@pytest.mark.parametrize(
    "text",
    [
        "Resmi hareketlendir.",
        "Bütün hareketleri sil.",
        "Enter'a bas.",
        "Kamerayı kapat.",
        # Review 2026-09-21: "unutma" is REMEMBER (the repo's own lesson), never a delete.
        "Hareketi unutma.",
        "Yeni mail sekmesi hareketini unutma.",
    ],
)
def test_what_is_not_a_macro_word(text: str) -> None:
    assert not resolve_intent(text).intent.value.startswith("macro_")


def test_forget_is_a_delete_only_as_the_exact_imperative() -> None:
    resolved = resolve_intent("Yeni mail sekmesi hareketini unut.")
    assert resolved.intent is Intent.MACRO_DELETE and resolved.macro_name == "yeni mail sekmesi"


def test_stopping_a_running_operator_task_is_never_a_macro_word() -> None:
    """Review 2026-09-21: "durdur" is a STOP_TOKEN; "hareketi durdur" while a Digital
    Operator task runs is the owner stopping THAT, and the macro words must not shadow it."""
    assert (
        resolve_intent("Hareketi durdur.", operator_running=True).intent is Intent.OPERATOR_CANCEL
    )
    assert resolve_intent("Hareketi durdur.").intent is not Intent.MACRO_RECORD_END
    assert (
        resolve_intent("Hareketi bitir.", operator_running=True).intent is Intent.MACRO_RECORD_END
    )


def test_a_stored_name_is_a_run_and_only_when_the_sentence_is_the_name() -> None:
    names = ("yeni mail sekmesi",)
    for text in ("Yeni mail sekmesi aç.", "yeni mail sekmesini aç", "Yeni mail sekmesi."):
        resolved = resolve_intent(text, macro_names=names)
        assert resolved.intent is Intent.MACRO_RUN, (text, resolved)
        assert resolved.macro_name == "yeni mail sekmesi"
    # Without the stored name the same sentence is whatever it was before.
    assert resolve_intent("Yeni mail sekmesi aç.").intent is not Intent.MACRO_RUN
    # A name buried in a longer sentence is not a run.
    assert resolve_intent("Yeni mail sekmesi açıp bir şey yaz.", macro_names=names).intent is not (
        Intent.MACRO_RUN
    )
    # A stored name never steals the key press it resembles.
    resolved = resolve_intent("sağ tuşuna bas", macro_names=("sag tus",))
    assert resolved.intent is Intent.OPERATOR_KEY and resolved.key_press == "right"


def test_while_a_name_is_awaited_the_sentence_is_the_name() -> None:
    resolved = resolve_intent("Yeni mail sekmesi.", macro_awaiting_name=True)
    assert resolved.intent is Intent.MACRO_NAME and resolved.macro_name == "Yeni mail sekmesi"
    resolved = resolve_intent("Adı yeni mail sekmesi olsun.", macro_awaiting_name=True)
    assert resolved.macro_name == "yeni mail sekmesi"
    assert resolve_intent("Vazgeç.", macro_awaiting_name=True).intent is Intent.MACRO_RECORD_CANCEL
    # The privacy stop still outranks the name.
    assert resolve_intent("Kamerayı kapat.", macro_awaiting_name=True).intent is Intent.EYE_DISABLE
    # "Enter'a bas" while a name is awaited IS the name - the owner was asked for one.
    resolved = resolve_intent("Enter'a bas.", macro_awaiting_name=True)
    assert resolved.intent is Intent.MACRO_NAME and resolved.macro_name == "Enter'a bas"
    # ...but a stop word is a stop, even then (security review 2026-09-21), and a stored
    # macro under a stop word never runs in its place.
    for word in ("Dur.", "Kes.", "Yeter."):
        assert resolve_intent(word, macro_awaiting_name=True).intent is not Intent.MACRO_NAME
        assert resolve_intent(word, macro_names=("dur", "kes", "yeter")).intent is not (
            Intent.MACRO_RUN
        )


# ------------------------------------------------------------ the conversation


def _record_two_steps(client, sid: str) -> None:
    answer = _say(client, sid, "Yeni hareket oluştur.")
    assert _tool_named(answer, "macro_record_start") == "macro.record_start"
    started = _tool(client, sid, "macro.record_start", {})
    assert started["status"] == "succeeded", started
    assert started["result"]["status"] == "recording"
    assert started["result"]["speech"] == tools_macros.SPEECH_RECORDING_TR
    _say(client, sid, "Enter'a bas.")
    assert _tool(client, sid, "operator.key", {})["result"]["execution_status"] == "executed"
    _say(client, sid, "Aşağı kaydır.")
    assert _tool(client, sid, "operator.pointer", {})["result"]["execution_status"] == "executed"


def _end_and_name(client, sid: str, name: str = "Yeni mail sekmesi") -> dict:
    answer = _say(client, sid, "Hareketi bitir.")
    assert _tool_named(answer, "macro_record_end") == "macro.record_end"
    ended = _tool(client, sid, "macro.record_end", {})["result"]
    assert ended["status"] == "awaiting_name", ended
    answer = _say(client, sid, f"{name}.")
    assert _tool_named(answer, "macro_name") == "macro.name"
    return _tool(client, sid, "macro.name", {})


def test_record_name_and_replay_by_name_alone() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _record_two_steps(client, sid)
    named = _end_and_name(client, sid)
    assert named["status"] == "succeeded", named
    body = named["result"]
    assert body["status"] == "saved" and body["replaced"] is False
    assert body["name"] == "Yeni mail sekmesi" and body["name_key"] == "yeni mail sekmesi"
    assert body["tools"] == ["operator.key", "operator.pointer"]
    assert body["speech"] == tools_macros.SPEECH_SAVED_TR.format(name="Yeni mail sekmesi", count=2)

    device.calls.clear()
    answer = _say(client, sid, "Yeni mail sekmesi aç.")
    assert _tool_named(answer, "macro_run") == "macro.run"
    ran = _tool(client, sid, "macro.run", {})
    assert ran["status"] == "succeeded", ran
    body = ran["result"]
    assert body["status"] == "succeeded" and body["steps_completed"] == 2
    assert body["stopped_at"] is None
    assert [s["tool"] for s in body["steps"]] == ["operator.key", "operator.pointer"]
    assert body["speech"] == tools_macros.SPEECH_RAN_TR.format(name="Yeni mail sekmesi", count=2)
    # The SAME device calls, in order: the key the owner pressed while teaching, then
    # the scroll - not a description of them.
    called = device.capabilities_called()
    assert called.count("keyboard.key") == 1 and called.count("pointer.scroll") == 1
    assert called.index("keyboard.key") < called.index("pointer.scroll")
    assert device.payload_for("keyboard.key") == {"window_id": window_id_for(1), "key": "enter"}

    with factory() as db:
        row = db.execute(
            select(RealtimeSessionRow).where(RealtimeSessionRow.id == __import__("uuid").UUID(sid))
        ).scalar_one()
        # The owner's OWN last sentence is what a follow-up sees, not a replayed step's.
        assert row.context_json["last_utterance"]["intent"] == "macro_run"
        assert macros_service.recording_state(row.context_json) is None
        # One relay call for the run; the replayed steps are inside it.
        names = [
            c.name
            for c in db.execute(select(RealtimeToolCall).order_by(RealtimeToolCall.id)).scalars()
        ]
        assert names.count("macro.run") == 1
        macro = macros_service.find_macro(db, "yeni mail sekmesi")
        assert macro is not None and macro.run_count == 1 and macro.last_run_at is not None


def test_the_replay_stops_at_the_first_step_that_did_not_succeed() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _record_two_steps(client, sid)
    assert _end_and_name(client, sid)["result"]["status"] == "saved"
    device.calls.clear()
    device.results["keyboard.key"] = DeviceRunResult(
        False, "focus_mismatch", "expected w-1 (notepad.exe), actual w-9 (chrome.exe)"
    )
    _say(client, sid, "Yeni mail sekmesi aç.")
    body = _tool(client, sid, "macro.run", {})["result"]
    assert body["status"] == "stopped" and body["stopped_at"] == 1
    assert body["steps_completed"] == 0
    assert "1. adımında durdum" in body["speech"], body["speech"]
    # The scroll after the failed key never happened.
    assert "pointer.scroll" not in device.capabilities_called()


def test_every_replayed_step_meets_the_step_up_gate_on_its_own_name(monkeypatch) -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _record_two_steps(client, sid)
    assert _end_and_name(client, sid)["result"]["status"] == "saved"
    seen: list[str] = []
    real_evaluate = step_up.evaluate

    def _spy(db, *, tool, **kwargs):
        seen.append(tool)
        if tool == "operator.pointer":
            return step_up.StepUpDecision(
                allowed=False,
                would_refuse=True,
                tier=step_up.TIER_SENSITIVE,
                reason=step_up.REASON_UNTRUSTED_DEVICE,
                tool=tool,
                mode=step_up.MODE_ENFORCE,
            )
        return real_evaluate(db, tool=tool, **kwargs)

    monkeypatch.setattr(step_up, "evaluate", _spy)
    device.calls.clear()
    _say(client, sid, "Yeni mail sekmesi aç.")
    body = _tool(client, sid, "macro.run", {})["result"]
    # The relay evaluated macro.run itself, then the runner evaluated each step by name.
    assert seen == ["macro.run", "operator.key", "operator.pointer"]
    assert body["status"] == "stopped" and body["stopped_at"] == 2
    assert body["steps"][1]["reason"] == "step_up_required"
    assert "keyboard.key" in device.capabilities_called()
    assert "pointer.scroll" not in device.capabilities_called()


def test_a_clarification_and_a_macro_word_are_not_steps() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Yeni hareket oluştur.")
    _tool(client, sid, "macro.record_start", {})
    # Starting again while recording changes nothing and says so.
    again = _tool(client, sid, "macro.record_start", {})["result"]
    assert again["already"] is True
    _say(client, sid, "Düğmeye bas.")  # names no key: a question, not a step
    assert _tool(client, sid, "operator.key", {})["status"] == "needs_clarification"
    _say(client, sid, "Hangi hareketlerim var?")
    _tool(client, sid, "macro.list", {})
    _say(client, sid, "Hareketi bitir.")
    ended = _tool(client, sid, "macro.record_end", {})["result"]
    assert ended["status"] == "idle" and ended["speech"] == tools_macros.SPEECH_NO_STEPS_TR
    with factory() as db:
        assert macros_service.list_macros(db) == []


def test_ending_or_naming_with_nothing_recorded_says_so() -> None:
    client, factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Hareketi bitir.")
    ended = _tool(client, sid, "macro.record_end", {})
    assert ended["status"] == "succeeded"
    assert ended["result"]["speech"] == tools_macros.SPEECH_NOT_RECORDING_TR
    named = _tool(client, sid, "macro.name", {"name": "x"})["result"]
    assert named["speech"] == tools_macros.SPEECH_NOTHING_TO_NAME_TR
    cancelled = _tool(client, sid, "macro.cancel", {})["result"]
    assert cancelled["speech"] == tools_macros.SPEECH_NOT_RECORDING_TR
    _say(client, sid, "Yeni mail sekmesi hareketini çalıştır.")
    ran = _tool(client, sid, "macro.run", {})["result"]
    assert ran["status"] == "unknown"
    assert ran["speech"] == tools_macros.SPEECH_UNKNOWN_TR.format(name="yeni mail sekmesi")


def test_an_empty_name_is_asked_again_and_a_cancel_drops_the_pending_name() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _record_two_steps(client, sid)
    _say(client, sid, "Hareketi bitir.")
    assert _tool(client, sid, "macro.record_end", {})["result"]["status"] == "awaiting_name"
    # No name on the turn (the model called without one): asked again, nothing dropped.
    asked = _tool(client, sid, "macro.name", {})
    assert asked["status"] == "needs_clarification"
    assert asked["result"]["speech"] == tools_macros.SPEECH_NAME_NEEDED_TR
    answer = _say(client, sid, "Vazgeç.")
    assert _tool_named(answer, "macro_record_cancel") == "macro.cancel"
    cancelled = _tool(client, sid, "macro.cancel", {})["result"]
    assert cancelled["status"] == "cancelled" and cancelled["step_count"] == 2
    with factory() as db:
        assert macros_service.list_macros(db) == []


def test_the_same_name_again_replaces_and_delete_makes_it_a_plain_sentence() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _record_two_steps(client, sid)
    assert _end_and_name(client, sid)["result"]["replaced"] is False
    # A second recording, one step, the same name: replaced, not doubled.
    _say(client, sid, "Yeni hareket oluştur.")
    _tool(client, sid, "macro.record_start", {})
    _say(client, sid, "Enter'a bas.")
    _tool(client, sid, "operator.key", {})
    replaced = _end_and_name(client, sid, name="yeni MAİL sekmesi")["result"]
    assert replaced["replaced"] is True and replaced["step_count"] == 1
    assert replaced["speech"] == tools_macros.SPEECH_REPLACED_TR.format(
        name="yeni MAİL sekmesi", count=1
    )
    _say(client, sid, "Hangi hareketlerim var?")
    listed = _tool(client, sid, "macro.list", {})["result"]
    assert listed["count"] == 1 and "yeni MAİL sekmesi" in listed["speech"]

    answer = _say(client, sid, "Yeni mail sekmesi hareketini sil.")
    assert _tool_named(answer, "macro_delete") == "macro.delete"
    deleted = _tool(client, sid, "macro.delete", {})["result"]
    assert deleted["status"] == "deleted"
    assert _tool(client, sid, "macro.list", {})["result"]["speech"] == tools_macros.SPEECH_NONE_TR
    # Without the stored name the sentence is no longer a run.
    answer = _say(client, sid, "Yeni mail sekmesi aç.")
    assert answer["resolved_intents"][-1]["intent"] != "macro_run"


def test_running_while_recording_is_refused() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _record_two_steps(client, sid)
    assert _end_and_name(client, sid)["result"]["status"] == "saved"
    _say(client, sid, "Yeni hareket oluştur.")
    _tool(client, sid, "macro.record_start", {})
    device.calls.clear()
    _say(client, sid, "Yeni mail sekmesi aç.")
    body = _tool(client, sid, "macro.run", {})["result"]
    assert body["status"] == "recording"
    assert body["speech"] == tools_macros.SPEECH_RUN_WHILE_RECORDING_TR
    assert device.capabilities_called() == []
