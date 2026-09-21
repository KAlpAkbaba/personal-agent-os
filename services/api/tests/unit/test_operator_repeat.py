"""ADR-0195 (owner note 1, 2026-09-21): "Yukarı tuşuna 5 kere bas."

The owner: "however much I try, I have to say every action one by one - if I need the up
key five times I say it five times; 'beş defa' or 'beş kere yap' is not accepted." Three
rules, each through the REAL relay against the fake device:

* the count the owner SAID is the count of guarded key steps after the one activate, and
  the receipt says so ("5 kere bastım");
* the focus guard stops the rest mid-way and the receipt counts what ran (3 of 5), never
  retrying a press that was sent;
* a count past the plan's bound is refused in words, never quietly done once.
"""

from __future__ import annotations

import pytest

from app.operator import plans
from app.operator.plans import MAX_REPEAT
from app.routines.dispatch import DeviceRunResult
from app.voice.intents import (
    MAX_SPOKEN_REPEAT,
    Intent,
    normalize_transcript,
    resolve_intent,
    spoken_repeat,
)
from tests.alarms_support import window_id as window_id_for
from tests.unit.test_operator_tools import _create, _focus_window, _say, _tool, _wired


def _tokens(text: str) -> tuple[str, ...]:
    return normalize_transcript(text)[1]


# ------------------------------------------------------------------- the words


@pytest.mark.parametrize(
    ("said", "count"),
    [
        ("beş kere", 5),
        ("5 kere", 5),
        ("beş defa", 5),
        ("iki kez", 2),
        ("üç sefer", 3),
        ("on beş kere", 15),
        ("yirmi kere", 20),
        ("bir kere", 1),
        ("kırk kere", 40),  # over the bound: still READ, the tool refuses it aloud
        ("kere", None),
        ("bu kez", None),
        ("birkaç kere", None),
        ("enter'a bas", None),
    ],
)
def test_the_count_the_owner_said(said: str, count: int | None) -> None:
    assert spoken_repeat(_tokens(said)) == count


def test_the_bounds_agree() -> None:
    """The router's bound and the plan's bound are one number, stated twice on purpose
    (the router has no plan to import); a drift here is a sentence the tool refuses for
    a count the plan would have taken."""
    assert MAX_SPOKEN_REPEAT == MAX_REPEAT


@pytest.mark.parametrize(
    ("text", "intent", "key", "direction", "count"),
    [
        ("Yukarı tuşuna 5 kere bas.", Intent.OPERATOR_KEY, "up", None, 5),
        ("Yukarı ok tuşuna beş defa bas.", Intent.OPERATOR_KEY, "up", None, 5),
        ("Beş kere enter'a bas.", Intent.OPERATOR_KEY, "enter", None, 5),
        ("Enter'a bas.", Intent.OPERATOR_KEY, "enter", None, None),
        ("0 tuşuna 2 kere bas.", Intent.OPERATOR_KEY, "0", None, 2),
        ("Kontrol Z'ye iki kere bas.", Intent.OPERATOR_KEY, "ctrl+z", None, 2),
        ("Üç kere aşağı kaydır.", Intent.OPERATOR_SCROLL, None, "down", 3),
        ("Aşağı kaydır.", Intent.OPERATOR_SCROLL, None, "down", None),
    ],
)
def test_the_count_rides_the_key_or_the_scroll(
    text: str, intent: Intent, key: str | None, direction: str | None, count: int | None
) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is intent, resolved
    assert resolved.key_press == key
    assert resolved.scroll_direction == direction
    assert resolved.repeat_count == count
    assert resolved.to_dict()["repeat_count"] == count


# -------------------------------------------------------------------- the plan


def test_a_repeated_key_is_one_activate_and_n_guarded_steps() -> None:
    steps = plans.press_key(window_id_for(1), "up", count=3)
    assert [s.name for s in steps] == [
        "press_key:activate",
        "press_key:key:1",
        "press_key:key:2",
        "press_key:key:3",
    ]
    assert all(s.retries == 0 for s in steps[1:])
    assert all(s.payload == {"window_id": window_id_for(1), "key": "up"} for s in steps[1:])
    # Once keeps the names every receipt and test already reads.
    assert [s.name for s in plans.press_key(window_id_for(1), "up")] == [
        "press_key:activate",
        "press_key:key",
    ]
    assert [s.name for s in plans.press_shortcut(window_id_for(1), ["ctrl", "z"], count=2)] == [
        "press_shortcut:activate",
        "press_shortcut:shortcut:1",
        "press_shortcut:shortcut:2",
    ]
    scroll = plans.pointer(window_id_for(1), "scroll", x=10, y=10, delta=-3, count=2)
    assert [s.name for s in scroll] == [
        "pointer_scroll:activate",
        "pointer_scroll:scroll:1",
        "pointer_scroll:scroll:2",
    ]
    # The repeated payloads are copies: a device that mutates one cannot leak into the next.
    assert scroll[1].payload is not scroll[2].payload


