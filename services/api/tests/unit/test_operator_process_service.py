"""B30 req 82, 84-88, 118-122: an application closed by name, a window moved and resized
with its rect re-observed, the third shell query, and the process/service family with
its two policies refused BEFORE any device is asked.

Every sentence here runs through the real relay (``_say``) and the real tool
(``_tool``), against the fake device ``tests/alarms_support`` scripts, so the fields the
router resolves (``application``, ``process_name``, ``service_name``, ``shell_query``)
are proven to travel from the utterance to the device payload — the B28 lesson, where a
field lived on the intent and never reached its tool.
"""

from __future__ import annotations

from app.operator.capabilities import (
    CAPABILITY_APP_CLOSE,
    CAPABILITY_PROCESS,
    CAPABILITY_SERVICE,
    OPERATOR_CAPABILITIES,
    PLAN_BY_PROCESS_ACTION,
    PLAN_BY_SERVICE_ACTION,
    PLAN_BY_SHELL_QUERY,
    PLAN_BY_WINDOW_ACTION,
    PLAN_CLOSE_APPLICATION,
    RECEIPT_BY_PLAN,
)
from app.routines.dispatch import DeviceRunResult
from app.security import step_up
from app.voice.intents import Intent, resolve_intent
from app.voice.realtime_sessions.tools import OPERATOR_CLARIFYING_TOOLS
from tests.alarms_support import failed, ok
from tests.alarms_support import window_id as window_id_for
from tests.unit.test_operator_capability_regression import PLAN_STEPS
from tests.unit.test_operator_tools import _create, _focus_window, _say, _tool, _wired

NOTEPAD = {
    "window_id": window_id_for(1),
    "pid": 4242,
    "image": "C:\\Windows\\System32\\notepad.exe",
    "title": "Adsız - Not Defteri",
    "state": "normal",
    "foreground": True,
}
CALC = {
    "window_id": window_id_for(0),
    "pid": 4100,
    "image": "calc.exe",
    "title": "Hesap Makinesi",
    "state": "normal",
    "foreground": False,
}


def _desktop(device, *rows: dict) -> None:
    """The device's window list: the rows given, minus any window an ``app.close`` or a
    ``window.close`` in the device's own call log already closed."""

    def _listing(_payload, _device=device, _rows=rows):
        closed = {
            call["payload"].get("window_id")
            for call in _device.calls
            if call["capability"] in ("app.close", "window.close")
        }
        return ok(windows=[dict(r) for r in _rows if r["window_id"] not in closed])

    device.results["window.list"] = _listing


# ----------------------------------------------------------------- the vocabulary


def test_the_three_tools_are_declared_governed_and_may_ask() -> None:
    assert {CAPABILITY_APP_CLOSE, CAPABILITY_PROCESS, CAPABILITY_SERVICE} <= set(
        OPERATOR_CAPABILITIES
    )
    assert RECEIPT_BY_PLAN[PLAN_CLOSE_APPLICATION] == CAPABILITY_APP_CLOSE
    for plan_name in PLAN_BY_PROCESS_ACTION.values():
        assert RECEIPT_BY_PLAN[plan_name] == CAPABILITY_PROCESS
    for plan_name in PLAN_BY_SERVICE_ACTION.values():
        assert RECEIPT_BY_PLAN[plan_name] == CAPABILITY_SERVICE
    assert RECEIPT_BY_PLAN[PLAN_BY_SHELL_QUERY["whoami"]] == "operator.shell"
    assert step_up.tier_of(CAPABILITY_APP_CLOSE) == step_up.TIER_SENSITIVE
    assert step_up.tier_of(CAPABILITY_PROCESS) == step_up.TIER_SENSITIVE
    assert step_up.tier_of(CAPABILITY_SERVICE) == step_up.TIER_CRITICAL, "machine level"
    for name in (CAPABILITY_APP_CLOSE, CAPABILITY_PROCESS, CAPABILITY_SERVICE):
        assert name in OPERATOR_CLARIFYING_TOOLS, name
    assert {"move", "resize"} <= set(PLAN_BY_WINDOW_ACTION)


