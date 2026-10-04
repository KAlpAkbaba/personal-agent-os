"""The misheard notebook wired into the ONE relay (ADR-0254 store, card misheard-relay-wiring).

Four conditions write a row, and only four: a sentence that asked for something and routed
nowhere ('no_intent'), a sentence the policy answered with its one question
('asked_question'), an acting sentence the owner objected to within the misroute window
('objected' - the row carries the ACTED sentence, never "hayır"), and a sentence whose tool
handler failed ('tool_failed'). Everything goes through the REAL ``POST .../events`` and
``POST .../tool-calls`` with the real tool registry over real enrolled rows (the world of
``test_understanding_relay``); nothing builds a turn record by hand. Every case runs in a paid
(simulator) session and in a local-router session.

What does NOT change is held here too: the audit rows, the ledger and the session's
``context_json`` gain no sentence (the local mode's existing ``chat_question`` is asserted as
it was), and a notebook that fails changes nothing the owner hears.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.broker.models import AuditEvent
from app.ledger.models import ActivityEventRow
from app.security import step_up
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.misheard import service as misheard
from app.voice.misheard.models import MisheardUtterance
from app.voice.providers_local_router import (
    LOCAL_ROUTER_PROVIDER_NAME,
    LocalRouterRealtimeProvider,
)
from app.voice.realtime_sessions import service as relay
from app.voice.realtime_sessions.models import RealtimeSessionRow
from tests.unit.test_operator_open_application_fallback import (
    _both_online,
)

UNBOUND_REQUEST = "Mutfaktaki bilgisayarda bir şey yap."
PLAIN_TALK = "Bugün nasılsın"
TRIAL_APP = "Ofisü bilgisayarında hesap makinesini açın."
UNBOUND_APP = "Mutfaktaki bilgisayarda hesap makinesini aç."
UNDERSTOOD = "Hesap makinesini aç."
ACTING = "Hesap makinesini kapat."
OBJECTION = "Hayır."
FAILING = "Saat kaç?"


class _Clock:
    """The relay's one clock (``service.utcnow``), moved by the test."""

    def __init__(self) -> None:
        self.at = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def _empty_hold():
    misheard.reset_hold()
    yield
    misheard.reset_hold()


@pytest.fixture()
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(relay, "utcnow", c)
    return c


@pytest.fixture(params=["paid", "local"])
def mode(request) -> str:
    return request.param


def _prepare(world: Any) -> Any:
    MisheardUtterance.__table__.create(world.factory.kw["bind"])
    local = LocalRouterRealtimeProvider()
    world.client.app.state.voice_realtime.providers[local.name] = local
    return world


def _sub(tmp_path: Path, name: str) -> Path:
    """A second world's own folder (its own database file)."""
    folder = tmp_path / name
    folder.mkdir()
    return folder


def _world(monkeypatch, tmp_path) -> Any:
    return _prepare(_both_online(monkeypatch, tmp_path))


def _session(world: Any, mode: str) -> str:
    body: dict[str, Any] = {"device_id": str(world.ids["MAIL"])}
    if mode == "local":
        body["transport"] = "text"
    response = world.client.post("/v1/voice/realtime/sessions", json=body)
    assert response.status_code == 201, response.text
    sid = response.json()["session_id"]
    with world.factory() as db:
        provider = db.get(RealtimeSessionRow, uuid.UUID(sid)).provider
    assert (provider == LOCAL_ROUTER_PROVIDER_NAME) is (mode == "local")
    return sid


