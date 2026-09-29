"""ADR-0212: the device the owner NAMES in a sentence is the device that acts.

The owner has two machines: MAIL (home, alias "ev", Operator installed) and GMKADIRAKBABA
(office, aliases "ofis"/"iş", no Operator). "Ofis bilgisayarımda hesap makinesini aç" said
from a session on the home PC - or from one bound to nothing while the home PC was online -
opened the calculator on the HOME PC: ``app.devices.aliases`` parsed the phrase, ``select_device``
made an explicit alias win, and nothing in the voice path ever handed the phrase to either.
Only research (``target_device``) and REST ``/v1/devices/select`` passed a target.

Everything here goes through the REAL application object, exactly like
``test_operator_open_application_fallback``: the one router hears the sentence, the relay
binds the per-call port, the port is the real ``BrokerDeviceAction`` over real enrolled
device rows, and only the last hop (the command client) is a recorder. A fake port would
prove nothing: the defect is what the relay hands the port.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.broker import service as broker_service
from app.broker.models import Device
from app.devices import selection
from app.devices.aliases import CANONICAL_ALIASES, extract_alias, extract_aliases, targets_of_turn
from app.operator.mission_models import OperatorMissionRow
from app.voice.realtime_sessions.models import RealtimeSessionRow
from tests.unit.test_operator_open_application_fallback import (
    HOME_CAPABILITIES,
    OFFICE_CAPABILITIES,
    _both_online,
    _bound_session,
    _create,
    _say,
    _tool,
    _wired,
    _World,
)


def _with_missions(world: _World) -> _World:
    """The mission tool's table, on top of the ones the launch suite's world creates."""
    with world.factory() as db:
        OperatorMissionRow.__table__.create(db.get_bind(), checkfirst=True)
    return world


def _open(world: _World, sid: str, sentence: str, application: str = "calc") -> dict[str, Any]:
    """The owner speaks, then the model calls the tool the router chose."""
    _say(world.client, sid, sentence)
    call = _tool(world.client, sid, "operator.app_open", {"application": application})
    assert call["status"] == "succeeded", call
    return call["result"]


def _devices_hit(world: _World) -> set[uuid.UUID]:
    return {c["device_id"] for c in world.commands.calls}


def _last_utterance(world: _World, sid: str) -> dict[str, Any]:
    with world.factory() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        return dict((row.context_json or {}).get("last_utterance") or {})


# --------------------------------------------------------------- the owner's sentences


def test_a_home_session_naming_the_office_launches_at_the_office_directly(
    monkeypatch, tmp_path
) -> None:
    """The reported gap, verbatim. Red before ADR-0212: the calculator opened on MAIL."""
    world = _both_online(monkeypatch, tmp_path)
    sid = _bound_session(world, "MAIL")
    body = _open(world, sid, "Ofis bilgisayarımda hesap makinesini aç.")

    assert world.commands.capabilities() == ["desktop.open_application"]
    assert _devices_hit(world) == {world.ids["GMKADIRAKBABA"]}
    assert body["execution_status"] == "executed", body
    assert body["observed_after"]["server"]["path"] == "desktop.open_application"


def test_an_office_session_naming_the_home_pc_goes_to_the_home_operator(
    monkeypatch, tmp_path
) -> None:
    world = _both_online(monkeypatch, tmp_path)
    sid = _bound_session(world, "GMKADIRAKBABA")
    body = _open(world, sid, "Ev bilgisayarımda hesap makinesini aç.")

    assert "app.launch" in world.commands.capabilities()
    assert "desktop.open_application" not in world.commands.capabilities()
    assert _devices_hit(world) == {world.ids["MAIL"]}
    assert body["execution_status"] == "executed", body


def test_an_unbound_session_naming_the_office_acts_at_the_office(monkeypatch, tmp_path) -> None:
    """Both machines online and no session device: the healthiest-device rule used to pick
    the home PC whatever the sentence said."""
    world = _both_online(monkeypatch, tmp_path)
    _open(world, _create(world.client), "Ofis bilgisayarımda hesap makinesini aç.")

    assert world.commands.capabilities() == ["desktop.open_application"]
    assert _devices_hit(world) == {world.ids["GMKADIRAKBABA"]}


def test_the_office_can_be_named_by_either_of_its_aliases(monkeypatch, tmp_path) -> None:
    world = _both_online(monkeypatch, tmp_path)
    _open(world, _bound_session(world, "MAIL"), "İş bilgisayarımda hesap makinesini aç.")
    assert _devices_hit(world) == {world.ids["GMKADIRAKBABA"]}


def test_a_capital_dotted_i_at_the_start_of_the_sentence_still_names_the_device() -> None:
    """``"İş".casefold()`` is "i" + a combining dot, so the pattern the module documents as
    diacritic-tolerant never matched a sentence that STARTS with "İş bilgisayarımda" - which
    is how a transcript capitalises it."""
    assert extract_alias("İş bilgisayarımda hesap makinesini aç") == "iş"
    assert extract_alias("İşteki bilgisayarda aç") == "iş"
    assert extract_alias("İş") == "iş"
    assert extract_alias("İstediğim videoyu aç") is None


def test_the_sentence_after_the_named_one_is_not_still_named(monkeypatch, tmp_path) -> None:
    """The device is the one the CURRENT sentence named: the next sentence, naming none, is
    the old rule again."""
    world = _both_online(monkeypatch, tmp_path)
    sid = _bound_session(world, "MAIL")
    _open(world, sid, "Ofis bilgisayarımda hesap makinesini aç.")
    assert _devices_hit(world) == {world.ids["GMKADIRAKBABA"]}

    world.commands.calls.clear()
    response = world.client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={
            "events": [
                {"kind": "utterance", "t_ms": 2000, "turn": 2, "text": "Hesap makinesini aç."}
            ]
        },
    )
    assert response.status_code == 200, response.text
    call = _tool(world.client, sid, "operator.app_open", {"application": "calc"})
    assert call["status"] == "succeeded", call
    assert _devices_hit(world) == {world.ids["MAIL"]}


def test_a_sentence_naming_no_device_keeps_the_session_rule(monkeypatch, tmp_path) -> None:
    world = _both_online(monkeypatch, tmp_path)
    _open(world, _bound_session(world, "GMKADIRAKBABA"), "Hesap makinesini aç.")
    assert world.commands.capabilities() == ["desktop.open_application"]
    assert _devices_hit(world) == {world.ids["GMKADIRAKBABA"]}


# --------------------------------------------------- a named device that cannot serve


def test_a_named_office_that_is_offline_is_a_refusal_naming_it(monkeypatch, tmp_path) -> None:
    world = _wired(
        monkeypatch,
        tmp_path,
        {
            "MAIL": {"capabilities": HOME_CAPABILITIES, "aliases": ["ev"]},
            "GMKADIRAKBABA": {
                "capabilities": OFFICE_CAPABILITIES,
                "aliases": ["ofis", "iş"],
                "online": False,
            },
        },
    )
    body = _open(world, _bound_session(world, "MAIL"), "Ofis bilgisayarımda hesap makinesini aç.")

    assert world.commands.calls == [], "nothing may be dispatched - least of all to the home PC"
    assert body["execution_status"] == "failed"
    assert "ofis" in body["speech"].lower() and "çevrimiçi değil" in body["speech"]


def test_a_named_home_pc_that_is_offline_is_not_replaced_by_the_office(
    monkeypatch, tmp_path
) -> None:
    world = _wired(
        monkeypatch,
        tmp_path,
        {
            "MAIL": {"capabilities": HOME_CAPABILITIES, "aliases": ["ev"], "online": False},
            "GMKADIRAKBABA": {"capabilities": OFFICE_CAPABILITIES, "aliases": ["ofis", "iş"]},
        },
    )
    body = _open(
        world, _bound_session(world, "GMKADIRAKBABA"), "Ev bilgisayarımda hesap makinesini aç."
    )

    assert world.commands.calls == []
    assert body["execution_status"] == "failed"
    assert "ev" in body["speech"].lower() and "çevrimiçi değil" in body["speech"]


def test_a_named_device_without_the_capability_is_a_refusal_not_a_fallback(
    monkeypatch, tmp_path
) -> None:
    """The office here advertises neither launch. The home PC could do it; the owner did not
    ask the home PC."""
    world = _wired(
        monkeypatch,
        tmp_path,
        {
            "MAIL": {"capabilities": HOME_CAPABILITIES, "aliases": ["ev"]},
            "GMKADIRAKBABA": {"capabilities": ["desktop.notify"], "aliases": ["ofis"]},
        },
    )
    body = _open(world, _bound_session(world, "MAIL"), "Ofis bilgisayarımda hesap makinesini aç.")

    assert world.commands.calls == []
    assert body["execution_status"] == "failed"
    assert "ofis" in body["speech"].lower()


def test_a_named_device_nobody_enrolled_is_a_refusal_not_the_only_device(
    monkeypatch, tmp_path
) -> None:
    world = _wired(
        monkeypatch, tmp_path, {"MAIL": {"capabilities": HOME_CAPABILITIES, "aliases": ["ev"]}}
    )
    body = _open(world, _create(world.client), "Ofis bilgisayarımda hesap makinesini aç.")

    assert world.commands.calls == []
    assert body["execution_status"] == "failed"
    assert "ofis" in body["speech"].lower() and "bulunamadı" in body["speech"]


def test_a_revoked_office_is_not_a_device_the_sentence_can_name(monkeypatch, tmp_path) -> None:
    world = _both_online(monkeypatch, tmp_path)
    with world.factory() as db:
        db.get(Device, world.ids["GMKADIRAKBABA"]).status = "revoked"
        db.commit()
    body = _open(world, _bound_session(world, "MAIL"), "Ofis bilgisayarımda hesap makinesini aç.")

    assert world.commands.calls == []
    assert body["execution_status"] == "failed"


def test_a_policy_that_denies_the_named_device_is_a_refusal_naming_it(
    monkeypatch, tmp_path
) -> None:
    world = _both_online(monkeypatch, tmp_path)
    with world.factory() as db:
        broker_service.update_device_metadata(
            db,
            world.ids["GMKADIRAKBABA"],
            policy={"deny": ["desktop.open_application"]},
            trace_id=None,
        )
    ran = world.action.bound_to([world.ids["MAIL"]], targets=("ofis",)).run(
        capability="desktop.open_application",
        payload={"application": "calc"},
        idempotency_key="k",
        timeout_s=1.0,
    )
    assert (ran.ok, ran.error_class) == (False, "no_capable_device")
    assert ran.message == "Politika 'ofis' cihazında bu işleme izin vermiyor."
    assert world.commands.calls == []


def test_an_alias_two_devices_share_is_a_refusal_and_dispatches_nothing(
    monkeypatch, tmp_path
) -> None:
    world = _both_online(monkeypatch, tmp_path)
    with world.factory() as db:
        # PATCH refuses to create this state; selection must not trust that it never happened.
        broker_service.update_device_metadata(
            db, world.ids["MAIL"], aliases=["ev", "ofis"], trace_id=None
        )
    body = _open(world, _bound_session(world, "MAIL"), "Ofis bilgisayarımda hesap makinesini aç.")

    assert world.commands.calls == []
    assert body["execution_status"] == "failed"
    assert "birden fazla cihazda" in body["speech"]


# -------------------------------------------------- two devices named in one sentence


def test_a_sentence_naming_two_different_devices_is_a_refusal(monkeypatch, tmp_path) -> None:
    """ "evdeki ... ofis bilgisayarımda": the parser's first pattern is "ev", which would have
    been the wrong machine for the office one. Which of the two is meant is not guessed."""
    world = _both_online(monkeypatch, tmp_path)
    body = _open(
        world,
        _bound_session(world, "MAIL"),
        "Evdeki hesap makinesini ofis bilgisayarımda aç.",
    )

    assert world.commands.calls == []
    assert body["execution_status"] == "failed"
    assert "ev" in body["speech"] and "ofis" in body["speech"]


def test_two_words_for_the_same_device_are_one_device(monkeypatch, tmp_path) -> None:
    world = _both_online(monkeypatch, tmp_path)
    _open(
        world,
        _bound_session(world, "MAIL"),
        "Ofis bilgisayarımda, yani iş bilgisayarımda hesap makinesini aç.",
    )
    assert _devices_hit(world) == {world.ids["GMKADIRAKBABA"]}


# ------------------------------------------------ ordinary words are not device names


@pytest.mark.parametrize(
    "sentence",
    [
        "Evrakları göster.",
        "İş listesini oku.",
        "Bu işi yap.",
        "Evini ara.",
        "Ev ödevini hatırlat.",
        "İşlem yap.",
        "İstediğim videoyu aç.",
        "Hesap makinesini aç.",
        "Office programını aç.",
        "Ofisiyal bir belge aç.",
    ],
)
def test_ordinary_words_are_not_a_device(sentence: str) -> None:
    assert extract_aliases(sentence) == ()


def test_ordinary_words_reach_no_target_through_the_real_relay(monkeypatch, tmp_path) -> None:
    world = _both_online(monkeypatch, tmp_path)
    sid = _bound_session(world, "GMKADIRAKBABA")
    for turn, sentence in enumerate(("Evrakları göster.", "İş listesini oku.", "Bu işi yap."), 1):
        response = world.client.post(
            f"/v1/voice/realtime/sessions/{sid}/events",
            json={
                "events": [
                    {"kind": "utterance", "t_ms": turn * 1000, "turn": turn, "text": sentence}
                ]
            },
        )
        assert response.status_code == 200, response.text
        assert _last_utterance(world, sid).get("device_targets") == []


def test_the_closed_locative_forms_the_grammar_already_knows_are_targets(
    monkeypatch, tmp_path
) -> None:
    """Not widened, not narrowed: ADR-0205's closed forms name a device, "evde mi" included."""
    assert extract_aliases("Evde mi?") == ("ev",)
    assert extract_aliases("Ofiste hesap makinesini aç") == ("ofis",)
    assert extract_aliases("evdeki dosyayı ofis bilgisayarımda aç") == ("ev", "ofis")
    assert extract_aliases("hesap makinesini aç") == ()
    assert extract_aliases("") == ()


