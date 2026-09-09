"""A window TITLE is not a window id (incident 2026-09-09).

The owner said "not defterine yaz". The model helpfully filled the tool's optional
``target`` with ``"Not Defteri"``, ``tools_operator._resolve_window_id`` forwarded that
string to the device as a window id because it was neither "current" nor "previous", and
the Windows companion refused it eight times —

    'Not Defteri' is not a window id (expected w-<hwnd>-<tick>)

— while the correct id for that very window (``w-10160952-365601875``, labelled
"Adsız - Not Defteri") sat in the durable focus stack, written thirteen seconds earlier
by the ``app.launch`` that opened it. No key was ever pressed, and the owner was told
"metni doğrulayamadım" — a sentence about verifying text that was never typed.

Every test here fails against that implementation. They are driven through the REAL
application object (the same relay/router/tool path ``test_operator_tools`` uses), and
the fake device port now refuses the ids the real one refuses, so a regression cannot
hide behind a lenient fixture.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.operator import focus as operator_focus
from app.operator.models import FOCUS_KIND_WINDOW
from app.voice.realtime_sessions import tools_operator
from tests.alarms_support import device_window_id_ok
from tests.alarms_support import window_id as window_id_for
from tests.unit.test_operator_tools import _create, _say, _tool, _wired

#: The device source both halves must keep agreeing with.
_WINDOW_REGISTRY_CS = (
    Path(__file__).resolve().parents[4]
    / "devices"
    / "windows-agent"
    / "src"
    / "PagentOS.SessionCompanion"
    / "Operator"
    / "WindowRegistry.cs"
)

#: An utterance the intent resolver does NOT read a window out of. It has to be this way:
#: when the owner's own words settle the window ("buraya", "bunu"), those words win over
#: the model's argument by design, and a test that says them is testing the wrong half.
#: The 2026-09-09 incident is exactly the other case — the model named a window because
#: nothing in the utterance had.
NEUTRAL = "Merhaba."

NOTEPAD = window_id_for(1)
CALCULATOR = window_id_for(2)


def _remember(factory, entries: list[tuple[str, str]]) -> None:
    """Write a focus stack, oldest first, on explicit staggered instants. Memory ONLY:
    a window in here may have been closed since."""
    base = datetime.now(UTC)
    with factory() as db:
        for index, (object_id, label) in enumerate(entries):
            operator_focus.set_focus(
                db,
                FOCUS_KIND_WINDOW,
                object_id,
                label=label,
                source="t",
                now=base + timedelta(seconds=index),
            )


def _on_desktop(device, entries: list[tuple[str, str]]) -> None:
    """What ``window.list`` answers: the windows that actually exist right now."""
    device.results["window.list"] = _ok(
        windows=[{"window_id": wid, "title": title} for wid, title in entries]
    )


def _focus(factory, entries: list[tuple[str, str]], device=None) -> None:
    """The ordinary case: these windows are open AND the owner has been in them."""
    _remember(factory, entries)
    if device is not None:
        _on_desktop(device, entries)


def _window_ids_sent(device) -> list[str]:
    return [
        str(call["payload"]["window_id"])
        for call in device.calls
        if "window_id" in call["payload"]
    ]


# ------------------------------------------------------- the owner's exact failure


def test_typing_into_a_window_named_by_title_types_into_it() -> None:
    """The incident, end to end: Notepad is open and focused, the model names it by
    title, and the owner's text reaches the keyboard."""
    client, factory, device, _operator = _wired()
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")], device)
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Not Defteri"})

    assert call["status"] == "succeeded", call
    assert "keyboard.type" in device.capabilities_called(), device.capabilities_called()
    assert call["result"]["speech"] == tools_operator.SPEECH_TYPE_SUCCESS


def test_no_window_name_is_ever_sent_to_the_device_as_an_id() -> None:
    """The guard that would have caught it on its own: whatever the model says, every
    ``window_id`` that leaves the server has the device's shape."""
    client, factory, device, _operator = _wired()
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")], device)
    sid = _create(client)
    _say(client, sid, NEUTRAL)
    _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Not Defteri"})
    _tool(client, sid, "operator.window_control", {"action": "close", "window": "Not Defteri"})

    sent = _window_ids_sent(device)
    assert sent, "the tools never reached the device at all"
    assert [w for w in sent if not device_window_id_ok(w)] == []


