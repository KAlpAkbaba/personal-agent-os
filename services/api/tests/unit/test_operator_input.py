"""B28 req 91-98, 107, 109, 110: the operator's input, through the REAL relay.

The device has answered ``keyboard.key`` / ``keyboard.shortcut`` / ``pointer.*`` since M19
(matrix rows 92-98: "Çağıran yok"). This file is about the callers this batch adds and the
three rules the test plan names:

* **FocusGuard stops the send, and the partial send is accounted for** - a
  ``focus_mismatch`` from the device mid-plan ends the task with that class, the receipt
  says how many steps ran and which one was refused, and the owner hears that the window
  moved rather than "failed".
* **The device's ``secret`` flag is SENT from the cloud** and the server gate refuses a
  secret-looking text before any device call; the companion's ``RefuseSecret`` reads the
  same key this plan writes (both halves read each other).
* **Raw coordinates are the last resort** - ``operator.pointer`` refuses a click that does
  not say so, records the justification when it does, and a spoken scroll lands on the
  window's own centre.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.operator import plans
from app.operator.capabilities import (
    CAPABILITY_KEY,
    CAPABILITY_POINTER,
    OPERATOR_CAPABILITIES,
    PLAN_BY_POINTER_ACTION,
    PLAN_PRESS_KEY,
    PLAN_PRESS_SHORTCUT,
    RECEIPT_BY_PLAN,
)
from app.operator.task import LEVEL_KEYBOARD, LEVEL_POINTER
from app.routines.dispatch import DeviceRunResult
from app.security import step_up
from app.voice.intents import Intent, resolve_intent
from tests.alarms_support import window_id as window_id_for
from tests.unit.test_operator_tools import _create, _focus_window, _say, _tool, _wired

REPO = Path(__file__).resolve().parents[4]
COMPANION = REPO / "devices/windows-agent/src/PagentOS.SessionCompanion/Operator"


# ----------------------------------------------------------------- the vocabulary


def test_the_two_tools_are_declared_and_governed() -> None:
    assert {CAPABILITY_KEY, CAPABILITY_POINTER} <= set(OPERATOR_CAPABILITIES)
    assert RECEIPT_BY_PLAN[PLAN_PRESS_KEY] == CAPABILITY_KEY
    assert RECEIPT_BY_PLAN[PLAN_PRESS_SHORTCUT] == CAPABILITY_KEY
    for plan_name in PLAN_BY_POINTER_ACTION.values():
        assert RECEIPT_BY_PLAN[plan_name] == CAPABILITY_POINTER
    assert step_up.tier_of(CAPABILITY_KEY) == step_up.TIER_SENSITIVE
    assert step_up.tier_of(CAPABILITY_POINTER) == step_up.TIER_SENSITIVE


def test_the_key_vocabulary_is_the_companions() -> None:
    """Both halves read each other: every key the server accepts is a key the device's
    ``KeyMap.Named`` accepts, and every modifier likewise. A name here the device does not
    know would activate a window and then be refused."""
    source = (COMPANION / "InputSynthesizer.cs").read_text("utf-8")
    device_keys = set()
    for line in source.splitlines():
        line = line.strip()
        if line.startswith('["') and "] = (0x" in line:
            device_keys.add(line[2 : line.index('"]')].lower())
    assert len(device_keys) >= 20, "the KeyMap scan stopped matching"
    assert set(plans.KEY_NAMES) <= device_keys, sorted(set(plans.KEY_NAMES) - device_keys)
    for modifier in plans.MODIFIER_NAMES:
        assert f'["{modifier}"] = 0x' in source, modifier
    assert plans.valid_shortcut(["ctrl", "s"])
    assert plans.valid_shortcut(["ctrl", "shift", "escape"])
    assert not plans.valid_shortcut(["s"]), "a chord needs a modifier"
    assert not plans.valid_shortcut(["ctrl", "alt"]), "a chord needs exactly one key"
    # 2026-09-19 ("0 tuşuna bas"): ONE ASCII letter or digit is a key on BOTH sides - the page's
    # own shortcuts are single characters and must arrive as a real keydown. Both halves are
    # read: the device's validator and its press path allow characters, and so does the plan.
    assert source.count("TryKey(key, allowCharacters: true, out") >= 3, (
        "KeyMap.ValidateKey / PressKey no longer accept a single character"
    )
    assert "TryKey(key, allowCharacters: false" not in source
    assert plans.valid_key("s") and plans.valid_key("0") and plans.valid_key("K")
    for text in ("ab", "ç", "!", " ", "", "merhaba"):
        assert not plans.valid_key(text), f"{text!r} is text, not a key (use operator.type)"


def test_the_secret_flag_travels_and_the_device_reads_the_same_key() -> None:
    """req 109. The plan writes ``secret``; the companion's ``RefuseSecret`` reads
    ``payload["secret"]``. Until this batch the flag never left the cloud."""
    steps = plans.type_text(window_id_for(1), "merhaba")
    typing = next(step for step in steps if step.capability == "keyboard.type")
    assert typing.payload["secret"] is False
    flagged = plans.type_text(window_id_for(1), "şifremi")
    assert next(s for s in flagged if s.capability == "keyboard.type").payload["secret"] is True
    companion = (COMPANION / "OperatorCapabilities.cs").read_text("utf-8")
    assert 'payload["secret"]' in companion
    assert "RefuseSecret(payload)" in companion


# ------------------------------------------------------------------ the intents


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("Enter'a bas.", "enter"),
        ("Escape'e bas.", "escape"),
        ("Tab tuşuna bas.", "tab"),
        ("Boşluğa bas.", "space"),
        ("Yukarı ok tuşuna bas.", "up"),
        ("F5'e bas.", "f5"),
        ("Ctrl S'ye bas.", "ctrl+s"),
        ("Kontrol Z'ye bas.", "ctrl+z"),
        ("Alt F4'e bas.", "alt+f4"),
        ("Ctrl Shift Escape'e bas.", "ctrl+shift+escape"),
    ],
)
def test_a_spoken_key_resolves_to_the_companions_name(text: str, key: str) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is Intent.OPERATOR_KEY, text
    assert resolved.key_press == key
    assert resolved.capability == CAPABILITY_KEY


@pytest.mark.parametrize(
    ("text", "direction"),
    [("Aşağı kaydır.", "down"), ("Yukarı kaydır.", "up"), ("Biraz aşağıya kaydır.", "down")],
)
def test_a_spoken_scroll_resolves_with_its_direction(text: str, direction: str) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is Intent.OPERATOR_SCROLL
    assert resolved.scroll_direction == direction
    assert resolved.capability == CAPABILITY_POINTER


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Düğmeye bas.", Intent.NONE),  # names no key: nothing to press
        ("Dur.", Intent.STOP),
        ("Buraya merhaba yaz.", Intent.TYPE_TEXT),
        ("Alarmı kapat.", Intent.ALARM_STOP),
        ("Pencereyi kapat.", Intent.WINDOW_CLOSE),
        ("Sesi kıs.", Intent.MEDIA_VOLUME),
    ],
)
def test_the_neighbours_keep_their_families(text: str, intent: Intent) -> None:
    assert resolve_intent(text).intent is intent, text


# --------------------------------------------------------------- the tools (relay)


def test_a_spoken_key_is_activate_then_one_guarded_key_re_observed() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Enter'a bas.")
    call = _tool(client, sid, "operator.key", {"key": "escape"})  # the owner's word wins
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert body["terminal_status"] == "verified"
    assert device.capabilities_called() == ["window.list", "window.activate", "keyboard.key"]
    assert device.payload_for("keyboard.key") == {"window_id": window_id_for(1), "key": "enter"}
    assert body["speech"] == "Enter tuşuna bastım efendim."
    server = body["observed_after"]["server"]
    assert server["interaction_level"] == LEVEL_KEYBOARD
    assert server["steps_completed"] == 2 and server["step_count"] == 2


def test_a_chord_is_sent_once_and_read_back() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Ctrl S'ye bas.")
    call = _tool(client, sid, "operator.key", {})
    body = call["result"]
    assert body["execution_status"] == "executed", body
    assert device.payload_for("keyboard.shortcut") == {
        "window_id": window_id_for(1),
        "keys": ["ctrl", "s"],
    }
    assert device.capabilities_called().count("keyboard.shortcut") == 1
    assert "Ctrl+S" in body["speech"] or "CTRL+S" in body["speech"]


def test_focus_guard_refusal_stops_the_send_and_is_accounted_for() -> None:
    """The test plan's first line. The device refuses the key because the foreground moved
    (``focus_mismatch``); nothing is retried, the receipt names the step, counts the one
    that ran, and the owner hears why."""
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    device.results["keyboard.key"] = DeviceRunResult(
        False,
        "focus_mismatch",
        "expected w-1 (notepad.exe 'Adsız - Not Defteri'), actual w-9 (chrome.exe); "
        "nothing was sent",
    )
    sid = _create(client)
    _say(client, sid, "Enter'a bas.")
    body = _tool(client, sid, "operator.key", {})["result"]
    assert body["execution_status"] == "failed"
    assert body["error_class"] == "focus_mismatch"
    assert body["speech"] == "Pencere önden çekildi efendim; gönderimi durdurdum."
    server = body["observed_after"]["server"]
    assert server["steps_completed"] == 1 and server["step_count"] == 2
    assert server["stopped_at"] == "press_key:key"
    # retries=0 on the input step: the guard's refusal is not answered by sending again.
    assert device.capabilities_called().count("keyboard.key") == 1


def test_a_key_the_device_does_not_know_is_refused_before_any_device_call() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    call = _tool(client, sid, "operator.key", {"key": "hyperspace"})
    assert call["status"] == "failed", call
    assert device.calls == []


def test_no_key_named_is_a_question() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    call = _tool(client, sid, "operator.key", {})
    assert call["status"] == "needs_clarification", call
    assert call["result"]["speech"] == "Hangi tuş?"
    assert device.calls == []


def test_a_click_without_last_resort_is_refused_by_policy() -> None:
    """req 107: raw coordinates are the last rung, and the tool says so instead of
    clicking. No window is resolved and no device is touched."""
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    body = _tool(client, sid, "operator.pointer", {"action": "click", "x": 10, "y": 10})["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "coordinate_not_last_resort"
    assert "son çare" in body["speech"]
    assert body["observed_after"]["server"]["interaction_level"] == "pointer"
    assert device.calls == []


def test_a_last_resort_click_runs_records_the_level_and_the_reason() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    body = _tool(
        client,
        sid,
        "operator.pointer",
        {
            "action": "click",
            "x": 40,
            "y": 30,
            "last_resort": True,
            "reason": "ui.inspect returned no invokable element for the button",
        },
    )["result"]
    assert body["execution_status"] == "executed", body
    assert body["terminal_status"] == "verified"
    assert device.capabilities_called() == ["window.list", "window.activate", "pointer.click"]
    assert device.payload_for("pointer.click") == {
        "window_id": window_id_for(1),
        "x": 40,
        "y": 30,
        "space": "window",
    }
    server = body["observed_after"]["server"]
    assert server["interaction_level"] == LEVEL_POINTER
    assert server["last_resort_reason"].startswith("ui.inspect returned")
    assert body["speech"] == "Tıkladım efendim."


def test_a_spoken_scroll_lands_on_the_windows_own_centre() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    # The device's own rect for the focused window: the centre is computed from it.
    row = {
        "window_id": window_id_for(1),
        "title": "Adsız - Not Defteri",
        "foreground": True,
        "rect": {"x": 100, "y": 100, "width": 800, "height": 600},
    }
    device.results["window.list"] = DeviceRunResult(True, result={"windows": [row]})
    sid = _create(client)
    _say(client, sid, "Aşağı kaydır.")
    body = _tool(client, sid, "operator.pointer", {})["result"]
    assert body["execution_status"] == "executed", body
    assert device.payload_for("pointer.scroll") == {
        "window_id": window_id_for(1),
        "x": 400,
        "y": 300,
        "space": "window",
        "delta": -3,
    }
    assert body["speech"] == "Kaydırdım efendim."


def test_a_pointer_that_did_not_land_is_a_failed_receipt_not_a_verified_one() -> None:
    """The device's cursor read-back disagrees with where it was asked to go."""
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    device.results["pointer.click"] = DeviceRunResult(
        True,
        result={
            "x": 40,
            "y": 30,
            "space": "window",
            "screen_x": 140,
            "screen_y": 130,
            "window_id": window_id_for(1),
            "observed": {"cursor": {"x": 900, "y": 900}, "window": {"window_id": window_id_for(1)}},
        },
    )
    sid = _create(client)
    body = _tool(
        client,
        sid,
        "operator.pointer",
        {"action": "click", "x": 40, "y": 30, "last_resort": True, "reason": "test"},
    )["result"]
    assert body["execution_status"] == "failed"
    assert body["error_class"] == "postcondition_failed"
    assert body["terminal_status"] == "failed"