def test_the_plan_table_grew_by_the_batchs_nine_and_every_plan_still_ends_verified() -> None:
    assert len(PLAN_STEPS) >= 29
    for plan_name, build in PLAN_STEPS.items():
        assert build()[-1].postcondition is not None, plan_name


# ---------------------------------------------------------------------- intents


def test_the_sentences_route_with_their_names() -> None:
    r = resolve_intent("Not Defteri'ni kapat.")
    assert r.intent is Intent.APP_CLOSE and r.application == "notepad"
    r = resolve_intent("Chrome çalışıyor mu?")
    assert r.intent is Intent.PROCESS_QUERY and r.process_name == "chrome"
    r = resolve_intent("Hangi uygulamalar açık?")
    assert r.intent is Intent.PROCESS_QUERY and r.process_name is None
    r = resolve_intent("Chrome'u sonlandır.")
    assert r.intent is Intent.PROCESS_STOP and r.process_name == "chrome"
    r = resolve_intent("Yazdırma servisi çalışıyor mu?")
    assert r.intent is Intent.SERVICE_QUERY and r.service_name == "yazdırma"
    r = resolve_intent("Spooler servisini yeniden başlat.")
    assert r.intent is Intent.SERVICE_RESTART and r.service_name == "spooler"
    r = resolve_intent("Kullanıcı adım ne?")
    assert r.intent is Intent.SHELL_QUERY and r.shell_query == "whoami"
    # The neighbours keep their families.
    assert resolve_intent("Bunu kapat.").intent is Intent.WINDOW_CLOSE
    assert resolve_intent("Alarmı kapat.").intent is Intent.ALARM_STOP
    assert resolve_intent("Yeniden başlat.").intent is Intent.REPEAT
    assert resolve_intent("Not Defteri'ni aç.").intent is Intent.APP_OPEN


# -------------------------------------------------------------------- app_close


def test_an_application_named_by_the_owner_is_closed_through_its_own_window() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory)
    _desktop(device, CALC, NOTEPAD)
    sid = _create(client)
    _say(client, sid, "Not Defteri'ni kapat.")
    body = _tool(client, sid, "operator.app_close", {"application": "Hesap Makinesi"})["result"]
    assert body["execution_status"] == "executed", body
    assert body["terminal_status"] == "verified"
    # The owner's word wins over the model's; the window is found by IMAGE in the device's
    # own list, then closed, then the list is read again and it is gone.
    assert device.capabilities_called() == ["window.list", "app.close", "window.list"]
    assert device.payload_for("app.close") == {"window_id": window_id_for(1)}
    assert body["speech"] == "Not Defteri uygulamasını kapattım efendim."


def test_an_application_that_is_not_open_is_already_closed_and_nothing_is_sent() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory)
    _desktop(device, NOTEPAD)
    sid = _create(client)
    _say(client, sid, "Chrome'u kapat.")
    body = _tool(client, sid, "operator.app_close", {})["result"]
    assert body["execution_status"] == "noop"
    assert body["terminal_status"] == "already"
    assert device.capabilities_called() == ["window.list"]
    assert body["speech"] == "Chrome zaten açık değil efendim."


def test_a_save_prompt_is_reported_never_answered() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory)
    _desktop(device, NOTEPAD)
    device.results["app.close"] = ok(
        closed=False,
        method="wm_close",
        modal={"window_id": window_id_for(7), "dialog": {"buttons": ["Kaydet", "Kaydetme"]}},
        observed={"process_alive": True},
    )
    sid = _create(client)
    _say(client, sid, "Not Defteri'ni kapat.")
    body = _tool(client, sid, "operator.app_close", {})["result"]
    assert body["execution_status"] == "failed"
    assert body["error_class"] == "modal_open"
    assert "kaydedilmemiş" in body["speech"]
    assert device.capabilities_called() == ["window.list", "app.close"], "no second attempt"
    assert not any(c["capability"].startswith("ui.") for c in device.calls)


def test_no_application_and_no_window_is_a_question() -> None:
    client, _factory, device, _operator = _wired()
    sid = _create(client)
    call = _tool(client, sid, "operator.app_close", {})
    assert call["status"] == "needs_clarification"
    assert device.calls == []


