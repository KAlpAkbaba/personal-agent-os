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


def _focus(factory, entries: list[tuple[str, str]]) -> None:
    """Write a focus stack, oldest first, on explicit staggered instants."""
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
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")])
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
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")])
    sid = _create(client)
    _say(client, sid, NEUTRAL)
    _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Not Defteri"})
    _tool(client, sid, "operator.window_control", {"action": "close", "window": "Not Defteri"})

    sent = _window_ids_sent(device)
    assert sent, "the tools never reached the device at all"
    assert [w for w in sent if not device_window_id_ok(w)] == []


def test_window_control_named_by_title_acts_on_that_window() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "Hesap Makinesi"), (NOTEPAD, "Adsız - Not Defteri")])
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
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")])
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Excel"})

    assert call["status"] == "needs_clarification", call
    body = call["result"]
    assert "Excel" in body["speech"]
    assert "Adsız - Not Defteri" in body["speech"]
    assert device.calls == [], "asked the owner AND still poked the device"


def test_a_name_matching_two_differently_titled_windows_asks_which() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "not.txt - Not Defteri"), (NOTEPAD, "Notlarım")])
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "not"})

    assert call["status"] == "needs_clarification", call
    body = call["result"]
    assert "not.txt - Not Defteri" in body["speech"] and "Notlarım" in body["speech"]
    assert device.calls == []


def test_two_windows_sharing_one_title_take_the_most_recent() -> None:
    """Two untitled Notepads look identical to the owner too; asking "which?" would be a
    question they cannot answer. Recency is the owner's own last interaction."""
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "Adsız - Not Defteri"), (NOTEPAD, "Adsız - Not Defteri")])
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "Not Defteri"})

    assert call["status"] == "succeeded", call
    assert device.payload_for("window.activate") == {"window_id": NOTEPAD}


def test_a_name_is_matched_case_insensitively_in_turkish() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")])
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": "NOT DEFTERİ"})

    assert call["status"] == "succeeded", call
    assert device.payload_for("window.activate") == {"window_id": NOTEPAD}


# ----------------------------------------------------------- what still passes through


def test_a_real_window_id_is_still_used_verbatim() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "Hesap Makinesi")])
    sid = _create(client)
    _say(client, sid, NEUTRAL)

    call = _tool(client, sid, "operator.type", {"content": "merhaba", "target": NOTEPAD})

    assert call["status"] == "succeeded", call
    assert device.payload_for("window.activate") == {"window_id": NOTEPAD}


def test_no_target_still_means_the_focused_window() -> None:
    client, factory, device, _operator = _wired()
    _focus(factory, [(CALCULATOR, "Hesap Makinesi"), (NOTEPAD, "Adsız - Not Defteri")])
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
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")])
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
    _focus(factory, [(NOTEPAD, "Adsız - Not Defteri")])
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