# ------------------------------------------------------------------ the port itself


def test_the_probe_and_the_run_of_a_named_port_pick_the_same_device(monkeypatch, tmp_path) -> None:
    world = _both_online(monkeypatch, tmp_path)
    for session_at in ("MAIL", "GMKADIRAKBABA"):
        for named, expected in (("ofis", "GMKADIRAKBABA"), ("ev", "MAIL")):
            port = world.action.bound_to([world.ids[session_at]], targets=(named,))
            for capability in ("app.launch", "desktop.open_application", "no.such.capability"):
                probe = port.selection_for(capability)
                world.commands.calls.clear()
                ran = port.run(
                    capability=capability, payload={}, idempotency_key="k", timeout_s=1.0
                )
                if probe is None:
                    assert ran.error_class == "no_capable_device", (session_at, named, capability)
                    assert world.commands.calls == []
                else:
                    assert probe.device.name == expected
                    assert probe.reason == selection.REASON_EXPLICIT_ALIAS
                    assert ran.device_id == probe.device.id
                    assert ran.selection_reason == selection.REASON_EXPLICIT_ALIAS


def test_a_named_port_needs_no_session_and_an_empty_binding_is_still_the_same_port(
    monkeypatch, tmp_path
) -> None:
    world = _both_online(monkeypatch, tmp_path)
    assert world.action.bound_to([]) is world.action
    assert world.action.bound_to([], targets=()) is world.action
    named = world.action.bound_to([], targets=("ofis",))
    assert named is not world.action
    assert named.selection_for("desktop.open_application").device.name == "GMKADIRAKBABA"