def test_window_control_named_by_title_acts_on_that_window() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "Hesap Makinesi"), (NOTEPAD, "Adsız - Not Defteri")], device)
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(
        client, sid, "operator.window_control", {"action": "close", "window": "Hesap Makinesi"}
    )

    assert call["status"] == "succeeded", call
    assert device.payload_for("window.close") == {"window_id": CALCULATOR}


# ------------------------------------------------------------------ ask, never guess


def test_an_unknown_window_name_asks_and_names_what_is_open() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")], device)
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Excel"})

    assert call["status"] == "needs_clarification", call
    body = call["result"]
    assert "Excel" in body["speech"]
    assert "Adsız - Not Defteri" in body["speech"]
    # A read is allowed (the resolver asks the device what is open); ACTING is not.
    assert device.capabilities_called() == ["window.list"], device.capabilities_called()


def test_a_name_matching_two_differently_titled_windows_asks_which() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "not.txt - Not Defteri"), (NOTEPAD, "Notlarım")], device)
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "not"})

    assert call["status"] == "needs_clarification", call
    body = call["result"]
    assert "not.txt - Not Defteri" in body["speech"] and "Notlarım" in body["speech"]
    assert [c for c in device.capabilities_called() if c != "window.list"] == [], (
        "asked the owner AND still acted on a window"
    )


def test_two_windows_sharing_one_title_take_the_most_recent() -> None:
    """Two untitled Notepads look identical to the owner too; asking "which?" would be a
    question they cannot answer. Recency is the owner's own last interaction."""
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "Adsız - Not Defteri"), (NOTEPAD, "Adsız - Not Defteri")], device)
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Not Defteri"})

    assert call["status"] == "succeeded", call
    assert device.payload_for("window.activate") == {"window_id": NOTEPAD}


def test_a_name_is_matched_case_insensitively_in_turkish() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")], device)
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "NOT DEFTERİ"})

    assert call["status"] == "succeeded", call
    assert device.payload_for("window.activate") == {"window_id": NOTEPAD}


# ----------------------------------------------------------- what still passes through


def test_a_real_window_id_is_still_used_verbatim() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "Hesap Makinesi")], device)
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": NOTEPAD})

    assert call["status"] == "succeeded", call
    assert device.payload_for("window.activate") == {"window_id": NOTEPAD}


def test_no_target_still_means_the_focused_window() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "Hesap Makinesi"), (NOTEPAD, "Adsız - Not Defteri")], device)
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba"})

    assert call["status"] == "succeeded", call
    assert device.payload_for("window.activate") == {"window_id": NOTEPAD}


# ------------------------------------------------------ the sentence the owner hears


def test_a_run_that_never_reached_the_keyboard_does_not_blame_verification() -> None:
    """The owner reported "metni doğrulayamıyormuş" for a run in which no key was ever
    pressed. A failure before ``keyboard.type`` must say which step stopped it."""
    client, factory, device, _operator = _wired()
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")], device)
    device.results["window.activate"] = _refusal("ui_target_not_found", "window is gone")
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba"})

    speech = call["result"]["speech"]
    assert speech == tools_operator.SPEECH_TYPE_NOT_ATTEMPTED
    assert "doğrulayamadım" not in speech
    assert "keyboard.type" not in device.capabilities_called()


def test_a_run_that_typed_but_could_not_read_it_back_still_says_so() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")], device)
    device.results["ui.inspect"] = _ok(root={"value": "something else"})
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba"})

    assert "keyboard.type" in device.capabilities_called()
    assert call["result"]["speech"] == tools_operator.SPEECH_TYPE_FAILURE


# ------------------------------------------------------------ the two halves agree