# ----------------------------------------------------------------- move / resize


def test_a_move_is_re_observed_within_the_tolerance() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    body = _tool(
        client, sid, "operator.window_control", {"action": "move", "x": 100, "y": 120}
    )["result"]
    assert body["execution_status"] == "executed", body
    assert body["terminal_status"] == "verified"
    assert device.payload_for("window.move") == {"window_id": window_id_for(1), "x": 100, "y": 120}
    assert body["speech"] == "Pencereyi taşıdım efendim."


def test_a_resize_whose_rect_did_not_land_is_a_failure() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    device.results["window.resize"] = ok(
        window={**NOTEPAD, "rect": {"x": 0, "y": 0, "width": 640, "height": 600}}
    )
    sid = _create(client)
    body = _tool(
        client, sid, "operator.window_control", {"action": "resize", "width": 800, "height": 600}
    )["result"]
    assert body["execution_status"] == "failed"
    assert body["error_class"] == "postcondition_failed"
    assert body["speech"].startswith("Pencereyi boyutlandıramadım")


def test_a_move_without_coordinates_is_a_validation_error_before_any_device_call() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    call = _tool(client, sid, "operator.window_control", {"action": "move", "x": 100})
    assert call["status"] == "failed", call
    assert not any(c["capability"] == "window.move" for c in device.calls)


# ------------------------------------------------------------------------ whoami


def test_the_user_name_is_read_from_whoami_and_the_domain_is_dropped() -> None:
    client, _factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Kullanıcı adım ne?")
    body = _tool(client, sid, "operator.shell", {"query": "ip"})["result"]
    assert body["execution_status"] == "executed", body
    assert device.payload_for("terminal.execute") == {"command": "whoami"}
    assert body["speech"] == "Kullanıcı adınız alp."


# ---------------------------------------------------------------------- process


def test_a_named_process_is_listed_by_image_and_the_answer_counts_it() -> None:
    client, _factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Chrome çalışıyor mu?")
    body = _tool(client, sid, "operator.process", {"action": "stop", "name": "notepad"})[
        "result"
    ]
    assert body["execution_status"] == "executed", body
    assert device.capabilities_called() == ["process.list"]
    assert device.payload_for("process.list") == {"name": "chrome.exe"}
    assert body["speech"] == "Chrome çalışıyor efendim (1 süreç)."
    assert [p["image"] for p in body["processes"]] == ["chrome.exe"]


def test_an_unnamed_listing_names_what_is_open() -> None:
    client, _factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Hangi uygulamalar açık?")
    body = _tool(client, sid, "operator.process", {})["result"]
    assert device.payload_for("process.list") == {}
    assert body["speech"] == "2 uygulama açık efendim: chrome, notepad."


def test_a_process_that_is_not_running_is_said_so() -> None:
    client, _factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Paint açık mı?")
    body = _tool(client, sid, "operator.process", {})["result"]
    assert device.payload_for("process.list") == {"name": "mspaint.exe"}
    assert body["speech"] == "Paint çalışmıyor efendim."


def test_a_stop_is_sent_once_and_verified_by_a_listing_that_shows_none() -> None:
    client, _factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Chrome'u sonlandır.")
    body = _tool(client, sid, "operator.process", {})["result"]
    assert body["execution_status"] == "executed", body
    assert body["terminal_status"] == "verified"
    assert device.capabilities_called() == ["process.stop", "process.list"]
    assert device.payload_for("process.stop") == {"name": "chrome.exe"}
    assert body["speech"] == "Chrome sürecini sonlandırdım efendim."


def test_a_stop_the_device_could_not_verify_is_a_failure() -> None:
    client, _factory, device, _operator = _wired()
    device.results["process.stop"] = ok(stopped=True, name="chrome.exe", method="wm_close")
    # The listing after the stop still shows chrome (the fake never marked it stopped).
    device.results["process.list"] = ok(
        processes=[{"pid": 9088, "image": "chrome.exe", "name": "chrome"}]
    )
    sid = _create(client)
    _say(client, sid, "Chrome'u sonlandır.")
    body = _tool(client, sid, "operator.process", {})["result"]
    assert body["execution_status"] == "failed"
    assert body["error_class"] == "postcondition_failed"
    assert body["speech"] == "Chrome sürecini sonlandıramadım efendim."


