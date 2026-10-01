"""A launch the owner sent to a NAMED machine never lands on the session's machine by default.

Owner's trial 2026-09-30 20:11 UTC (MAIL): "Ofis bilgisayarımdan hesap makinesini aç" came
back from the STT as "Ofisü bilgisayarında hesap makinesini açın."; the alias parser could not
read "ofisü", the model called ``operator.app_open({application})`` - no device argument - and
Calculator opened on MAIL, the session's own machine, with not a word about it.

The turn record's ``machine_named_unbound`` is a word-free yes/no the relay writes beside
``device_targets`` (ADR-0224: the model may not guess a device, so the tool reads the record, not
an argument; the sentence itself is never stored). The first half of this file builds the record
by hand; the second half goes through the real session so the field provably travels.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.broker.models import Device
from app.ledger.models import ActivityEventRow
from app.voice.realtime_sessions.tools import ToolContext
from app.voice.realtime_sessions.tools_operator import operator_app_open

OFFICE_ID = uuid.uuid4()
HOME_ID = uuid.uuid4()


class _DeviceAction:
    """The port under the tool: only ``desktop.open_application`` is advertised (the office
    PC, ADR-0203), and every command it is given is recorded."""

    def __init__(self, targets: tuple[str, ...] = ()) -> None:
        self.targets = targets
        self.calls: list[dict[str, Any]] = []

    def can_run(self, capability: str) -> bool:
        return capability == "desktop.open_application"

    def run(self, *, capability: str, payload: dict[str, Any], **_: Any) -> Any:
        self.calls.append({"capability": capability, "payload": dict(payload)})
        return SimpleNamespace(
            ok=True, device_id=OFFICE_ID, result={"pid": 77, "executable": "calc.exe"}
        )


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Device.__table__.create(engine)
    ActivityEventRow.__table__.create(engine)
    with sessionmaker(bind=engine)() as session:
        for device_id, name, aliases in (
            (OFFICE_ID, "GMKADIRAKBABA", ["ofis", "iş"]),
            (HOME_ID, "MAIL", ["ev"]),
        ):
            session.add(
                Device(
                    id=device_id,
                    name=name,
                    platform="windows",
                    public_key_spki_b64="x",
                    capabilities_json=[],
                    metadata_json={"aliases": aliases},
                )
            )
        session.commit()
        yield session


def _run(db: Any, turn: dict[str, Any], action: _DeviceAction) -> dict[str, Any]:
    now = datetime.now(UTC)
    ctx = ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=HOME_ID,
        client_kind="web",
        context={"last_utterance": {"at": now.isoformat(), **turn}},
        db=db,
        now=now,
        call_id="call-1",
        live={"device_action": action},
    )
    return operator_app_open(ctx, {"application": "Hesap Makinesi"})


def test_unbound_computer_word_asks_and_dispatches_nothing(db) -> None:
    action = _DeviceAction()
    out = _run(
        db,
        {
            "application": "calc",
            "machine_named_unbound": True,
            "device_targets": [],
        },
        action,
    )
    assert action.calls == []
    assert out["status"] == "needs_clarification"
    speech = out["speech"]
    assert speech.count("?") == 1
    assert "ofis" in speech and "ev" in speech


def test_bound_ofis_target_launches_and_speech_names_the_device(db) -> None:
    action = _DeviceAction(targets=("ofis",))
    out = _run(
        db,
        {
            "application": "calc",
            "machine_named_unbound": False,
            "device_targets": ["ofis"],
        },
        action,
    )
    assert [c["capability"] for c in action.calls] == ["desktop.open_application"]
    assert "Ofis" in out["speech"] and "Hesap Makinesi" in out["speech"]


def test_sentence_without_a_computer_word_keeps_todays_behaviour(db) -> None:
    action = _DeviceAction()
    out = _run(
        db,
        {"application": "calc", "machine_named_unbound": False, "device_targets": []},
        action,
    )
    assert len(action.calls) == 1
    assert out.get("status") != "needs_clarification"


def test_this_computer_is_not_an_unbound_machine(db) -> None:
    action = _DeviceAction()
    _run(
        db,
        {
            "application": "calc",
            "machine_named_unbound": False,
            "device_targets": [],
        },
        action,
    )
    assert len(action.calls) == 1


# ------------------------------------------------ through the real session (ADR-0224)
# The tests above build the turn record by hand, which is exactly how this guard once passed
# while nothing wrote the field it reads. These go: utterance -> router -> relay -> tool.


def _relay(monkeypatch, tmp_path, sentence: str):
    from tests.unit.test_operator_open_application_fallback import (
        _both_online,
        _bound_session,
        _say,
        _tool,
    )

    world = _both_online(monkeypatch, tmp_path)
    sid = _bound_session(world, "MAIL")
    _say(world.client, sid, sentence)
    call = _tool(world.client, sid, "operator.app_open", {"application": "Hesap Makinesi"})
    return world, sid, call


def _record(world: Any, sid: str) -> dict[str, Any]:
    from app.voice.realtime_sessions.models import RealtimeSessionRow

    with world.factory() as session:
        row = session.get(RealtimeSessionRow, uuid.UUID(sid))
        return dict((row.context_json or {}).get("last_utterance") or {})


def test_relay_unbound_machine_word_asks_and_dispatches_nothing(monkeypatch, tmp_path) -> None:
    """The owner's real sentence, as the STT rendered it. Red before: launched on MAIL."""
    world, sid, call = _relay(monkeypatch, tmp_path, "Ofisü bilgisayarında hesap makinesini açın.")
    assert world.commands.calls == []
    assert call["result"]["status"] == "needs_clarification", call
    assert call["result"]["speech"].count("?") == 1
    record = _record(world, sid)
    assert record["machine_named_unbound"] is True
    # word-free: the owner's sentence is never kept on the session (privacy)
    assert "utterance_text" not in record
    assert "hesap" not in str(record.get("machine_named_unbound")).lower()