def test_the_id_shape_still_matches_the_device_source() -> None:
    """The server's ``_is_window_id`` and the test fake both restate
    ``WindowRegistry.TryParseHandle``. Read the C# and check the restatements still hold —
    neither half may drift without this failing."""
    source = _WINDOW_REGISTRY_CS.read_text(encoding="utf-8")
    body = source[source.index("public static bool TryParseHandle") :][:900]

    # The rules this test's cases are derived from, asserted to still BE the rules.
    assert 'windowId.Split(' in body and "'-'" in body
    assert "parts.Length != 3" in body and 'parts[0] != "w"' in body
    assert "long.TryParse(parts[1], NumberStyles.None" in body and "raw <= 0" in body
    assert "ulong.TryParse(parts[2], NumberStyles.None" in body

    accepted = ["w-1-0", "w-16058812-365824437", f"w-{2**63 - 1}-{2**64 - 1}"]
    refused = [
        "Not Defteri",  # the incident
        "w-1",  # two parts
        "w-0-1",  # handle must be positive
        "w--1-1",  # NumberStyles.None: no sign
        "w-1-2-3",  # four parts
        "W-1-1",  # the literal is lowercase
        "w- 1-1",  # NumberStyles.None: no whitespace
        "w-1.0-1",
        "w-1-",
        "",
        f"w-{2**63}-1",  # past long.MaxValue
    ]
    for value in accepted:
        assert tools_operator._is_window_id(value), f"server refused {value!r}"
        assert device_window_id_ok(value), f"fake refused {value!r}"
    for value in refused:
        assert not tools_operator._is_window_id(value), f"server accepted {value!r}"
        assert not device_window_id_ok(value), f"fake accepted {value!r}"


def test_the_tool_schemas_do_not_advertise_a_free_window_id() -> None:
    """The schema is half of what the model reads. ``window``/``target`` used to be bare
    strings beside a description promising the server resolved the window itself; the
    model resolved the contradiction by filling them with a title."""
    from app.voice.realtime_sessions.tools import ToolRegistry

    manifest = {
        entry["name"]: entry
        for entry in tools_operator.register_operator_tools(ToolRegistry()).manifest()
    }
    for name, field in (
        (tools_operator.TOOL_WINDOW_CONTROL, "window"),
        (tools_operator.TOOL_TYPE, "target"),
    ):
        parameters = manifest[name]["parameters"]
        described = parameters["properties"][field].get("description", "")
        assert "w-<handle>-<tick>" in described, f"{name}.{field} does not say what it takes"
        assert re.search(r"\bNAME\b", described), f"{name}.{field} does not offer a name"
        assert field not in parameters.get("required", [])
        # And the prose the model reads must not still leave a guess open.
        assert "UYDURMA" in manifest[name]["description"], f"{name} does not forbid a guess"


# --------------------------------------------------------------------------- helpers


def _ok(**result):
    from app.routines.dispatch import DeviceRunResult

    return DeviceRunResult(True, result=result)


def _refusal(error_class: str, message: str):
    from app.routines.dispatch import DeviceRunResult

    return DeviceRunResult(False, error_class, message)


# --------------------------------------------- verifying text where the device put it


#: Notepad's REAL ui.inspect answer, captured from the owner's device on 2026-09-09 after
#: a successful keyboard.type: the window root carries NO value, and the text is one node
#: down in the edit control. Every typing run into Notepad failed its verification against
#: this shape while the text sat on the owner's screen.
def _notepad_tree(text: str) -> dict:
    return {
        "root": {
            "name": "*Adsız - Not Defteri",
            "bounds": {"x": 100, "y": 100, "width": 800, "height": 600},
            "enabled": True,
            "children": [
                {
                    "name": "Metin Düzenleyici",
                    "value": text,
                    "control_type": "Edit",
                    "enabled": True,
                    "children": [{"name": "Yatay Kaydırma Çubuğu", "enabled": True}],
                }
            ],
        }
    }


def test_typing_into_notepad_verifies_against_the_control_that_holds_the_text() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")], device)
    device.results["keyboard.type"] = _ok(typed_chars=20, window_id=NOTEPAD)
    device.results["ui.inspect"] = _ok(**_notepad_tree("merhaba"))
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba"})

    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed", body
    assert body["terminal_status"] == "verified", body
    assert body["speech"] == tools_operator.SPEECH_TYPE_SUCCESS


def test_text_absent_from_the_whole_tree_is_still_a_failure() -> None:
    """The postcondition moved; it did not soften. Nothing in the device's tree carrying
    the text is still 'I could not verify it'."""
    client, factory, device, _operator = _wired()
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")], device)
    device.results["keyboard.type"] = _ok(typed_chars=20, window_id=NOTEPAD)
    device.results["ui.inspect"] = _ok(**_notepad_tree("bambaska bir sey"))
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba"})

    assert call["result"]["execution_status"] != "executed"
    assert call["result"]["speech"] == tools_operator.SPEECH_TYPE_FAILURE