@pytest.mark.parametrize("count", [0, -1, MAX_REPEAT + 1, True, 2.0])
def test_a_count_outside_the_bound_is_not_a_plan(count: object) -> None:
    with pytest.raises(ValueError):
        plans.press_key(window_id_for(1), "up", count=count)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        plans.pointer(window_id_for(1), "scroll", x=1, y=1, delta=-3, count=count)  # type: ignore[arg-type]


# ------------------------------------------------------------------- the relay


def test_five_times_is_five_guarded_presses_and_the_receipt_says_so() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Yukarı tuşuna 5 kere bas.")
    call = _tool(client, sid, "operator.key", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert body["terminal_status"] == "verified"
    assert body["repeat_count"] == 5
    assert device.capabilities_called() == ["window.list", "window.activate"] + ["keyboard.key"] * 5
    assert all(
        c["payload"] == {"window_id": window_id_for(1), "key": "up"}
        for c in device.calls
        if c["capability"] == "keyboard.key"
    )
    assert "5 kere bastım" in body["speech"], body["speech"]
    server = body["observed_after"]["server"]
    assert server["steps_completed"] == 6 and server["step_count"] == 6


def test_once_still_reads_as_it_always_did() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Enter'a bas.")
    body = _tool(client, sid, "operator.key", {})["result"]
    assert body["repeat_count"] == 1
    assert body["speech"] == "Enter tuşuna bastım efendim."
    assert device.capabilities_called().count("keyboard.key") == 1


def test_the_focus_guard_stops_the_rest_and_the_receipt_counts_what_ran() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    happy = device.results["keyboard.key"]
    presses = {"n": 0}

    def _third_press_loses_focus(payload: dict) -> DeviceRunResult:
        presses["n"] += 1
        if presses["n"] == 3:
            return DeviceRunResult(
                False, "focus_mismatch", "expected w-1 (notepad.exe), actual w-9 (chrome.exe)"
            )
        return happy(payload)

    device.results["keyboard.key"] = _third_press_loses_focus
    sid = _create(client)
    _say(client, sid, "Yukarı tuşuna 5 kere bas.")
    body = _tool(client, sid, "operator.key", {})["result"]
    assert body["execution_status"] == "failed"
    assert body["error_class"] == "focus_mismatch"
    server = body["observed_after"]["server"]
    assert server["steps_completed"] == 3 and server["step_count"] == 6
    assert server["stopped_at"] == "press_key:key:3"
    # retries=0 on every press: the guard's refusal is not answered by sending again.
    assert device.capabilities_called().count("keyboard.key") == 3


def test_more_than_the_bound_is_refused_in_words_not_done_once() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, f"Yukarı tuşuna {MAX_REPEAT + 10} kere bas.")
    call = _tool(client, sid, "operator.key", {})
    assert call["status"] == "needs_clarification", call
    assert str(MAX_REPEAT) in call["result"]["speech"]
    assert "keyboard.key" not in device.capabilities_called()


def test_a_chord_said_twice_is_two_chords() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Kontrol Z'ye iki kere bas.")
    body = _tool(client, sid, "operator.key", {})["result"]
    assert body["execution_status"] == "executed", body
    assert device.capabilities_called().count("keyboard.shortcut") == 2
    assert "2 kere" in body["speech"]


def test_a_scroll_said_three_times_is_three_scrolls() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Üç kere aşağı kaydır.")
    body = _tool(client, sid, "operator.pointer", {})["result"]
    assert body["execution_status"] == "executed", body
    assert body["repeat_count"] == 3
    assert device.capabilities_called().count("pointer.scroll") == 3
    assert body["speech"] == "3 kere kaydırdım efendim."


def test_the_owners_count_wins_over_the_models_and_the_models_counts_when_none_was_said() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Enter'a üç kere bas.")
    body = _tool(client, sid, "operator.key", {"count": 2})["result"]
    assert body["repeat_count"] == 3
    assert device.capabilities_called().count("keyboard.key") == 3
    device.calls.clear()
    _say(client, sid, "Enter'a bas.")
    body = _tool(client, sid, "operator.key", {"count": 2})["result"]
    assert body["repeat_count"] == 2
    assert device.capabilities_called().count("keyboard.key") == 2