def test_a_stop_outside_the_policy_is_refused_before_any_device_call() -> None:
    client, _factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "PowerShell'i sonlandır.")
    body = _tool(client, sid, "operator.process", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "permission_denied"
    assert body["observed_after"]["server"]["reason"] == "not_in_stop_policy"
    assert device.calls == []
    assert "politikaya aykırı" in body["speech"]


def test_a_stop_without_a_name_is_a_question() -> None:
    client, _factory, device, _operator = _wired()
    sid = _create(client)
    call = _tool(client, sid, "operator.process", {"action": "stop"})
    assert call["status"] == "needs_clarification"
    assert device.calls == []


# ---------------------------------------------------------------------- service


def test_a_service_named_in_turkish_is_read_by_its_windows_name() -> None:
    client, _factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Yazdırma servisi çalışıyor mu?")
    body = _tool(client, sid, "operator.service", {"action": "restart", "name": "bthserv"})[
        "result"
    ]
    assert body["execution_status"] == "executed", body
    assert device.capabilities_called() == ["service.status"]
    assert device.payload_for("service.status") == {"name": "Spooler"}
    assert body["speech"] == "Spooler servisi çalışıyor efendim."
    assert body["state"] == "Running"


def test_a_service_that_does_not_exist_is_said_so() -> None:
    client, _factory, device, _operator = _wired()
    device.results["service.status"] = failed("ui_target_not_found", "no service named 'X'")
    sid = _create(client)
    _say(client, sid, "Fax servisi çalışıyor mu?")
    body = _tool(client, sid, "operator.service", {})["result"]
    assert body["execution_status"] == "failed"
    assert body["speech"] == "fax adında bir servis yok efendim."


def test_a_restart_is_verified_by_reading_the_service_running_again() -> None:
    client, _factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Spooler servisini yeniden başlat.")
    body = _tool(client, sid, "operator.service", {})["result"]
    assert body["execution_status"] == "executed", body
    assert body["terminal_status"] == "verified"
    assert device.capabilities_called() == ["service.restart", "service.status"]
    assert device.payload_for("service.restart") == {"name": "Spooler"}
    assert body["speech"] == "Spooler servisini yeniden başlattım efendim."


def test_a_restart_outside_the_policy_is_refused_before_any_device_call() -> None:
    client, _factory, device, _operator = _wired()
    sid = _create(client)
    _say(client, sid, "Bluetooth servisini yeniden başlat.")
    body = _tool(client, sid, "operator.service", {})["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "permission_denied"
    assert body["observed_after"]["server"]["reason"] == "not_in_restart_policy"
    assert device.calls == []


def test_an_unelevated_companion_is_an_honest_uac_answer() -> None:
    """The device refuses with ``permission_denied`` (UAC is the owner's, never bypassed):
    the task fails with that class, no retry, and the owner hears why."""
    client, _factory, device, _operator = _wired()
    device.results["service.restart"] = failed(
        "permission_denied", "restarting 'Spooler' needs an elevated companion (UAC)"
    )
    sid = _create(client)
    _say(client, sid, "Yazdırma servisini yeniden başlat.")
    body = _tool(client, sid, "operator.service", {})["result"]
    assert body["execution_status"] == "failed"
    assert body["error_class"] == "permission_denied"
    assert device.capabilities_called() == ["service.restart"], "no retry, no status read"
    assert "yönetici yetkisi" in body["speech"]


def test_the_device_result_does_not_carry_a_service_state_the_tool_invents() -> None:
    """A status the device answered without a state fails the plan's postcondition; the
    speech says it could not read, never a guessed state."""
    client, _factory, device, _operator = _wired()
    device.results["service.status"] = DeviceRunResult(True, result={"name": "Spooler"})
    sid = _create(client)
    _say(client, sid, "Spooler servisi çalışıyor mu?")
    body = _tool(client, sid, "operator.service", {})["result"]
    assert body["execution_status"] == "failed"
    assert body["speech"] == "Spooler servisinin durumunu okuyamadım efendim."
    assert body["state"] is None