def test_a_malformed_tree_does_not_crash_the_verification() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")], device)
    device.results["keyboard.type"] = _ok(typed_chars=20, window_id=NOTEPAD)
    device.results["ui.inspect"] = _ok(root={"name": "x", "children": None, "value": None})
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba"})

    assert call["status"] == "succeeded", call  # a refusal, not an exception
    assert call["result"]["speech"] == tools_operator.SPEECH_TYPE_FAILURE


# ------------------------------------- a window the OWNER opened, not this operator


#: What the device answers for ``window.list``. The focus stack cannot know about these:
#: it is written only when an operator STEP observes a foreground window, so a Notepad the
#: owner opened by hand is invisible to it. The desktop is not.
def _open_on_the_desktop(*windows: tuple[str, str]):
    return _ok(windows=[{"window_id": wid, "title": title} for wid, title in windows])


def test_a_window_the_owner_opened_by_hand_is_found_by_asking_the_device() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "Hesap Makinesi")], device)  # the operator never saw Notepad
    device.results["window.list"] = _open_on_the_desktop(
        (CALCULATOR, "Hesap Makinesi"), (NOTEPAD, "Adsız - Not Defteri")
    )
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Not Defteri"})

    assert call["status"] == "succeeded", call
    assert device.payload_for("window.activate") == {"window_id": NOTEPAD}


def test_a_remembered_window_that_was_closed_is_never_offered() -> None:
    """Measured on the real device: the focus stack held two Notepads from earlier runs,
    both long closed, and the owner was asked which of the two they meant. Memory says
    what WAS; only the device says what IS."""
    client, factory, device, _operator = _wired()
    _remember(factory, [(CALCULATOR, "Adsız - Not Defteri")])  # closed since
    _on_desktop(device, [(NOTEPAD, "Adsız - Not Defteri")])  # the one really open
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Not Defteri"})

    assert call["status"] == "succeeded", call
    assert device.payload_for("window.activate") == {"window_id": NOTEPAD}


def test_notepads_unsaved_marker_does_not_make_a_second_window() -> None:
    """The real question the owner was asked, which they could not answer:
    "Hangisi efendim: Adsız - Not Defteri, *Adsız - Not Defteri?" — one window, one
    keystroke apart."""
    client, factory, device, _operator = _wired()
    _remember(factory, [(CALCULATOR, "Adsız - Not Defteri"), (NOTEPAD, "Adsız - Not Defteri")])
    _on_desktop(device, [(CALCULATOR, "Adsız - Not Defteri"), (NOTEPAD, "*Adsız - Not Defteri")])
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Not Defteri"})

    assert call["status"] == "succeeded", call["result"].get("speech")
    assert device.payload_for("window.activate") == {"window_id": NOTEPAD}  # most recent


def test_a_name_is_resolved_against_windows_that_exist() -> None:
    """One device call per named resolution, and it is the source of the candidates."""
    client, factory, device, _operator = _wired()
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")], device)
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Not Defteri"})

    assert device.count("window.list") == 1, device.capabilities_called()


def test_the_device_is_asked_exactly_once_per_resolution() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "Hesap Makinesi")], device)
    device.results["window.list"] = _open_on_the_desktop((CALCULATOR, "Hesap Makinesi"))
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Excel"})

    assert call["status"] == "needs_clarification", call
    assert device.count("window.list") == 1, device.capabilities_called()


def test_the_question_names_what_the_DEVICE_says_is_open() -> None:
    """Not what this operator happens to remember — the owner is looking at the desktop."""
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "Hesap Makinesi")], device)
    device.results["window.list"] = _open_on_the_desktop((NOTEPAD, "Word - Rapor.docx"))
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Excel"})

    speech = call["result"]["speech"]
    assert "Word - Rapor.docx" in speech, speech
    assert "Hesap Makinesi" not in speech, speech


def test_a_device_that_cannot_answer_still_asks_rather_than_guessing() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "Hesap Makinesi")], device)
    device.results["window.list"] = _refusal("device_unreachable", "no")
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Not Defteri"})

    assert call["status"] == "needs_clarification", call
    assert "window.activate" not in device.capabilities_called()
    assert "keyboard.type" not in device.capabilities_called()