@pytest.mark.parametrize(
    "sentence",
    [
        "Mutfaktaki bilgisayarda hesap makinesini aç.",  # the 'bilgisayar' branch alone
        "Ofisü hesap makinesini açın.",  # an 'ofis...' branch alone, no 'bilgisayar'
    ],
)
def test_relay_each_machine_word_branch_is_pinned(monkeypatch, tmp_path, sentence) -> None:
    world, sid, call = _relay(monkeypatch, tmp_path, sentence)
    assert _record(world, sid)["machine_named_unbound"] is True
    assert world.commands.calls == []
    assert call["result"]["status"] == "needs_clarification", call


def test_relay_bound_office_launches_there_and_the_flag_is_false(monkeypatch, tmp_path) -> None:
    world, sid, call = _relay(monkeypatch, tmp_path, "Ofis bilgisayarında hesap makinesini açın.")
    assert _record(world, sid)["machine_named_unbound"] is False
    assert {c["device_id"] for c in world.commands.calls} == {world.ids["GMKADIRAKBABA"]}
    assert call["result"]["execution_status"] == "executed", call


def test_relay_no_machine_word_launches_on_the_session_device(monkeypatch, tmp_path) -> None:
    world, sid, call = _relay(monkeypatch, tmp_path, "Hesap makinesini açın.")
    assert _record(world, sid)["machine_named_unbound"] is False
    assert {c["device_id"] for c in world.commands.calls} == {world.ids["MAIL"]}


def test_names_unbound_machine_pure_function() -> None:
    from app.voice.realtime_sessions.tools_operator import names_unbound_machine

    assert names_unbound_machine("Ofisü bilgisayarında hesap makinesini açın.", ()) is True
    assert names_unbound_machine("Mutfaktaki bilgisayarda hesap makinesini aç.", ()) is True
    assert names_unbound_machine("Ofisü hesap makinesini açın.", ()) is True
    assert names_unbound_machine("Ofis bilgisayarında hesap makinesini açın.", ("ofis",)) is False
    assert names_unbound_machine("Bu bilgisayarda hesap makinesini aç.", ()) is False
    assert names_unbound_machine("Hesap makinesini açın.", ()) is False
    assert names_unbound_machine("", ()) is False