def test_binding_again_replaces_the_target_and_never_accumulates(monkeypatch, tmp_path) -> None:
    world = _both_online(monkeypatch, tmp_path)
    once = world.action.bound_to([world.ids["MAIL"]], targets=("ofis",))
    again = once.bound_to([world.ids["MAIL"]])
    assert again.selection_for("desktop.open_application").device.name == "MAIL"


def test_the_named_refusal_wording_is_the_one_selection_already_had(monkeypatch, tmp_path) -> None:
    world = _both_online(monkeypatch, tmp_path)
    world.action.bound_to([], targets=("ofis",))  # constructs without a broker touching anything
    ran = world.action.bound_to([world.ids["MAIL"]], targets=("ofis",)).run(
        capability="app.launch", payload={}, idempotency_key="k", timeout_s=1.0
    )
    assert ran.message == "'ofis' cihazı 'app.launch' yeteneğine sahip değil."
    assert world.commands.calls == []


# ----------------------------------------------------- logging: the words are never kept


def test_the_selection_log_names_the_alias_and_never_the_sentence(monkeypatch, tmp_path) -> None:
    from structlog.testing import capture_logs

    world = _both_online(monkeypatch, tmp_path)
    sentence = "Ofis bilgisayarımda hesap makinesini aç."
    with capture_logs() as logs:
        _open(world, _bound_session(world, "MAIL"), sentence)
    selected = [e for e in logs if e.get("event") == "broker_device_action_selected"]
    assert selected and all(e.get("reason") == "explicit_alias" for e in selected)
    assert all(e.get("device_alias") == "ofis" for e in selected)
    assert all("hesap makinesini" not in str(v).lower() for e in logs for v in e.values())