def test_secret_typing_is_refused_at_the_server_gate_before_the_device() -> None:
    """req 109's server half, kept: the flag on the wire is the second gate, not the first."""
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    body = _tool(client, sid, "operator.type", {"content": "parolam 1234"})["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "secret_refused"
    assert device.calls == []


def test_every_operator_action_leaves_exactly_one_receipt(tmp_path) -> None:
    """req 110: one ``action.receipt`` per action, with the accounting fields."""
    from sqlalchemy import select

    from app.ledger.models import ActivityEventRow

    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Enter'a bas.")
    _tool(client, sid, "operator.key", {})
    _say(client, sid, "Aşağı kaydır.")
    _tool(client, sid, "operator.pointer", {})
    with factory() as db:
        rows = [
            r
            for r in db.execute(select(ActivityEventRow)).scalars()
            if r.event_type == "action.receipt" and r.subsystem == "operator"
        ]
    assert [r.action for r in rows] == [CAPABILITY_KEY, CAPABILITY_POINTER]
    for row in rows:
        server = row.detail_json["observed_after"]["server"]
        assert {"steps_completed", "step_count", "interaction_level"} <= set(server)


@pytest.mark.parametrize(
    ("said", "key"),
    [
        # Owner, 2026-09-20: "sağ tuşuna bas komutu çalışmıyor" - and it reached nothing at
        # all, because the arrow branch needed the word "ok" the owner does not say.
        ("Sağ tuşuna bas", "right"),
        ("Sol tuşuna bas", "left"),
        ("Yukarı tuşuna bas", "up"),
        ("Aşağı tuşuna bas", "down"),
        ("Sağ tuşa bas", "right"),
        ("Sol tuşuna bassana", "left"),
        # What already worked, kept working:
        ("Sağ ok tuşuna bas", "right"),
        ("Yukarı oka bas", "up"),
    ],
)
def test_a_direction_before_the_key_noun_is_the_arrow_key(said: str, key: str) -> None:
    resolved = resolve_intent(said)
    assert resolved.intent is Intent.OPERATOR_KEY, (said, resolved.intent)
    assert resolved.key_press == key


@pytest.mark.parametrize(
    "said",
    [
        # The MOUSE's right button is not a key, and "tıkla" is the pointer's own word.
        "Sağ tıkla",
        "Farenin sağ tuşuna bas",
        "Sağ fare tuşuna bas",
    ],
)
def test_the_mouse_button_is_never_read_as_an_arrow_key(said: str) -> None:
    assert resolve_intent(said).key_press is None, said