def _say(world: Any, sid: str, text: str, *, engine: str | None = None) -> dict:
    event: dict[str, Any] = {"kind": "utterance", "t_ms": 1000, "turn": 1, "text": text}
    if engine is not None:
        event["payload"] = {"stt_engine": engine}
    response = world.client.post(
        f"/v1/voice/realtime/sessions/{sid}/events", json={"events": [event]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _tool(world: Any, sid: str, name: str, arguments: dict, *, call_id: str | None = None):
    response = world.client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": call_id or f"c-{uuid.uuid4()}", "name": name, "arguments": arguments},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _rows(world: Any) -> list[MisheardUtterance]:
    """Read back through a FRESH session, newest last."""
    with world.factory() as db:
        return list(
            db.execute(select(MisheardUtterance).order_by(MisheardUtterance.heard_at)).scalars()
        )


def _replace_handler(monkeypatch, world: Any, name: str, handler: Any) -> None:
    """The REAL registry, one handler swapped: the relay's own path decides the status."""
    registry = world.client.app.state.voice_realtime.registry
    spec = registry.get(name)
    monkeypatch.setitem(registry._tools, name, dataclasses.replace(spec, handler=handler))


def _context(world: Any, sid: str) -> dict[str, Any]:
    with world.factory() as db:
        return dict(db.get(RealtimeSessionRow, uuid.UUID(sid)).context_json or {})


def _assert_no_sentence_leaked(world: Any, sid: str, mode: str, sentences: list[str]) -> None:
    """(8) Neither the session's context_json, nor an audit row, nor a ledger row carries a
    sentence; the local mode's chat_question is exactly what it always was."""
    ctx = _context(world, sid)
    last = dict(ctx.get("last_utterance") or {})
    chat = last.pop("chat_question", None)
    if mode == "paid":
        assert chat is None
    ctx["last_utterance"] = last
    with world.factory() as db:
        audits = [
            json.dumps(r.metadata_json, ensure_ascii=False)
            for r in db.execute(select(AuditEvent)).scalars()
        ]
        ledger = [
            json.dumps(
                {
                    "summary": r.factual_summary,
                    "detail": r.detail_json,
                    "action": r.action,
                    "source_ref": r.source_ref,
                },
                ensure_ascii=False,
                default=str,
            )
            for r in db.execute(select(ActivityEventRow)).scalars()
        ]
    dumped_ctx = json.dumps(ctx, ensure_ascii=False, default=str)
    for sentence in sentences:
        core = sentence.rstrip(".?").casefold()
        assert core not in dumped_ctx.casefold(), sentence
        for blob in audits + ledger:
            assert core not in blob.casefold(), (sentence, blob[:200])


def _chat_question(world: Any, sid: str) -> Any:
    return (_context(world, sid).get("last_utterance") or {}).get("chat_question")


# --- (1) no_intent ------------------------------------------------------------------------


def test_a_request_that_names_an_unbound_machine_and_routes_nowhere_is_one_row(
    mode, clock, monkeypatch, tmp_path
) -> None:
    world = _world(monkeypatch, tmp_path)
    sid = _session(world, mode)
    engine = "chrome-cihaz-ici" if mode == "local" else None
    said = _say(world, sid, UNBOUND_REQUEST, engine=engine)
    assert said["resolved_intents"][0]["intent"] == "none"
    rows = _rows(world)
    assert len(rows) == 1
    row = rows[0]
    assert row.reason == "no_intent"
    assert row.sentence == UNBOUND_REQUEST
    assert row.mode == mode
    assert row.band == "low" and row.confidence == 0.0
    assert row.resolved_intent is None and row.tool is None
    assert row.engine == engine  # the client's own name, never guessed
    assert row.device_id == world.ids["MAIL"]
    assert row.session_id == uuid.UUID(sid)
    assert row.meant is None and row.answered_at is None
    expected_chat = UNBOUND_REQUEST if mode == "local" else None
    assert _chat_question(world, sid) == expected_chat  # byte-for-byte as before
    _assert_no_sentence_leaked(world, sid, mode, [UNBOUND_REQUEST])


# --- (2) plain conversation ----------------------------------------------------------------


def test_plain_conversation_is_never_a_row(mode, clock, monkeypatch, tmp_path) -> None:
    world = _world(monkeypatch, tmp_path)
    sid = _session(world, mode)
    assert _say(world, sid, PLAIN_TALK)["resolved_intents"][0]["intent"] == "none"
    assert _rows(world) == []
    _assert_no_sentence_leaked(world, sid, mode, [PLAIN_TALK])


# --- (3) asked_question ----------------------------------------------------------------------


def test_the_policys_one_question_is_one_asked_question_row(
    mode, clock, monkeypatch, tmp_path
) -> None:
    world = _world(monkeypatch, tmp_path)
    sid = _session(world, mode)
    resolved = _say(world, sid, UNBOUND_APP)["resolved_intents"][0]
    assert resolved["band"] == "low"
    assert _context(world, sid)["last_utterance"]["understanding"]["question"] == (
        "Hangi bilgisayarda: ev mi, ofis mi, iş mi?"
    )
    rows = _rows(world)
    assert [r.reason for r in rows] == ["asked_question"]  # not also a no_intent one
    assert rows[0].sentence == UNBOUND_APP
    assert rows[0].resolved_intent == "app_open"
    assert rows[0].mode == mode and rows[0].band == "low"
    # The tool call the question stops is the layer-3 refusal: no 'tool_failed' row.
    call = _tool(world, sid, "operator.app_open", {"application": "Hesap Makinesi"})
    assert call["result"]["refused"] == "understanding_low"
    assert [r.reason for r in _rows(world)] == ["asked_question"]
    _assert_no_sentence_leaked(world, sid, mode, [UNBOUND_APP])


def test_the_trial_rendering_is_read_back_today_and_is_no_row(
    mode, clock, monkeypatch, tmp_path
) -> None:
    """The owner's rendering 'Ofisü bilgisayarında ...' no longer asks (ADR-0233: layer 1
    binds 'Ofisü' to 'ofis', MEDIUM, read back); acted and done, it is not a row."""
    world = _world(monkeypatch, tmp_path)
    sid = _session(world, mode)
    resolved = _say(world, sid, TRIAL_APP)["resolved_intents"][0]
    assert resolved["band"] == "medium"
    assert _context(world, sid)["last_utterance"]["understanding"]["question"] is None
    call = _tool(world, sid, "operator.app_open", {"application": "Hesap Makinesi"})
    assert call["status"] == "succeeded", call
    assert _rows(world) == []
    _assert_no_sentence_leaked(world, sid, mode, [TRIAL_APP])


# --- (4) understood and done ---------------------------------------------------------------


def test_an_understood_sentence_whose_tool_succeeds_is_no_row(
    mode, clock, monkeypatch, tmp_path
) -> None:
    world = _world(monkeypatch, tmp_path)
    sid = _session(world, mode)
    _say(world, sid, UNDERSTOOD)
    call = _tool(world, sid, "operator.app_open", {"application": "Hesap Makinesi"})
    assert call["status"] == "succeeded", call
    assert _rows(world) == []
    _assert_no_sentence_leaked(world, sid, mode, [UNDERSTOOD])


# --- (5) objected ------------------------------------------------------------------------


def test_an_objection_inside_the_window_records_the_acted_sentence(
    mode, clock, monkeypatch, tmp_path
) -> None:
    world = _world(monkeypatch, tmp_path)
    sid = _session(world, mode)
    acted = _say(world, sid, ACTING)["resolved_intents"][0]
    assert acted["intent"] == "app_close"
    acted_at = clock.at
    clock.advance(11.5)
    _say(world, sid, OBJECTION)
    rows = _rows(world)
    assert len(rows) == 1
    row = rows[0]
    assert row.reason == "objected"
    assert row.sentence == ACTING  # never "hayır"
    assert row.resolved_intent == "app_close"
    assert row.band == "high" and row.confidence == 1.0
    assert misheard._utc(row.heard_at) == acted_at
    assert row.mode == mode
    _assert_no_sentence_leaked(world, sid, mode, [ACTING])


def test_the_same_objection_thirteen_seconds_later_is_no_row(
    mode, clock, monkeypatch, tmp_path
) -> None:
    world = _world(monkeypatch, tmp_path)
    sid = _session(world, mode)
    _say(world, sid, ACTING)
    clock.advance(13)
    _say(world, sid, OBJECTION)
    assert _rows(world) == []


def test_an_objection_in_another_session_records_nothing_here(
    mode, clock, monkeypatch, tmp_path
) -> None:
    """The misroute ring is process-wide; the held sentence is this session's own."""
    world = _world(monkeypatch, tmp_path)
    first = _session(world, mode)
    second = _session(world, mode)
    _say(world, first, ACTING)
    clock.advance(1)
    _say(world, second, UNDERSTOOD)
    clock.advance(1)
    _say(world, second, OBJECTION)
    assert [(r.reason, r.sentence) for r in _rows(world)] == []


# --- (6) tool_failed ---------------------------------------------------------------------


def _raises_voice_error(ctx, arguments):
    raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "planned failure")


def _crashes(ctx, arguments):
    raise RuntimeError("planned crash")


def _earns_failed(ctx, arguments):
    # A research-bound tool's "ok" with no target and no words EARNS failed (ADR-0077).
    return {"status": "ok"}


@pytest.mark.parametrize(
    ("tool", "handler"),
    [
        ("clock.now", _raises_voice_error),
        ("clock.now", _crashes),
        ("research.sources", _earns_failed),
    ],
    ids=["voice_error", "crash", "earns_failed"],
)
def test_a_failed_handler_is_one_tool_failed_row_with_the_tools_name(
    mode, clock, monkeypatch, tmp_path, tool, handler
) -> None:
    world = _world(monkeypatch, tmp_path)
    _replace_handler(monkeypatch, world, tool, handler)
    sid = _session(world, mode)
    _say(world, sid, FAILING)
    call = _tool(world, sid, tool, {})
    assert call["status"] == "failed", call
    rows = _rows(world)
    assert [(r.reason, r.tool, r.sentence, r.mode) for r in rows] == [
        ("tool_failed", tool, FAILING, mode)
    ]
    _assert_no_sentence_leaked(world, sid, mode, [FAILING])


def test_two_failed_tools_in_one_turn_are_one_row(mode, clock, monkeypatch, tmp_path) -> None:
    world = _world(monkeypatch, tmp_path)
    _replace_handler(monkeypatch, world, "clock.now", _crashes)
    sid = _session(world, mode)
    _say(world, sid, FAILING)
    assert _tool(world, sid, "clock.now", {})["status"] == "failed"
    clock.advance(1)
    assert _tool(world, sid, "clock.now", {})["status"] == "failed"
    assert [r.reason for r in _rows(world)] == ["tool_failed"]


def test_a_failed_tool_with_no_sentence_held_is_no_row(mode, clock, monkeypatch, tmp_path) -> None:
    world = _world(monkeypatch, tmp_path)
    _replace_handler(monkeypatch, world, "clock.now", _crashes)
    sid = _session(world, mode)
    assert _tool(world, sid, "clock.now", {})["status"] == "failed"
    assert _rows(world) == []


def test_a_step_up_refusal_is_not_a_tool_failed_row(mode, clock, monkeypatch, tmp_path) -> None:
    world = _world(monkeypatch, tmp_path)

    def _refuse(db, *, tool, **kwargs):
        return step_up.StepUpDecision(
            allowed=False,
            would_refuse=True,
            tier=step_up.TIER_SENSITIVE,
            reason=step_up.REASON_UNTRUSTED_DEVICE,
            tool=tool,
            mode=step_up.MODE_ENFORCE,
        )

    monkeypatch.setattr(step_up, "evaluate", _refuse)
    sid = _session(world, mode)
    _say(world, sid, FAILING)
    call = _tool(world, sid, "clock.now", {})
    assert call["status"] == "failed" and call["error"]["refused"] == "step_up_required", call
    assert _rows(world) == []


def test_an_unknown_tool_is_not_a_tool_failed_row(mode, clock, monkeypatch, tmp_path) -> None:
    world = _world(monkeypatch, tmp_path)
    sid = _session(world, mode)
    _say(world, sid, FAILING)
    call = _tool(world, sid, "no.such_tool", {})
    assert call["status"] == "failed", call
    assert _rows(world) == []


def test_a_replayed_call_is_not_a_tool_failed_row(mode, clock, monkeypatch, tmp_path) -> None:
    world = _world(monkeypatch, tmp_path)
    _replace_handler(monkeypatch, world, "clock.now", _crashes)
    sid = _session(world, mode)
    # The original fails while nothing is held ...
    assert _tool(world, sid, "clock.now", {}, call_id="c-replay")["status"] == "failed"
    _say(world, sid, FAILING)
    # ... and its replay, with a sentence now held, executes nothing and records nothing.
    replay = _tool(world, sid, "clock.now", {}, call_id="c-replay")
    assert replay["status"] == "failed" and replay.get("replayed") is True, replay
    assert _rows(world) == []


def test_a_device_slot_refusal_is_not_a_tool_failed_row(mode, clock, monkeypatch, tmp_path) -> None:
    world = _world(monkeypatch, tmp_path)
    sid = _session(world, mode)
    _say(world, sid, UNDERSTOOD)
    call = _tool(world, sid, "operator.app_open", {"application": "Hesap Makinesi", "device": "x"})
    assert call["error"]["refused"] == "device_slot_forbidden", call
    assert _rows(world) == []


# --- (7) 'sadece dinle' ---------------------------------------------------------------------


def test_listen_only_records_nothing_and_holds_nothing(mode, clock, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(relay, "_listen_only", lambda row: True)
    world = _world(monkeypatch, tmp_path)
    _replace_handler(monkeypatch, world, "clock.now", _crashes)
    sid = _session(world, mode)
    for text in (UNBOUND_REQUEST, UNBOUND_APP, ACTING, OBJECTION, FAILING):
        # no_intent, asked_question, (acting), objected, (the sentence of the failed tool)
        _say(world, sid, text)
        assert misheard.held(sid, clock.at) is None
        clock.advance(2)
    assert _tool(world, sid, "clock.now", {})["status"] == "failed"  # tool_failed
    assert misheard.held(sid, clock.at) is None
    assert _rows(world) == []


def test_the_listen_only_helper_answers_false_today() -> None:
    assert relay._listen_only(object()) is False
    assert "ADR-0171" in (relay._listen_only.__doc__ or "")


# --- (9) the notebook never changes or fails a turn -----------------------------------------


def _turns(world: Any, mode: str) -> list[Any]:
    """The four conditions' turns, as the owner's client sees them (ids and times removed)."""
    out: list[Any] = []
    sid = _session(world, mode)
    for text in (UNBOUND_REQUEST, PLAIN_TALK, ACTING, OBJECTION, FAILING):
        out.append(_say(world, sid, text)["resolved_intents"])
    call = _tool(world, sid, "clock.now", {})
    out.append((call["status"], call.get("error"), call.get("result")))
    out.append(_context(world, sid).get("last_utterance", {}).get("chat_question"))
    return out


def test_a_notebook_that_raises_changes_nothing_the_turn_returns(
    mode, clock, monkeypatch, tmp_path
) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("notebook down")

    without = _world(monkeypatch, _sub(tmp_path, "without"))
    _replace_handler(monkeypatch, without, "clock.now", _crashes)
    monkeypatch.setattr(misheard, "record", lambda *a, **k: None)
    expected = _turns(without, mode)

    broken = _world(monkeypatch, _sub(tmp_path, "broken"))
    _replace_handler(monkeypatch, broken, "clock.now", _crashes)
    monkeypatch.setattr(misheard, "record", _boom)
    assert _turns(broken, mode) == expected


def test_a_caller_type_error_through_the_relay_does_not_break_the_turn(
    mode, clock, monkeypatch, tmp_path
) -> None:
    """Inspection finding 2 (a): the store refuses a wrong type quietly; through the relay's
    own call the turn is untouched and nothing is written."""
    real = misheard.record
    wrong: list[dict[str, Any]] = [
        {"reason": ["no_intent"]},
        {"mode": {"paid": 1}},
        {"heard_at": "2026-10-03T10:00:00Z"},
        {"now": 12345},
    ]
    seen: list[Any] = []

    def _wrong_types(db, **kwargs):
        kwargs.update(wrong[len(seen) % len(wrong)])
        seen.append(real(db, **kwargs))
        return seen[-1]

    monkeypatch.setattr(misheard, "record", _wrong_types)
    world = _world(monkeypatch, tmp_path)
    for _ in wrong:
        sid = _session(world, mode)
        said = _say(world, sid, UNBOUND_REQUEST)
        assert said["resolved_intents"][0]["intent"] == "none"
    assert seen == [None, None, None, None]
    assert _rows(world) == []


def test_the_relay_flushes_its_own_work_before_it_calls_the_notebook(
    mode, clock, monkeypatch, tmp_path
) -> None:
    """Inspection finding 5 (c): a caller with an unflushed row of its own would get None and
    then PendingRollbackError. Every call into the store finds nothing pending."""
    real = misheard.record
    pending: list[bool] = []

    def _spy(db, **kwargs):
        pending.append(bool(db.new or db.dirty or db.deleted))
        return real(db, **kwargs)

    monkeypatch.setattr(misheard, "record", _spy)
    world = _world(monkeypatch, tmp_path)
    _replace_handler(monkeypatch, world, "clock.now", _crashes)
    sid = _session(world, mode)
    _say(world, sid, UNBOUND_REQUEST)
    clock.advance(2)
    _say(world, sid, FAILING)
    _tool(world, sid, "clock.now", {})
    assert pending == [False, False]
    assert [r.reason for r in _rows(world)] == ["no_intent", "tool_failed"]