def test_the_relay_records_canonical_alias_words_only(monkeypatch, tmp_path) -> None:
    """What the new field holds is the closed set of words the grammar produces - not a
    phrase, not a name the owner typed."""
    world = _both_online(monkeypatch, tmp_path)
    sid = _bound_session(world, "MAIL")
    _say(world.client, sid, "Ofis bilgisayarımda hesap makinesini aç.")
    assert _last_utterance(world, sid)["device_targets"] == ["ofis"]
    assert set(_last_utterance(world, sid)["device_targets"]) <= CANONICAL_ALIASES
    assert targets_of_turn({"device_targets": ["ofis", "MAIL", 3, "C:\\x"]}) == ("ofis",)
    assert targets_of_turn({"device_targets": "ofis"}) == ()
    assert targets_of_turn(None) == ()


# ---------------------------------------------------- the mission path (requirement 8)


def _start_mission(world: _World, sid: str, sentence: str) -> dict[str, Any]:
    _say(world.client, sid, sentence)
    call = _tool(world.client, sid, "operator.mission", {"action": "start"})
    assert call["status"] == "succeeded", call
    return call["result"]


def _mission_row(world: _World) -> OperatorMissionRow:
    with world.factory() as db:
        return db.scalars(select(OperatorMissionRow)).one()


def test_a_mission_that_names_a_device_carries_the_target_on_its_row(monkeypatch, tmp_path) -> None:
    world = _with_missions(_both_online(monkeypatch, tmp_path))
    _start_mission(
        world,
        _bound_session(world, "GMKADIRAKBABA"),
        "Ev bilgisayarımda Not Defteri'ni aç ve merhaba yaz.",
    )
    assert _mission_row(world).mission_json["device_targets"] == ["ev"]


def test_a_mission_naming_no_device_has_no_target_key_at_all(monkeypatch, tmp_path) -> None:
    world = _with_missions(_both_online(monkeypatch, tmp_path))
    _start_mission(world, _bound_session(world, "MAIL"), "Not Defteri'ni aç ve merhaba yaz.")
    assert "device_targets" not in _mission_row(world).mission_json


def test_a_named_mission_runs_every_step_on_the_named_device(monkeypatch, tmp_path) -> None:
    """The steps run later, in a worker, over the unbound port: the named device has to come
    from the ROW. Both machines carry the Operator here and the home PC is the one the
    ordinary rule prefers (enrolled last), so a mission naming the OFFICE lands there only if
    the row's target reaches the port."""
    from app.operator import mission_service
    from app.operator.mission import MissionPorts

    world = _with_missions(
        _wired(
            monkeypatch,
            tmp_path,
            {
                "GMKADIRAKBABA": {"capabilities": HOME_CAPABILITIES, "aliases": ["ofis"]},
                "MAIL": {"capabilities": HOME_CAPABILITIES, "aliases": ["ev"]},
            },
        )
    )
    with world.factory() as db:
        # Who the ordinary rule prefers is decided by last_seen (a clock tie on Windows is
        # not a preference): MAIL a second ago, the office a minute ago.
        now = datetime.now(UTC)
        db.get(Device, world.ids["MAIL"]).last_seen_at = now - timedelta(seconds=1)
        db.get(Device, world.ids["GMKADIRAKBABA"]).last_seen_at = now - timedelta(seconds=60)
        db.commit()
    unnamed = world.action.selection_for("app.launch")
    assert unnamed is not None and unnamed.device.name == "MAIL", "the ordinary rule prefers MAIL"
    with world.factory() as db:
        row = mission_service.start_mission_db(
            db,
            text="Ofis bilgisayarımda Not Defteri'ni aç ve merhaba yaz.",
            device_targets=("ofis",),
        )
        mission_service.run_step_db(db, row.id, MissionPorts(device=world.action))

    assert world.commands.calls, "the first step ran"
    assert _devices_hit(world) == {world.ids["GMKADIRAKBABA"]}


def test_a_named_mission_never_runs_on_another_device_when_the_named_one_cannot(
    monkeypatch, tmp_path
) -> None:
    """The office has no Operator; the home PC has. "Ofis bilgisayarımda ... aç ve yaz" stops
    on the office, with the reason - it does not become a mission on the home PC."""
    from app.operator import mission_service
    from app.operator.mission import MISSION_PAUSED, MissionPorts

    world = _with_missions(_both_online(monkeypatch, tmp_path))
    with world.factory() as db:
        row = mission_service.start_mission_db(
            db,
            text="Ofis bilgisayarımda hesap makinesini aç ve 5 yaz.",
            device_targets=("ofis",),
        )
        mission_service.run_step_db(db, row.id, MissionPorts(device=world.action))
        row = mission_service.get_mission(db, row.id)

    assert world.commands.calls == []
    assert row.status == MISSION_PAUSED
    assert row.mission_json["escalation"]["error_class"] == "no_capable_device"


def test_a_mission_naming_two_devices_is_refused_and_nothing_is_planned(
    monkeypatch, tmp_path
) -> None:
    world = _with_missions(_both_online(monkeypatch, tmp_path))
    body = _start_mission(
        world,
        _bound_session(world, "MAIL"),
        "Evdeki Not Defteri'ni ofis bilgisayarımda aç ve merhaba yaz.",
    )
    assert body["status"] == "needs_clarification"
    assert "ev" in body["speech"] and "ofis" in body["speech"]
    with world.factory() as db:
        assert db.scalars(select(OperatorMissionRow)).all() == []
    assert world.commands.calls == []


def test_a_named_mission_on_a_port_that_cannot_be_bound_fails_instead_of_running_elsewhere(
    monkeypatch, tmp_path
) -> None:
    from app.operator import mission_service
    from app.operator.mission import MISSION_FAILED, MissionPorts
    from app.routines.dispatch import DeviceRunResult

    class _PlainPort:
        """A port that has no ``bound_to``: it would take the command to whatever device."""

        def __init__(self) -> None:
            self.calls = 0

        def run(self, **_kwargs: Any) -> DeviceRunResult:
            self.calls += 1
            return DeviceRunResult(True)

    world = _with_missions(_both_online(monkeypatch, tmp_path))
    port = _PlainPort()
    with world.factory() as db:
        row = mission_service.start_mission_db(
            db, text="Ev bilgisayarımda Not Defteri'ni aç ve merhaba yaz.", device_targets=("ev",)
        )
        outcome = mission_service.run_step_db(db, row.id, MissionPorts(device=port))
    assert port.calls == 0
    assert outcome["status"] == MISSION_FAILED
