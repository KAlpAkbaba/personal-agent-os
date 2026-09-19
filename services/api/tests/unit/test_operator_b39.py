"""B39 - the operator's autonomy loop (req 106, 112-115, 123-130).

Measured before: every operator plan was a fixed list of steps run once inside a tool
call; a failed postcondition ended the task with no second look; nothing climbed the
ladder; a sentence with two parts ("Chrome'u aç ve YouTube'a gir") reached nothing;
Settings, Explorer folders, Office and an editor had no plan; nothing outlived the tool
call, nothing paused, and the owner never saw a plan before it ran.

Every seam is proven by executing it: the loop over a scripted device (retry, re-observe
and re-plan, the visual rung with a scripted vision provider, the owner escalation, the
cancel), the planner on the owner's sentences, the plans through ``run_task`` (the
observed window id carried into later steps), the row and its lifecycle on the real
backend, the workflow in the real Temporal test environment, the routes and the voice
tool through the real application object.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.ledger.models import ActivityEventRow
from app.operator import adapters, mission, mission_service, plans
from app.operator.mission import (
    KIND_APP_OPEN,
    KIND_EXPLORER_OPEN,
    KIND_IDE_OPEN_FILE,
    KIND_NAVIGATE,
    KIND_OFFICE_TYPE,
    KIND_SETTINGS_OPEN,
    KIND_TYPE_TEXT,
    KIND_UI_INVOKE,
    MAX_REPLANS_PER_STEP,
    MAX_RETRIES_PER_STEP,
    MISSION_AWAITING_APPROVAL,
    MISSION_CANCELLED,
    MISSION_PAUSED,
    MISSION_RUNNING,
    MISSION_SUCCEEDED,
    STEP_DONE,
    STRATEGY_BY_ERROR_CLASS,
    STRATEGY_DEFAULT,
    STRATEGY_OWNER,
    STRATEGY_REOBSERVE,
    STRATEGY_RETRY,
    Mission,
    MissionClarificationNeeded,
    MissionPorts,
    MissionStep,
    plan_mission,
    run_mission,
)
from app.operator.mission_models import OperatorMissionRow
from app.operator.mission_workflow import MissionRequest, OperatorMissionWorkflow
from app.operator.task import LEVEL_KEYBOARD, LEVEL_VISUAL, OperatorStep, new_task, run_task
from app.operator.vision import FakeVisionProvider, VisionError, parse_location
from app.routines.dispatch import DeviceRunResult
from app.voice.intents import Intent, resolve_intent
from tests.alarms_support import ONE_PIXEL_PNG_B64, FakeDeviceAction, ok, window_id
from tests.voice_corpus.harness import build_harness

TASK_QUEUE = "b39-missions"


def _window(n: int, image: str, title: str, *, foreground: bool = True) -> dict[str, Any]:
    return {
        "window_id": window_id(n),
        "pid": 1000 + n,
        "image": image,
        "title": title,
        "state": "normal",
        "foreground": foreground,
    }


def _observed(payload: dict[str, Any], window: dict[str, Any]) -> dict[str, Any]:
    return {"window": {**window, "window_id": str(payload.get("window_id") or window["window_id"])}}


CHROME = _window(2, "chrome.exe", "Yeni Sekme - Google Chrome")
NOTEPAD = _window(1, "notepad.exe", "Adsız - Not Defteri")


def _chrome_device(
    *, already_open: bool = False, navigate_url: str = "https://www.youtube.com/"
) -> FakeDeviceAction:
    """A desktop where Chrome opens (or is open) and the browser family answers. The
    foreground follows the launch, as a real desktop's does."""
    windows = [CHROME] if already_open else []
    state = {"launched": already_open}

    def launch(payload: dict[str, Any]) -> DeviceRunResult:
        state["launched"] = True
        return ok(
            pid=1002,
            window_id=window_id(2),
            executable="C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        )

    def current(payload: dict[str, Any]) -> DeviceRunResult:
        return ok(window=dict(CHROME if state["launched"] else NOTEPAD))

    return FakeDeviceAction(
        results={
            "window.current": current,
            "window.list": ok(windows=[dict(w) for w in windows]),
            "app.launch": launch,
            "window.activate": lambda p: ok(
                window={**CHROME, "window_id": str(p.get("window_id"))}
            ),
            "browser.session_open": ok(session_id="s", created=True, profile="owner"),
            "browser.navigate": ok(url=navigate_url, title="YouTube"),
            "browser.inspect": ok(
                url=navigate_url, title="YouTube", tab_index=0, tab_count=1, page_kind="site"
            ),
        }
    )


# ------------------------------------------------------------------ the planner (127)


def test_the_planner_turns_a_compound_sentence_into_steps_and_keeps_its_preview_word() -> None:
    m = plan_mission("Chrome'u aç ve YouTube'a gir")
    assert [s.kind for s in m.steps] == [KIND_APP_OPEN, KIND_NAVIGATE]
    assert m.steps[0].args == {"application": "chrome"}
    assert m.steps[1].args == {"url": "https://www.youtube.com/"}
    assert m.steps[0].id == "m1" and m.steps[1].id == "m2" and not m.preview
    shown = plan_mission("Not Defteri'ni aç, sonra merhaba yaz; önce göster")
    assert [s.kind for s in shown.steps] == [KIND_APP_OPEN, KIND_TYPE_TEXT] and shown.preview
    assert shown.steps[1].args["text"] == "merhaba"
    assert "Planım şu" in shown.plan_speech() and "Başlayayım mı" in shown.plan_speech()


@pytest.mark.parametrize(
    ("text", "kind", "args"),
    [
        ("Ayarlarda Bluetooth'u aç", KIND_SETTINGS_OPEN, {"page": "Bluetooth"}),
        ("Ayarları aç", KIND_SETTINGS_OPEN, {"page": ""}),
        (
            "Dosya Gezgini'nde İndirilenler klasörünü aç",
            KIND_EXPLORER_OPEN,
            {"folder": "indirilenler"},
        ),
        ("VS Code'da main.py dosyasını aç", KIND_IDE_OPEN_FILE, {"file": "main.py"}),
        (
            "Word'e merhaba dünya yaz",
            KIND_OFFICE_TYPE,
            {"application": "word", "text": "merhaba dünya"},
        ),
        ("Tamam düğmesine tıkla", KIND_UI_INVOKE, {"name": "Tamam"}),
    ],
)
def test_the_planner_knows_the_new_families(text: str, kind: str, args: dict[str, Any]) -> None:
    m = plan_mission(text)
    assert len(m.steps) == 1 and m.steps[0].kind == kind, m.steps
    assert m.steps[0].args == args


def test_a_part_the_planner_cannot_serve_is_a_clarification_never_a_guess() -> None:
    with pytest.raises(MissionClarificationNeeded, match="kısmını nasıl yapacağımı bilmiyorum"):
        plan_mission("Chrome'u aç ve kahve yap")
    with pytest.raises(MissionClarificationNeeded):
        plan_mission("   ")
    with pytest.raises(MissionClarificationNeeded, match="en çok"):
        plan_mission(" ve ".join(["Chrome'u aç"] * 7))


# --------------------------------------------------------- the failure taxonomy (114)


def test_the_taxonomy_is_declared_and_an_unknown_class_is_the_owners() -> None:
    assert STRATEGY_BY_ERROR_CLASS["timeout"] == STRATEGY_RETRY
    assert STRATEGY_BY_ERROR_CLASS["postcondition_failed"] == STRATEGY_REOBSERVE
    assert STRATEGY_BY_ERROR_CLASS["permission_denied"] == STRATEGY_OWNER
    assert STRATEGY_BY_ERROR_CLASS["cancelled"] == "stop"
    assert STRATEGY_DEFAULT == STRATEGY_OWNER
    assert "something_new" not in STRATEGY_BY_ERROR_CLASS


# ------------------------------------------------------------- the loop (112, 113, 127)


def test_a_mixed_mission_runs_the_desktop_half_and_the_browser_half_over_one_port() -> None:
    device = _chrome_device()
    m = plan_mission("Chrome'u aç ve YouTube'a gir")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert [s.status for s in m.steps] == [STEP_DONE, STEP_DONE]
    assert [s.level for s in m.steps] == ["api", "dom"]
    called = device.capabilities_called()
    # OBSERVE first (two reads), then the launch, then the browser family - one port.
    assert called[:2] == ["window.current", "window.list"]
    assert (
        "app.launch" in called and "browser.session_open" in called and "browser.navigate" in called
    )
    assert called.index("browser.inspect") > called.index("browser.navigate")
    assert [t["outcome"] for t in m.trail] == ["verified", "verified"]
    assert m.steps[1].message == "sahibin kendi tarayıcısında"


def test_the_plan_changes_with_what_is_seen_an_open_application_is_activated_not_relaunched() -> (
    None
):
    device = _chrome_device(already_open=True)
    m = plan_mission("Chrome'u aç ve YouTube'a gir")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert "app.launch" not in device.capabilities_called()
    assert m.trail[0]["decided"] == "activate_window"
    assert "zaten açık" in m.steps[0].message


def test_a_transient_device_fault_is_retried_and_counted() -> None:
    attempts = {"n": 0}

    def flaky_launch(payload: dict[str, Any]) -> DeviceRunResult:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return DeviceRunResult(False, "timeout", "the device did not answer")
        return ok(pid=1002, window_id=window_id(2), executable="C:\\x\\chrome.exe")

    device = _chrome_device()
    device.results["app.launch"] = flaky_launch
    device.results["window.current"] = lambda p: ok(
        window=dict(CHROME if attempts["n"] >= 3 else NOTEPAD)
    )
    m = plan_mission("Chrome'u aç")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    # The plan's own retry (1) plus the loop's retry strategy carried it; bounded.
    assert m.steps[0].retries <= MAX_RETRIES_PER_STEP and m.steps[0].rounds >= 2
    assert any(t.get("strategy") == STRATEGY_RETRY for t in m.trail)


def test_a_failed_verification_looks_again_and_the_second_look_can_succeed() -> None:
    looks = {"n": 0}

    def foreground_later(payload: dict[str, Any]) -> DeviceRunResult:
        looks["n"] += 1
        # The first observations after the launch see Notepad; the desktop settles later.
        return ok(window=dict(CHROME if looks["n"] > 4 else NOTEPAD))

    device = _chrome_device()
    device.results["window.current"] = foreground_later
    m = plan_mission("Chrome'u aç")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert m.steps[0].replans >= 1 and m.steps[0].replans <= MAX_REPLANS_PER_STEP
    assert any(t.get("strategy") == STRATEGY_REOBSERVE for t in m.trail)


# --------------------------------------------------- the visual rung and the owner (106, 115)


def _stubborn_button_device(*, gone_after_click: bool = True) -> FakeDeviceAction:
    """A dialog whose Tamam button the tree can name but whose invoke does nothing
    observable; a click where the picture says it is makes it go away."""
    clicked = {"done": False}
    root_with = {"name": "Kaydet?", "children": [{"name": "Tamam", "control_type": "Button"}]}
    root_without = {"name": "Kaydet?", "children": []}

    def invoke(payload: dict[str, Any]) -> DeviceRunResult:
        element = {"automation_id": "", "name": "Tamam", "control_type": "Button", "enabled": True}
        return ok(
            invoked=True,
            element=element,
            observed={"element": dict(element), "element_present": True, "window": dict(NOTEPAD)},
        )

    def click(payload: dict[str, Any]) -> DeviceRunResult:
        clicked["done"] = True
        x, y = int(payload["x"]), int(payload["y"])
        return ok(
            x=x,
            y=y,
            space="screen",
            screen_x=x,
            screen_y=y,
            observed={"cursor": {"x": x, "y": y}, "window": dict(NOTEPAD)},
        )

    def inspect(payload: dict[str, Any]) -> DeviceRunResult:
        return ok(root=dict(root_without if (clicked["done"] and gone_after_click) else root_with))

    return FakeDeviceAction(
        results={
            "window.current": ok(window=dict(NOTEPAD)),
            "window.list": ok(windows=[dict(NOTEPAD)]),
            "window.activate": lambda p: ok(
                window={**NOTEPAD, "window_id": str(p.get("window_id"))}
            ),
            "ui.invoke": invoke,
            "ui.inspect": inspect,
            "screen.capture": ok(width=200, height=100, png_base64=ONE_PIXEL_PNG_B64, scale=1),
            "pointer.click": click,
        }
    )


def test_the_loop_climbs_to_the_visual_rung_and_verifies_by_the_tree() -> None:
    device = _stubborn_button_device()
    vision = FakeVisionProvider(location=(40, 20))
    m = plan_mission("Tamam düğmesine tıkla")
    run_mission(m, MissionPorts(device=device, vision=vision))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    step = m.steps[0]
    assert step.level == LEVEL_VISUAL and step.status == STEP_DONE
    assert vision.targets == ["Tamam"]
    click = device.payload_for("pointer.click")
    assert click == {"window_id": window_id(1), "x": 40, "y": 20, "space": "screen"}
    strategies = [t.get("strategy") for t in m.trail if t.get("strategy")]
    assert strategies[:2] == [STRATEGY_REOBSERVE, STRATEGY_REOBSERVE]
    assert m.trail[-1]["outcome"] == "verified" and m.trail[-1]["level"] == LEVEL_VISUAL
    # The rung is bounded: replans first, the picture once, never a coordinate first.
    assert device.capabilities_called().index("ui.invoke") < device.capabilities_called().index(
        "screen.capture"
    )


def test_without_a_vision_provider_the_loop_stops_for_the_owner_and_says_why() -> None:
    m = plan_mission("Tamam düğmesine tıkla")
    run_mission(m, MissionPorts(device=_stubborn_button_device(), vision=None))
    assert m.status == MISSION_PAUSED and m.escalation is not None
    assert m.escalation["error_class"] == "vision_unavailable"
    assert "görsel sağlayıcı tanımlı değil" in m.escalation["speech"]
    assert m.steps[0].status == "failed"


def test_a_permission_refusal_is_the_owners_at_once_never_retried() -> None:
    device = _chrome_device()
    device.results["app.launch"] = DeviceRunResult(False, "permission_denied", "not allowed")
    m = plan_mission("Chrome'u aç")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_PAUSED and m.escalation["error_class"] == "permission_denied"
    assert "izin verilmemiş" in m.escalation["speech"]
    # The loop asked once: no retry, no re-observe, no rung - straight to the owner.
    assert m.steps[0].rounds == 1 and m.steps[0].retries == 0 and m.steps[0].replans == 0
    # Resume gives the step a fresh set of rounds (the owner changed something).
    device.results["app.launch"] = ok(
        pid=1002, window_id=window_id(2), executable="C:\\x\\chrome.exe"
    )
    device.results["window.current"] = ok(window=dict(CHROME))
    mission.resume(m)
    assert m.status == MISSION_RUNNING and m.steps[0].rounds == 0
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED


def test_a_cancel_between_rounds_ends_the_mission_and_a_preview_runs_nothing_until_approved() -> (
    None
):
    device = _chrome_device()
    m = plan_mission("Chrome'u aç ve YouTube'a gir")
    m.cancel_requested = True
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_CANCELLED and device.capabilities_called() == []

    shown = plan_mission("Chrome'u aç ve YouTube'a gir; önce göster")
    device = _chrome_device()
    run_mission(shown, MissionPorts(device=device))
    assert shown.status == MISSION_AWAITING_APPROVAL and device.capabilities_called() == []
    mission.resume(shown)
    run_mission(shown, MissionPorts(device=device))
    assert shown.status == MISSION_SUCCEEDED


def test_a_pause_lands_between_steps_and_a_resume_continues_from_there() -> None:
    device = _chrome_device()
    m = plan_mission("Chrome'u aç ve YouTube'a gir")
    m.pause_requested = True
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_PAUSED and m.current_step == 0 and device.capabilities_called() == []
    mission.resume(m)
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED and [s.status for s in m.steps] == [STEP_DONE, STEP_DONE]


# ---------------------------------------------------------- the plans (123-126, 106)


def test_a_later_step_carries_the_window_id_an_earlier_step_observed() -> None:
    settings = _window(5, "applicationframehost.exe", "Ayarlar")
    device = FakeDeviceAction(
        results={
            # The launched pid is SystemSettings.exe's; the window belongs to
            # ApplicationFrameHost - a different pid, a different image, the same title.
            "app.launch": ok(
                pid=7777, executable="C:\\Windows\\ImmersiveControlPanel\\SystemSettings.exe"
            ),
            "window.current": ok(window=dict(settings)),
            "ui.set_value": lambda p: ok(
                observed_value=p.get("value"), window_id=p.get("window_id")
            ),
            "keyboard.key": lambda p: ok(key=p.get("key"), window_id=p.get("window_id")),
            "ui.inspect": ok(
                root={"name": "Ayarlar", "children": [{"name": "Bluetooth ve cihazlar"}]}
            ),
        }
    )
    task = new_task(goal="t", plan_name="open_settings", steps=plans.open_settings("Bluetooth"))
    run_task(task, device)
    assert task.status == "succeeded", task.as_dict()
    assert device.payload_for("ui.set_value")["window_id"] == window_id(5)
    assert device.payload_for("keyboard.key") == {"key": "enter", "window_id": window_id(5)}
    # The receipt records the payload that was actually sent, id included.
    sent = [r for r in task.receipts if r.capability == "ui.set_value"][0]
    assert sent.payload["window_id"] == window_id(5)


def test_the_settings_window_is_known_by_its_title_when_launched_through_app_open() -> None:
    settings = _window(5, "applicationframehost.exe", "Ayarlar")
    device = FakeDeviceAction(
        results={
            # The launched pid is SystemSettings.exe's; the window belongs to
            # ApplicationFrameHost - a different pid, a different image, the same title.
            "app.launch": ok(
                pid=7777, executable="C:\\Windows\\ImmersiveControlPanel\\SystemSettings.exe"
            ),
            "window.current": ok(window=dict(settings)),
        }
    )
    task = new_task(
        goal="t", plan_name="open_application", steps=plans.open_application("settings")
    )
    run_task(task, device)
    assert task.status == "succeeded", task.as_dict()


def test_explorer_opens_a_folder_by_the_address_bar_and_reads_the_title_back() -> None:
    explorer_home = _window(6, "explorer.exe", "Giriş")
    downloads = _window(6, "explorer.exe", "İndirilenler")
    seen = {"n": 0}

    def current(payload: dict[str, Any]) -> DeviceRunResult:
        seen["n"] += 1
        return ok(window=dict(downloads if seen["n"] > 1 else explorer_home))

    device = FakeDeviceAction(
        results={
            "app.launch": ok(pid=1006, executable="C:\\Windows\\explorer.exe"),
            "window.current": current,
            "keyboard.shortcut": lambda p: ok(keys=p.get("keys"), window_id=p.get("window_id")),
            "keyboard.type": lambda p: ok(
                typed_chars=len(str(p.get("text"))), window_id=p.get("window_id")
            ),
            "keyboard.key": lambda p: ok(key=p.get("key"), window_id=p.get("window_id")),
        }
    )
    m = plan_mission("Dosya Gezgini'nde İndirilenler klasörünü aç")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert device.payload_for("keyboard.type") == {
        "text": "shell:Downloads",
        "secret": False,
        "window_id": window_id(6),
    }
    assert device.payload_for("keyboard.shortcut")["keys"] == ["ctrl", "l"]
    assert m.steps[0].level == LEVEL_KEYBOARD


def test_the_editor_opens_a_file_through_its_documented_chord_and_the_title_proves_it() -> None:
    code = _window(7, "code.exe", "proje - Visual Studio Code")
    opened = _window(7, "code.exe", "main.py - proje - Visual Studio Code")
    looks = {"n": 0}

    def current(payload: dict[str, Any]) -> DeviceRunResult:
        looks["n"] += 1
        return ok(window=dict(opened if looks["n"] > 1 else code))

    device = FakeDeviceAction(
        results={
            "window.current": current,
            "window.list": ok(windows=[dict(code)]),
            "window.activate": lambda p: ok(window={**code, "window_id": str(p.get("window_id"))}),
            "keyboard.shortcut": lambda p: ok(keys=p.get("keys"), window_id=p.get("window_id")),
            "keyboard.type": lambda p: ok(
                typed_chars=len(str(p.get("text"))), window_id=p.get("window_id")
            ),
            "keyboard.key": lambda p: ok(key=p.get("key"), window_id=p.get("window_id")),
        }
    )
    m = plan_mission("VS Code'da main.py dosyasını aç")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert device.payload_for("keyboard.shortcut")["keys"] == list(
        adapters.VSCODE.shortcuts["quick_open"]
    )
    assert device.payload_for("keyboard.type")["text"] == "main.py"
    assert adapters.adapter_for("C:\\Users\\x\\code.exe") is adapters.VSCODE


def test_word_is_typed_into_and_read_back_from_its_document_control() -> None:
    word = _window(8, "winword.exe", "Belge1 - Word")
    device = FakeDeviceAction(
        results={
            "window.current": ok(window=dict(word)),
            "window.list": ok(windows=[dict(word)]),
            "window.activate": lambda p: ok(window={**word, "window_id": str(p.get("window_id"))}),
            "keyboard.type": lambda p: ok(
                typed_chars=len(str(p.get("text"))), observed={"window": {**word}}
            ),
            "ui.inspect": lambda p: ok(
                root={
                    "name": "Belge1",
                    "control_type": "Document",
                    "value": "Sayın yetkili, merhaba dünya",
                }
            ),
        }
    )
    m = plan_mission("Word'e merhaba dünya yaz")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert device.payload_for("ui.inspect")["query"] == adapters.WORD.document_query
    assert (
        adapters.adapter_for("excel.exe") is adapters.EXCEL
        and adapters.EXCEL.document_query is None
    )
    # No Word on the desktop: the owner's, by name.
    m2 = plan_mission("Word'e selam yaz")
    run_mission(
        m2,
        MissionPorts(
            device=FakeDeviceAction(
                results={"window.current": ok(window=dict(NOTEPAD)), "window.list": ok(windows=[])}
            )
        ),
    )
    assert m2.status == MISSION_PAUSED and "Word penceresi göremedim" in m2.escalation["speech"]


def _owners_chrome_desktop(
    *, lands_on: str = "YouTube - Google Chrome", attach: str = "dependency_unavailable"
) -> FakeDeviceAction:
    """The owner's everyday Chrome: open, in front, not started with a debugging endpoint
    (so it cannot be attached), and driven by the keyboard. The title changes to
    ``lands_on`` only after Enter - and only on the SECOND look after it, as a real page
    load does a moment later."""
    state = {"entered": False, "looks": 0}

    def current(payload: dict[str, Any]) -> DeviceRunResult:
        if state["entered"]:
            state["looks"] += 1
        title = lands_on if state["entered"] and state["looks"] >= 2 else CHROME["title"]
        return ok(window={**CHROME, "title": title})

    def key(payload: dict[str, Any]) -> DeviceRunResult:
        if payload.get("key") == "enter":
            state["entered"] = True
        return ok(
            key=payload.get("key"),
            window_id=payload.get("window_id"),
            observed=_observed(payload, CHROME),
        )

    # Every keyboard answer carries the foreground read back AFTER the input, exactly as the
    # companion's KeyboardType/Key/Shortcut do (observed.window) - the plans refuse an input
    # whose landing they cannot read.
    return FakeDeviceAction(
        results={
            "window.current": current,
            "window.list": ok(windows=[dict(CHROME)]),
            "window.activate": lambda p: ok(
                window={**CHROME, "window_id": str(p.get("window_id"))}
            ),
            "browser.session_open": DeviceRunResult(
                False, attach, "existing_session_connect: CDP endpoint is not reachable"
            ),
            "keyboard.shortcut": lambda p: ok(
                keys=p.get("keys"), window_id=p.get("window_id"), observed=_observed(p, CHROME)
            ),
            "keyboard.type": lambda p: ok(
                typed_chars=len(str(p.get("text"))),
                window_id=p.get("window_id"),
                observed=_observed(p, CHROME),
            ),
            "keyboard.key": key,
        }
    )


def test_the_owners_own_chrome_is_driven_by_the_keyboard_when_it_cannot_be_attached(
    monkeypatch,
) -> None:
    """Owner decision 2026-09-18: "kendi Chrome'umu kullansın, sanki ben klavyeyi ve
    mouse'u kullanıyormuşum gibi". The everyday Chrome has no debugging endpoint, so the
    mission drives its window: Ctrl+L, the address, Enter, the title read back."""
    from app.operator import task as task_module

    waits: list[float] = []
    monkeypatch.setattr(task_module, "_sleep", waits.append)
    device = _owners_chrome_desktop()

    m = plan_mission("Google Chrome’dan direkt YouTube ana sayfasını aç")
    run_mission(m, MissionPorts(device=device))

    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert device.payload_for("keyboard.shortcut")["keys"] == ["ctrl", "l"]
    assert device.payload_for("keyboard.type")["text"] == "https://www.youtube.com/"
    assert device.payload_for("keyboard.key")["key"] == "enter"
    assert m.steps[-1].level == LEVEL_KEYBOARD
    assert waits, "the title was only looked at again after a pause, as a page load needs"
    # Never a separate automation window: the only profile ever asked for is the owner's.
    profiles = [
        c["payload"].get("profile")
        for c in device.calls
        if c["capability"] == "browser.session_open"
    ]
    assert set(profiles) == {"owner"}
    assert "browser.navigate" not in device.capabilities_called()


def test_a_page_that_never_arrives_is_not_reported_as_arrived(monkeypatch) -> None:
    """Enter was pressed; the title never named the site. That is a failed step the owner
    hears about - never "YouTube açıldı" on the strength of the keystrokes alone."""
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _owners_chrome_desktop(lands_on="Yeni Sekme - Google Chrome")

    m = plan_mission("Chrome’dan YouTube’u aç")
    run_mission(m, MissionPorts(device=device))

    assert m.status == MISSION_PAUSED
    assert m.steps[-1].error_class == "postcondition_failed"


def test_an_attachable_chrome_is_still_driven_through_its_page() -> None:
    """When the owner's Chrome DOES answer on its debugging endpoint, the DOM rung - the
    highest this browser offers - is used, not the keyboard."""
    device = _chrome_device(already_open=True)
    m = plan_mission("Chrome’dan YouTube’u aç")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert "browser.navigate" in device.capabilities_called()
    assert "keyboard.type" not in device.capabilities_called()


def test_the_page_title_is_read_without_chromes_own_name() -> None:
    assert plans.page_title("YouTube - Google Chrome") == "YouTube"
    assert plans.site_word("https://www.youtube.com/") == "youtube"
    assert plans.site_word("https://tr.wikipedia.org/wiki/X") == "tr"


def test_the_vision_answer_is_parsed_never_guessed() -> None:
    loc = parse_location(
        'Elbette: {"found": true, "x": 120, "y": 44}', provider="openai", model="m"
    )
    assert (loc.x, loc.y) == (120, 44)
    assert parse_location('{"found": false}', provider="openai", model="m") is None
    with pytest.raises(VisionError):
        parse_location("Sol üstte bir düğme var.", provider="openai", model="m")
    with pytest.raises(VisionError):
        parse_location('{"found": true, "x": -3, "y": 1}', provider="openai", model="m")
    fake = FakeVisionProvider()
    assert fake.locate(b"png", target="Tamam") is None and fake.targets == ["Tamam"]


# -------------------------------------------------- the row and the lifecycle (128, 129)


def test_the_mission_row_carries_the_plan_and_one_mission_runs_at_a_time() -> None:
    h = build_harness()
    with h.factory() as db:
        row = mission_service.start_mission_db(
            db, text="Chrome'u aç ve YouTube'a gir", session_id="s1"
        )
        assert row.status == "planned" and row.step_count == 2
        assert [s["kind"] for s in row.mission_json["steps"]] == [KIND_APP_OPEN, KIND_NAVIGATE]
        with pytest.raises(mission_service.MissionServiceError, match="başka bir görev"):
            mission_service.start_mission_db(db, text="Not Defteri'ni aç", session_id="s1")
        assert mission_service.active_mission(db).id == row.id
        started = list(
            db.scalars(
                select(ActivityEventRow).where(
                    ActivityEventRow.event_type == "operator.mission.started"
                )
            )
        )
        assert len(started) == 1 and started[0].action == "operator.mission"

        device = _chrome_device()
        done = mission_service.run_inline(db, row.id, MissionPorts(device=device))
        assert done.status == MISSION_SUCCEEDED and done.current_step == 2
        assert [s["status"] for s in done.mission_json["steps"]] == [STEP_DONE, STEP_DONE]
        finished = list(
            db.scalars(
                select(ActivityEventRow).where(
                    ActivityEventRow.event_type == "operator.mission.finished"
                )
            )
        )
        assert len(finished) == 1 and finished[0].detail_json["levels"] == ["api", "dom"]
        assert mission_service.active_mission(db) is None


def test_the_row_parks_pauses_resumes_and_cancels_as_the_owner_says() -> None:
    h = build_harness()
    device = _chrome_device()
    with h.factory() as db:
        row = mission_service.start_mission_db(
            db, text="Chrome'u aç ve YouTube'a gir", preview=True
        )
        assert row.status == MISSION_AWAITING_APPROVAL
        assert (
            mission_service.run_step_db(db, row.id, MissionPorts(device=device))["status"]
            == MISSION_AWAITING_APPROVAL
        )
        assert device.capabilities_called() == []
        approved = mission_service.approve_db(db, row.id)
        assert approved.status == "planned" and approved.approved is True
        with pytest.raises(mission_service.MissionServiceError, match="beklemiyor"):
            mission_service.approve_db(db, row.id)
        # One step, then a pause request, honoured before the next.
        assert (
            mission_service.run_step_db(db, row.id, MissionPorts(device=device))["status"]
            == MISSION_RUNNING
        )
        mission_service.pause_db(db, row.id)
        assert (
            mission_service.run_step_db(db, row.id, MissionPorts(device=device))["status"]
            == MISSION_PAUSED
        )
        mission_service.resume_db(db, row.id)
        assert (
            mission_service.run_step_db(db, row.id, MissionPorts(device=device))["status"]
            == MISSION_SUCCEEDED
        )
        with pytest.raises(mission_service.MissionServiceError, match="zaten bitmiş"):
            mission_service.cancel_db(db, row.id)

        parked = mission_service.start_mission_db(
            db, text="Not Defteri'ni aç ve merhaba yaz", preview=True
        )
        cancelled = mission_service.cancel_db(db, parked.id)
        assert cancelled.status == MISSION_CANCELLED and cancelled.cancel_requested


# ------------------------------------------------------------- the workflow (128, 129)

_STEPS: list[str] = []
_MARKS: list[str] = []
_SCRIPT: list[dict[str, Any]] = []


@activity.defn(name="operator_mission_step")
async def fake_step(mission_id: str) -> dict[str, Any]:
    _STEPS.append(mission_id)
    entry = _SCRIPT.pop(0) if _SCRIPT else {"status": "succeeded", "current_step": 2}
    if "raise" in entry:
        raise RuntimeError(str(entry["raise"]))
    return entry


@activity.defn(name="operator_mission_fail")
async def fake_fail(mission_id: str, reason: str) -> dict[str, Any]:
    _MARKS.append(f"failed:{reason}")
    return {"status": "failed", "current_step": 0}


@activity.defn(name="operator_mission_mark")
async def fake_mark(mission_id: str, mark: str) -> dict[str, Any]:
    _MARKS.append(mark)
    return {"status": "planned", "current_step": 0}


@activity.defn(name="operator_mission_cancel")
async def fake_cancel(mission_id: str) -> dict[str, Any]:
    _MARKS.append("cancelled")
    return {"status": "cancelled", "current_step": 0}


@pytest.mark.asyncio
async def test_the_workflow_waits_for_the_owners_yes_and_for_a_resume_then_finishes() -> None:
    _STEPS.clear()
    _MARKS.clear()
    _SCRIPT[:] = [
        {"status": "awaiting_approval", "current_step": 0},
        {"status": "running", "current_step": 1},
        {"status": "paused", "current_step": 1},
        {"status": "succeeded", "current_step": 2},
    ]
    env = await WorkflowEnvironment.start_time_skipping()
    worker = Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[OperatorMissionWorkflow],
        activities=[fake_step, fake_mark, fake_cancel],
    )
    async with env, worker:
        handle = await env.client.start_workflow(
            OperatorMissionWorkflow.run,
            MissionRequest(mission_id="m-1"),
            id="operator-mission-m-1",
            task_queue=TASK_QUEUE,
        )
        for _ in range(50):
            status = await handle.query(OperatorMissionWorkflow.status)
            if status.get("status") == "awaiting_approval":
                break
            await asyncio.sleep(0.05)
        assert status["status"] == "awaiting_approval" and _STEPS == ["m-1"]
        await handle.signal(OperatorMissionWorkflow.approve)
        for _ in range(50):
            status = await handle.query(OperatorMissionWorkflow.status)
            if status.get("status") == "paused":
                break
            await asyncio.sleep(0.05)
        assert status["status"] == "paused" and _MARKS == ["approved"]
        await handle.signal(OperatorMissionWorkflow.resume)
        result = await asyncio.wait_for(handle.result(), timeout=30)
    assert result["status"] == "succeeded" and _MARKS == ["approved", "resumed"]
    assert len(_STEPS) == 4


# ------------------------------------------------------------ the routes and the voice


def _fake_temporal_client():
    handle = AsyncMock()
    handle.signal = AsyncMock(return_value=None)
    client = AsyncMock()
    client.start_workflow = AsyncMock(return_value=None)
    client.get_workflow_handle = lambda *_a, **_k: handle
    return client, handle


def test_the_routes_start_show_and_control_a_mission_through_the_application() -> None:
    h = build_harness()
    client, handle = _fake_temporal_client()
    with patch("app.operator.mission_routes.Client.connect", AsyncMock(return_value=client)):
        refused = h.client.post("/v1/operator/missions", json={"text": "Chrome'u aç ve kahve yap"})
        assert (
            refused.status_code == 422
            and refused.json()["detail"]["code"] == "clarification_needed"
        )
        started = h.client.post(
            "/v1/operator/missions", json={"text": "Chrome'u aç ve YouTube'a gir", "preview": True}
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert (
            body["status"] == MISSION_AWAITING_APPROVAL
            and body["step_count"] == 2
            and body["source"] == "rest"
        )
        assert client.start_workflow.await_count == 1
        listed = h.client.get("/v1/operator/missions").json()["missions"]
        assert [m["mission_id"] for m in listed] == [body["mission_id"]]
        approved = h.client.post(f"/v1/operator/missions/{body['mission_id']}/approve")
        assert (
            approved.status_code == 200
            and approved.json()["approved"] is True
            and approved.json()["signalled"] is True
        )
        assert handle.signal.await_count == 1
        again = h.client.post(f"/v1/operator/missions/{body['mission_id']}/approve")
        assert again.status_code == 422 and again.json()["detail"]["code"] == "not_awaiting"
        cancelled = h.client.post(f"/v1/operator/missions/{body['mission_id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["cancel_requested"] is True
        missing = h.client.get(f"/v1/operator/missions/{uuid.uuid4()}")
        assert missing.status_code == 404
    # Without an owner session: refused before anything is planned.
    from fastapi.testclient import TestClient

    anonymous = TestClient(h.client.app)
    assert (
        anonymous.post(
            "/v1/operator/missions", json={"text": "Chrome'u aç ve YouTube'a gir"}
        ).status_code
        == 401
    )


@pytest.mark.parametrize(
    ("text", "intent", "state"),
    [
        ("Chrome'u aç ve YouTube'a gir.", Intent.MISSION_START, None),
        ("Ayarlarda Bluetooth'u aç.", Intent.MISSION_START, None),
        ("Dosya Gezgini'nde İndirilenler klasörünü aç.", Intent.MISSION_START, None),
        ("Not Defteri'ni aç, sonra merhaba yaz; önce göster.", Intent.MISSION_START, None),
        # Single simple steps stay with the tools that own them.
        ("Not Defteri'ni aç.", Intent.APP_OPEN, None),
        ("Buraya merhaba yaz.", Intent.TYPE_TEXT, None),
        ("Tamam düğmesine tıkla.", Intent.UI_INVOKE, None),
        ("Bunu kapat.", Intent.WINDOW_CLOSE, None),
        # The control words, only while a mission is parked or running.
        ("Evet, başla.", Intent.MISSION_APPROVE, "awaiting_approval"),
        ("Onaylıyorum.", Intent.MISSION_APPROVE, "awaiting_approval"),
        ("Bekle.", Intent.MISSION_PAUSE, "running"),
        ("Devam et.", Intent.MISSION_RESUME, "paused"),
    ],
)
def test_the_router_reads_a_mission_and_its_words_by_state(
    text: str, intent: Intent, state: str | None
) -> None:
    resolved = resolve_intent(text, mission_state=state, operator_running=state is not None)
    assert resolved.intent is intent, (text, resolved.intent, resolved.matched)
    if intent is Intent.MISSION_START:
        assert resolved.mission_request == text.strip() and resolved.mission_action == "start"
    if state is not None:
        assert resolved.mission_action == intent.value.split("_", 1)[1]


def test_the_control_words_mean_nothing_special_without_a_mission() -> None:
    assert resolve_intent("Evet, başla.").intent is not Intent.MISSION_APPROVE
    assert resolve_intent("Bekle.").intent is not Intent.MISSION_PAUSE
    assert resolve_intent("Devam et.", mission_state="running").intent is not Intent.MISSION_RESUME


def test_the_voice_tool_plans_the_owners_sentence_and_the_row_carries_it() -> None:
    h = build_harness()
    sid = h.new_session()
    said = h.say(sid, "Chrome'u aç ve YouTube'a gir; önce göster.")
    assert said["resolved_intents"][-1]["intent"] == "mission_start"
    call = h.tool(
        sid, "c-1", "operator.mission", {"action": "start", "content": "the model's paraphrase"}
    )
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed" and body["status"] == MISSION_AWAITING_APPROVAL
    assert "Planım şu" in body["speech"] and "Chrome" in body["speech"]
    assert h.device.capabilities_called() == []
    with h.factory() as db:
        rows = list(db.scalars(select(OperatorMissionRow)))
        assert len(rows) == 1 and rows[0].goal == "Chrome'u aç ve YouTube'a gir; önce göster."
        assert rows[0].source == "voice" and rows[0].preview is True
        # The harness has no Temporal: the deferred start reported through the row.
        assert rows[0].status in (MISSION_AWAITING_APPROVAL, "failed")
        rows[0].status = MISSION_AWAITING_APPROVAL
        db.commit()
    status = h.tool(sid, "c-2", "operator.mission", {"action": "status"})
    assert "onayınızı bekliyor" in status["result"]["speech"]
    # "Dur." while a mission is parked is the mission's cancel (operator.cancel delegates).
    said = h.say(sid, "Dur.", turn=2)
    assert said["resolved_intents"][-1]["intent"] == "operator_cancel"
    cancel = h.tool(sid, "c-3", "operator.cancel", {})
    assert cancel["result"]["speech"] == "Görevi iptal ettim efendim."
    with h.factory() as db:
        assert mission_service.active_mission(db) is None


def test_the_tool_is_registered_tiered_and_refuses_an_empty_sentence() -> None:
    from app.operator.capabilities import CAPABILITY_MISSION
    from app.security.step_up import TIER_SENSITIVE, tier_of
    from app.voice.realtime_sessions.tools import default_registry
    from app.voice.realtime_sessions.tools_mission import TOOL_MISSION

    assert TOOL_MISSION == CAPABILITY_MISSION == "operator.mission"
    assert "operator.mission" in set(default_registry().names())
    assert tier_of("operator.mission") == TIER_SENSITIVE
    h = build_harness()
    sid = h.new_session()
    call = h.tool(sid, "c-1", "operator.mission", {"action": "start"})
    assert call["result"]["status"] == "needs_clarification"
    assert call["result"]["error_class"] == "invalid_argument"


def test_a_mission_step_and_its_record_round_trip_as_data() -> None:
    m = plan_mission("Chrome'u aç ve YouTube'a gir")
    m.trail.append({"step": "m1", "round": 1, "outcome": "verified"})
    again = Mission.from_dict(m.as_dict())
    assert again.as_dict() == m.as_dict()
    assert isinstance(again.steps[0], MissionStep) and again.id == m.id
    assert isinstance(OperatorStep("window.current").payload_from, type(None))


# ------------------ 2026-09-18, production: "Chrome'dan YouTube'u aç" - four faults, one sentence


def test_the_browser_brand_is_not_the_destination() -> None:
    """ "Google Chrome'dan direkt YouTube ana sayfasını aç" was planned as google.com: the
    first known site in the sentence won, and it was the browser's own first name."""
    m = plan_mission("Google Chrome’dan direkt YouTube ana sayfasını aç")
    assert [(s.kind, s.args) for s in m.steps] == [
        (KIND_APP_OPEN, {"application": "chrome"}),
        (KIND_NAVIGATE, {"url": "https://www.youtube.com/"}),
    ]


def test_google_on_its_own_is_still_a_destination() -> None:
    m = plan_mission("Google’a git")
    assert m.steps[-1].args == {"url": "https://www.google.com/"}


def _paused_mission(db: Any) -> Any:
    row = mission_service.start_mission_db(db, text="Chrome'u aç ve YouTube'a gir")
    device = _chrome_device()
    mission_service.run_step_db(db, row.id, MissionPorts(device=device))
    mission_service.pause_db(db, row.id)
    mission_service.run_step_db(db, row.id, MissionPorts(device=device))
    assert mission_service.get_mission(db, row.id).status == MISSION_PAUSED
    return row


def test_a_resume_the_tool_already_applied_does_not_kill_the_workflow_it_wakes(
    monkeypatch,
) -> None:
    """The voice tool applies the owner's "Devam et" to the row and THEN signals the
    workflow. The workflow's mark activity applied it a second time, found the row no longer
    paused, raised "Devam ettirecek duraklatılmış bir görev yok" - and the workflow failed
    with the row left "running" for ever (operator-mission-a7eb937f)."""
    from app.operator import mission_activities

    h = build_harness()
    monkeypatch.setattr(mission_activities, "_factory", lambda: h.factory)
    with h.factory() as db:
        row = _paused_mission(db)
        mission_service.resume_db(db, row.id)  # the tool's half

    out = asyncio.run(mission_activities.mission_mark_activity(str(row.id), "resumed"))

    assert out["status"] == MISSION_RUNNING


def test_an_approval_the_tool_already_applied_does_not_kill_the_workflow_either(
    monkeypatch,
) -> None:
    from app.operator import mission_activities

    h = build_harness()
    monkeypatch.setattr(mission_activities, "_factory", lambda: h.factory)
    with h.factory() as db:
        row = mission_service.start_mission_db(db, text="Not Defteri'ni aç", preview=True)
        mission_service.approve_db(db, row.id)  # the tool's half

    out = asyncio.run(mission_activities.mission_mark_activity(str(row.id), "approved"))

    assert out["status"] == "planned"


def test_a_signal_alone_still_applies_the_owners_word(monkeypatch) -> None:
    """The mark must stay a real write when nothing applied it first."""
    from app.operator import mission_activities

    h = build_harness()
    monkeypatch.setattr(mission_activities, "_factory", lambda: h.factory)
    with h.factory() as db:
        row = _paused_mission(db)

    out = asyncio.run(mission_activities.mission_mark_activity(str(row.id), "resumed"))

    assert out["status"] == MISSION_RUNNING


def _age(db: Any, row_id: Any, *, status: str, seconds: int) -> None:
    from datetime import UTC, datetime, timedelta

    row = mission_service.get_mission(db, row_id)
    row.status = status
    row.updated_at = datetime.now(UTC) - timedelta(seconds=seconds)
    db.commit()


def test_a_running_row_nothing_has_written_for_too_long_is_closed_not_obeyed() -> None:
    """The stuck row refused every later mission as "mission_in_flight", and cancelling it
    only set a flag for an activity that would never read it."""
    h = build_harness()
    with h.factory() as db:
        stuck = mission_service.start_mission_db(db, text="Chrome'u aç ve YouTube'a gir")
        _age(db, stuck.id, status=MISSION_RUNNING, seconds=600)

        fresh = mission_service.start_mission_db(db, text="Not Defteri'ni aç")

        closed = mission_service.get_mission(db, stuck.id)
        assert closed.status == "failed"
        assert closed.error_class == "workflow_abandoned"
        assert mission_service.active_mission(db).id == fresh.id


def test_a_quiet_row_that_waits_for_the_owner_is_not_an_orphan() -> None:
    h = build_harness()
    with h.factory() as db:
        waiting = mission_service.start_mission_db(db, text="Chrome'u aç ve YouTube'a gir")
        _age(db, waiting.id, status=MISSION_PAUSED, seconds=3600)
        with pytest.raises(mission_service.MissionServiceError, match="başka bir görev"):
            mission_service.start_mission_db(db, text="Not Defteri'ni aç")


def test_a_running_row_inside_one_steps_lifetime_is_left_alone() -> None:
    h = build_harness()
    with h.factory() as db:
        busy = mission_service.start_mission_db(db, text="Chrome'u aç ve YouTube'a gir")
        _age(db, busy.id, status=MISSION_RUNNING, seconds=200)
        with pytest.raises(mission_service.MissionServiceError, match="başka bir görev"):
            mission_service.start_mission_db(db, text="Not Defteri'ni aç")


def test_the_orphan_bound_outlives_the_longest_step() -> None:
    """Two numbers for one fact - the step activity's lifetime and the orphan bound - held
    to each other, because the bound is only true while it is the longer of the two."""
    from datetime import timedelta

    from app.operator.mission_workflow import STEP_ACTIVITY_TIMEOUT_S

    assert mission_service.ORPHANED_AFTER > timedelta(seconds=STEP_ACTIVITY_TIMEOUT_S)


def _mission_ctx(db: Any, *, said: str | None) -> Any:
    from datetime import UTC, datetime

    from app.voice.realtime_sessions.tools import ToolContext

    turn = {"turn": 3, "mission_action": said} if said else {"turn": 3}
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="web",
        context={"last_utterance": turn},
        db=db,
        now=datetime.now(UTC),
        call_id="call-1",
        live={},
    )


@pytest.mark.parametrize("action", ["resume", "approve"])
def test_only_the_owners_word_moves_a_mission_that_waits_for_the_owner(action: str) -> None:
    """A step escalated at 19:00:30 asking "Nasıl devam edeyim?" and the model resumed it
    at 19:00:31 - one second, no owner word in between."""
    from app.voice.realtime_sessions import tools_mission

    h = build_harness()
    with h.factory() as db:
        if action == "resume":
            row = _paused_mission(db)
        else:
            row = mission_service.start_mission_db(db, text="Not Defteri'ni aç", preview=True)
        before = mission_service.get_mission(db, row.id).status

        out = tools_mission.operator_mission(_mission_ctx(db, said=None), {"action": action})

        assert out["error_class"] == "owner_word_required"
        assert mission_service.get_mission(db, row.id).status == before


def test_the_owners_resume_still_resumes() -> None:
    from app.voice.realtime_sessions import tools_mission

    h = build_harness()
    with h.factory() as db:
        row = _paused_mission(db)

        out = tools_mission.operator_mission(_mission_ctx(db, said="resume"), {"action": "resume"})

        assert out.get("error_class") in (None, "")
        assert mission_service.get_mission(db, row.id).status == MISSION_RUNNING


def test_the_model_may_still_stop_or_ask() -> None:
    """Stopping and asking only ever make the system do less; they stay open to the model."""
    from app.voice.realtime_sessions import tools_mission

    h = build_harness()
    with h.factory() as db:
        row = mission_service.start_mission_db(db, text="Not Defteri'ni aç", preview=True)
        status = tools_mission.operator_mission(_mission_ctx(db, said=None), {"action": "status"})
        assert status.get("error_class") in (None, "")
        out = tools_mission.operator_mission(_mission_ctx(db, said=None), {"action": "cancel"})
        assert out.get("error_class") in (None, "")
        assert mission_service.get_mission(db, row.id).status == MISSION_CANCELLED


# ------------- 2026-09-18: "ekrandaki söylediğim şeyin yerini bulup mouse'u götürüp sol klik"


@pytest.mark.parametrize(
    ("said", "target"),
    [
        ("Atatürk belgeseli videosunu aç", "Atatürk belgeseli"),
        ("Tarkan’a tıkla", "Tarkan"),
        ("Ekranda Abone ol yazana tıkla", "Abone ol"),
        ("İlk videoyu aç", "İlk video"),
        ("Şımarık videosunu oynat", "Şımarık"),
    ],
)
def test_the_planner_reads_a_thing_on_the_screen(said: str, target: str) -> None:
    m = plan_mission(said)
    assert [(s.kind, s.args["name"]) for s in m.steps] == [(mission.KIND_CLICK_TEXT, target)]


def test_a_thing_to_click_is_never_typed() -> None:
    """ "... YAZANA tıkla" was read by the typing matcher as text to type INTO the window."""
    m = plan_mission("Ekranda Abone ol yazana tıkla")
    assert KIND_TYPE_TEXT not in [s.kind for s in m.steps]


@pytest.mark.parametrize(
    ("said", "kind"),
    [
        ("Tamam düğmesine tıkla", KIND_UI_INVOKE),
        ("Not Defteri’ni aç", KIND_APP_OPEN),
        ("Word’e merhaba yaz", KIND_OFFICE_TYPE),
    ],
)
def test_buttons_applications_and_typing_keep_their_own_reading(said: str, kind: str) -> None:
    assert plan_mission(said).steps[-1].kind == kind


def test_a_whole_request_opens_the_site_and_then_the_video() -> None:
    m = plan_mission("Chrome’dan YouTube’u aç ve Barış Manço videosunu aç")
    assert [s.kind for s in m.steps] == [KIND_APP_OPEN, KIND_NAVIGATE, mission.KIND_CLICK_TEXT]
    assert m.steps[-1].args["name"] == "Barış Manço"


YOUTUBE = _window(2, "chrome.exe", "YouTube - Google Chrome")


def _youtube_page(*, opens_to: str = "Barış Manço - Dönence - YouTube - Google Chrome") -> Any:
    """The owner's Chrome on YouTube. A left click changes the title - only on the SECOND
    look after it, as a video page opens a moment later."""
    state = {"clicked": False, "looks": 0}

    def current(payload: dict[str, Any]) -> DeviceRunResult:
        if state["clicked"]:
            state["looks"] += 1
        title = opens_to if state["clicked"] and state["looks"] >= 2 else YOUTUBE["title"]
        return ok(window={**YOUTUBE, "title": title})

    def click(payload: dict[str, Any]) -> DeviceRunResult:
        state["clicked"] = True
        x, y = int(payload["x"]), int(payload["y"])
        return ok(
            x=x,
            y=y,
            space="screen",
            screen_x=x,
            screen_y=y,
            observed={"cursor": {"x": x, "y": y}, "window": dict(YOUTUBE)},
        )

    return FakeDeviceAction(
        results={
            "window.current": current,
            "window.list": ok(windows=[dict(YOUTUBE)]),
            "window.activate": lambda p: ok(
                window={**YOUTUBE, "window_id": str(p.get("window_id"))}
            ),
            "screen.capture": ok(width=200, height=100, png_base64=ONE_PIXEL_PNG_B64, scale=1),
            "pointer.click": click,
        }
    )


def test_the_video_is_found_on_the_screen_and_clicked_where_it_is(monkeypatch) -> None:
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _youtube_page()
    vision = FakeVisionProvider(location=(640, 360))

    m = plan_mission("Barış Manço videosunu aç")
    run_mission(m, MissionPorts(device=device, vision=vision))

    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert vision.targets == ["Barış Manço"], "the picture was asked for the owner's own words"
    assert device.payload_for("pointer.click") == {
        "window_id": YOUTUBE["window_id"],
        "x": 640,
        "y": 360,
        "space": "screen",
    }
    assert m.steps[0].level == LEVEL_VISUAL


def test_a_click_that_opened_nothing_is_not_reported_as_opened(monkeypatch) -> None:
    """The click landed; the title never moved. That is a step that did not do what was
    asked - never "videoyu açtım" on the strength of a click."""
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _youtube_page(opens_to=YOUTUBE["title"])
    vision = FakeVisionProvider(location=(640, 360))

    m = plan_mission("Barış Manço videosunu aç")
    run_mission(m, MissionPorts(device=device, vision=vision))

    assert m.status == MISSION_PAUSED
    assert m.steps[0].error_class == "postcondition_failed"


def test_a_thing_the_picture_cannot_find_is_said_not_guessed() -> None:
    device = _youtube_page()
    m = plan_mission("Barış Manço videosunu aç")
    run_mission(m, MissionPorts(device=device, vision=FakeVisionProvider(location=None)))
    assert m.status == MISSION_PAUSED
    assert m.steps[0].error_class == "ui_target_not_found"
    assert "pointer.click" not in device.capabilities_called()


def test_without_a_picture_there_is_no_coordinate_click() -> None:
    device = _youtube_page()
    m = plan_mission("Barış Manço videosunu aç")
    run_mission(m, MissionPorts(device=device, vision=None))
    assert m.steps[0].error_class == "vision_unavailable"
    assert "pointer.click" not in device.capabilities_called()


# ------------------ "YouTube'da X", "Google'da X ara": the words typed, Enter pressed


@pytest.mark.parametrize(
    ("said", "url"),
    [
        ("YouTube’da Barış Manço aç", "https://www.youtube.com/results?search_query=Barış+Manço"),
        (
            "YouTube’da Barış Manço videosunu aç",
            "https://www.youtube.com/results?search_query=Barış+Manço",
        ),
        ("Google’da hava durumu ara", "https://www.google.com/search?q=hava+durumu"),
        # the normaliser splits "Chrome'dan" into "chrome" + "dan"; "dan" is not searched for
        (
            "Chrome’dan YouTube’da Sezen Aksu ara",
            "https://www.youtube.com/results?search_query=Sezen+Aksu",
        ),
        (
            "Google Chrome’da YouTube’da Tarkan ara",
            "https://www.youtube.com/results?search_query=Tarkan",
        ),
        ("Google’da 5+3 kaç ara", "https://www.google.com/search?q=5+3+kaç"),
    ],
)
def test_a_site_named_with_the_locative_is_searched_on(said: str, url: str) -> None:
    m = plan_mission(said)
    assert [s.kind for s in m.steps] == [KIND_APP_OPEN, KIND_NAVIGATE]
    assert m.steps[-1].args["url"] == url


@pytest.mark.parametrize("said", ["YouTube’u aç", "Chrome’dan YouTube’u aç", "Google’a git"])
def test_a_site_named_as_a_destination_is_just_opened(said: str) -> None:
    url = plan_mission(said).steps[-1].args["url"]
    assert "search" not in url and "results" not in url


def test_the_search_is_typed_readably_and_arrives_on_the_site(monkeypatch) -> None:
    """What the owner watches being typed is the words they said, and the results page is
    still recognised as the site it belongs to."""
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _owners_chrome_desktop(lands_on="Barış Manço - YouTube - Google Chrome")

    m = plan_mission("YouTube’da Barış Manço aç")
    run_mission(m, MissionPorts(device=device))

    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    typed = device.payload_for("keyboard.type")["text"]
    assert typed == "https://www.youtube.com/results?search_query=Barış+Manço"


@pytest.mark.parametrize(
    "said", ["İptal butonuna tıkla", "Tamam düğmesine tıkla", "Kaydet tuşuna bas"]
)
def test_a_button_is_never_the_pictures(said: str) -> None:
    """Buttons are named in the accessibility tree - a rung above the screen picture."""
    try:
        m = plan_mission(said)
    except MissionClarificationNeeded:
        return  # the single-step ui.invoke tool answers it, as before
    assert mission.KIND_CLICK_TEXT not in [s.kind for s in m.steps]


# ------------- routing: what the router hands to the mission, and what it keeps elsewhere


@pytest.mark.parametrize(
    ("said", "intent"),
    [
        # the owner's own sentences of 2026-09-18 - their own Chrome
        ("Chrome’dan YouTube’u aç", Intent.MISSION_START),
        ("Google Chrome’dan direkt YouTube ana sayfasını aç", Intent.MISSION_START),
        ("YouTube’da Barış Manço ara", Intent.MISSION_START),
        ("Google’da hava durumu ara", Intent.MISSION_START),
        ("Barış Manço videosunu aç", Intent.MISSION_START),
        # kept where they were: the news tool, the media player, the app tool
        ("Haberleri YouTube’dan aç", Intent.NEWS_OPEN),
        ("Bugünün Show Ana Haber videosunu aç", Intent.NEWS_OPEN),
        # owner decision 2026-09-18: YouTube requests go to the owner's own Chrome
        ("YouTube’da Barış Manço aç", Intent.MISSION_START),
        ("Not Defteri’ni aç", Intent.APP_OPEN),
    ],
)
def test_the_router_gives_the_mission_what_is_the_missions(said: str, intent: Intent) -> None:
    assert resolve_intent(said).intent is intent


def test_a_step_the_planner_added_is_not_a_second_request() -> None:
    """ "Haberleri YouTube'dan aç" became a two-step mission the moment the planner started
    putting the browser in front of every page - the router counted the planner's own
    preparation as something the owner asked for (CI 2026-09-18). The news sentence itself
    is refused by the router before the planner sees it now; the implicit step is shown on
    the plain page request."""
    m = plan_mission("YouTube’u aç")
    assert m.steps[0].args.get("implicit") is True
    named = plan_mission("Chrome’dan YouTube’u aç")
    assert "implicit" not in named.steps[0].args, "the owner said Chrome; it was asked for"


# ---------------- owner scenario 2026-09-18: tabs, a paused video, a video not on this tab


@pytest.mark.parametrize(
    ("said", "steps"),
    [
        ("Yan sekmeye geç", [(mission.KIND_TAB_SWITCH, {"direction": "next"})]),
        ("Önceki sekmeye geç", [(mission.KIND_TAB_SWITCH, {"direction": "prev"})]),
        ("Üçüncü sekmeye geç", [(mission.KIND_TAB_SWITCH, {"index": 3})]),
        ("3. sekmeye geç", [(mission.KIND_TAB_SWITCH, {"index": 3})]),
        ("Sekmeyi kapat", [(mission.KIND_TAB_CLOSE, {})]),
        ("Videoyu aç", [(mission.KIND_VIDEO_PLAY, {})]),
        ("Videoyu oynat", [(mission.KIND_VIDEO_PLAY, {})]),
    ],
)
def test_the_planner_reads_the_browser_as_the_owner_uses_it(said: str, steps: list) -> None:
    assert [(s.kind, s.args) for s in plan_mission(said).steps] == steps


def test_a_new_tab_carries_the_rest_of_the_sentence() -> None:
    m = plan_mission("Yeni sekmede YouTube aç")
    assert [(s.kind, s.args.get("url")) for s in m.steps] == [
        (mission.KIND_TAB_NEW, None),
        (KIND_NAVIGATE, "https://www.youtube.com/"),
    ]


def test_a_position_on_the_screen_names_a_video_and_is_never_searched_for() -> None:
    step = plan_mission("Sağdan üçüncü videoyu aç").steps[0]
    assert step.kind == mission.KIND_CLICK_TEXT
    assert step.args["name"] == "Sağdan üçüncü video"
    assert "search_if_missing" not in step.args
    named = plan_mission("Barış Manço videosunu aç").steps[0]
    assert named.args.get("search_if_missing") is True


@pytest.mark.parametrize(
    ("said", "intent"),
    [
        ("YouTube’u aç", Intent.MISSION_START),
        ("Yan sekmeye geç", Intent.MISSION_START),
        ("Sekmeyi kapat", Intent.MISSION_START),
        ("Yeni sekmede YouTube aç", Intent.MISSION_START),
        ("Videoyu oynat", Intent.MISSION_START),
        ("Haberleri YouTube’dan aç", Intent.NEWS_OPEN),
    ],
)
def test_the_router_sends_the_browser_words_to_the_mission(said: str, intent: Intent) -> None:
    assert resolve_intent(said).intent is intent


def _tabbed_chrome(titles: list[str]) -> Any:
    """The owner's Chrome with several tabs: a tab chord moves the title to the next
    entry of ``titles``; Ctrl+W drops the current one; Ctrl+T adds a blank tab."""
    state = {"i": 0, "tabs": list(titles)}

    def title() -> str:
        return state["tabs"][state["i"]] if state["tabs"] else ""

    def current(payload: dict[str, Any]) -> DeviceRunResult:
        if not state["tabs"]:
            return ok(window=None)
        return ok(window={**CHROME, "title": title()})

    def shortcut(payload: dict[str, Any]) -> DeviceRunResult:
        keys = list(payload.get("keys") or [])
        n = len(state["tabs"])
        if keys == ["ctrl", "tab"]:
            state["i"] = (state["i"] + 1) % n
        elif keys == ["ctrl", "shift", "tab"]:
            state["i"] = (state["i"] - 1) % n
        elif keys[0] == "ctrl" and keys[1].isdigit():
            k = int(keys[1])
            state["i"] = n - 1 if k == 9 else min(k, n) - 1
        elif keys == ["ctrl", "w"]:
            state["tabs"].pop(state["i"])
            state["i"] = min(state["i"], len(state["tabs"]) - 1)
        elif keys == ["ctrl", "t"]:
            state["tabs"].append("Yeni Sekme - Google Chrome")
            state["i"] = len(state["tabs"]) - 1
        return ok(
            keys=keys, window_id=payload.get("window_id"), observed=_observed(payload, CHROME)
        )

    device = FakeDeviceAction(
        results={
            "window.current": current,
            "window.list": ok(windows=[dict(CHROME)]),
            "window.activate": lambda p: ok(
                window={**CHROME, "window_id": str(p.get("window_id"))}
            ),
            "keyboard.shortcut": shortcut,
        }
    )
    device.tabs = state  # type: ignore[attr-defined]
    return device


def test_the_next_tab_is_reached_by_chromes_own_chord_and_proven_by_its_title(monkeypatch) -> None:
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _tabbed_chrome(["YouTube - Google Chrome", "Speedrunners - YouTube - Google Chrome"])
    m = plan_mission("Yan sekmeye geç")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert device.payload_for("keyboard.shortcut")["keys"] == ["ctrl", "tab"]
    assert device.tabs["i"] == 1


def test_a_numbered_tab_uses_ctrl_and_the_number() -> None:
    device = _tabbed_chrome(["A - Google Chrome", "B - Google Chrome", "C - Google Chrome"])
    m = plan_mission("Üçüncü sekmeye geç")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert device.payload_for("keyboard.shortcut")["keys"] == ["ctrl", "3"]


def test_a_switch_that_changed_nothing_is_not_reported_as_a_switch(monkeypatch) -> None:
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _tabbed_chrome(["Only - Google Chrome"])  # one tab: Ctrl+Tab lands on itself
    m = plan_mission("Yan sekmeye geç")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_PAUSED
    assert m.steps[0].error_class == "postcondition_failed"


def test_closing_the_tab_shows_the_next_one() -> None:
    device = _tabbed_chrome(["A - Google Chrome", "B - Google Chrome"])
    m = plan_mission("Sekmeyi kapat")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert device.payload_for("keyboard.shortcut")["keys"] == ["ctrl", "w"]
    assert device.tabs["tabs"] == ["B - Google Chrome"]


def test_a_new_tab_then_the_page_typed_into_it(monkeypatch) -> None:
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _tabbed_chrome(["A - Google Chrome"])
    typed: list[str] = []

    def type_text(payload: dict[str, Any]) -> DeviceRunResult:
        typed.append(str(payload.get("text")))
        return ok(
            typed_chars=len(str(payload.get("text"))),
            window_id=payload.get("window_id"),
            observed=_observed(payload, CHROME),
        )

    def key(payload: dict[str, Any]) -> DeviceRunResult:
        if payload.get("key") == "enter" and typed:
            device.tabs["tabs"][device.tabs["i"]] = "YouTube - Google Chrome"
        return ok(
            key=payload.get("key"),
            window_id=payload.get("window_id"),
            observed=_observed(payload, CHROME),
        )

    device.results["keyboard.type"] = type_text
    device.results["keyboard.key"] = key
    device.results["browser.session_open"] = DeviceRunResult(
        False, "dependency_unavailable", "no CDP"
    )

    m = plan_mission("Yeni sekmede YouTube aç")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert [
        c["payload"].get("keys") for c in device.calls if c["capability"] == "keyboard.shortcut"
    ] == [
        ["ctrl", "t"],
        ["ctrl", "l"],
    ]
    assert typed == ["https://www.youtube.com/"]
    assert device.tabs["tabs"] == ["A - Google Chrome", "YouTube - Google Chrome"]


# ---- the paused video: proven by motion, never by the click


def _png_of(shade: int, *, spot: int | None = None) -> str:
    import base64
    from io import BytesIO

    from PIL import Image

    image = Image.new("L", (192, 108), shade)
    if spot is not None:
        for x in range(60, 130):
            for y in range(30, 80):
                image.putpixel((x, y), spot)
    out = BytesIO()
    image.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode("ascii")


def _paused_player(
    *,
    starts_playing: bool,
    key_starts: bool = False,
    playing: bool = False,
    char_keys: bool = True,
    windows: list[dict[str, Any]] | None = None,
) -> Any:
    """A video page with a STATE, not a capture count: while it plays, two looks a moment
    apart differ; while it is paused they are the same picture. A click on the player starts
    it when ``starts_playing``; the page's own key (k, or Space) toggles it when
    ``key_starts``. ``char_keys=False`` is an agent that predates single-character keys."""
    state = {"playing": playing, "tick": 0}
    shown = windows or [dict(YOUTUBE)]

    def capture(payload: dict[str, Any]) -> DeviceRunResult:
        if state["playing"]:
            state["tick"] += 1
        # a playing video never shows the same frame twice
        shade = 30 + (state["tick"] * 23) % 140 if state["playing"] else 200
        return ok(width=192, height=108, png_base64=_png_of(200, spot=shade), scale=1)

    def click(payload: dict[str, Any]) -> DeviceRunResult:
        state["playing"] = starts_playing
        x, y = int(payload["x"]), int(payload["y"])
        return ok(
            x=x,
            y=y,
            space="screen",
            screen_x=x,
            screen_y=y,
            observed={"cursor": {"x": x, "y": y}, "window": dict(YOUTUBE)},
        )

    def key(payload: dict[str, Any]) -> DeviceRunResult:
        name = str(payload.get("key"))
        if len(name) == 1 and not char_keys:
            return DeviceRunResult(False, "validation_error", f"'{name}' is not a key")
        if name in ("k", "space") and key_starts:
            state["playing"] = not state["playing"]
        target = next(
            (w for w in shown if w.get("window_id") == payload.get("window_id")), shown[0]
        )
        return ok(key=name, window_id=payload.get("window_id"), observed=_observed(payload, target))

    device = FakeDeviceAction(
        results={
            "window.current": ok(window=dict(shown[0])),
            "window.list": ok(windows=[dict(w) for w in shown]),
            "window.activate": lambda p: ok(
                window={
                    **next(
                        (w for w in shown if w.get("window_id") == p.get("window_id")), shown[0]
                    ),
                    "foreground": True,
                }
            ),
            "screen.capture": capture,
            "pointer.click": click,
            "keyboard.key": key,
        }
    )
    device.video_state = state  # type: ignore[attr-defined]
    return device


def test_a_paused_video_is_clicked_and_proven_to_move(monkeypatch) -> None:
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _paused_player(starts_playing=True)
    vision = FakeVisionProvider(location=(96, 54))
    m = plan_mission("Videoyu oynat")
    run_mission(m, MissionPorts(device=device, vision=vision))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert device.payload_for("pointer.click")["x"] == 96
    assert vision.targets == [mission.VIDEO_PLAYER_TARGET_TR]


def test_a_video_that_stays_still_after_the_click_is_not_playing(monkeypatch) -> None:
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _paused_player(starts_playing=False)
    m = plan_mission("Videoyu oynat")
    run_mission(m, MissionPorts(device=device, vision=FakeVisionProvider(location=(96, 54))))
    assert m.status == MISSION_PAUSED
    assert m.steps[0].error_class == "postcondition_failed"


def test_frames_differ_ignores_a_corner_and_notices_the_middle() -> None:
    import base64

    still = base64.b64decode(_png_of(200))
    moved = base64.b64decode(_png_of(200, spot=20))
    assert plans.frames_differ(still, moved)
    assert not plans.frames_differ(still, still)


# ---- a video that is not on this tab: searched, then clicked on the results


def test_a_video_not_on_this_tab_is_searched_for_then_clicked(monkeypatch) -> None:
    """The owner's rule: first look at the current tab; if the video is not there, search
    for it, then open it from the results."""
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _youtube_page(opens_to="Barış Manço - Dönence - YouTube - Google Chrome")
    typed: list[str] = []
    device.results["keyboard.shortcut"] = lambda p: ok(
        keys=p.get("keys"), window_id=p.get("window_id"), observed=_observed(p, YOUTUBE)
    )
    device.results["keyboard.key"] = lambda p: ok(
        key=p.get("key"), window_id=p.get("window_id"), observed=_observed(p, YOUTUBE)
    )

    def type_text(payload: dict[str, Any]) -> DeviceRunResult:
        typed.append(str(payload.get("text")))
        return ok(
            typed_chars=len(str(payload.get("text"))),
            window_id=payload.get("window_id"),
            observed=_observed(payload, YOUTUBE),
        )

    device.results["keyboard.type"] = type_text

    class TwoLooks:
        """Not on the home tab; found on the results page (after the search was typed)."""

        name = "fake"

        def __init__(self) -> None:
            self.targets: list[str] = []

        def locate(self, png: bytes, *, target: str):
            from app.operator.vision import VisionLocation

            self.targets.append(target)
            if not typed:
                return None
            return VisionLocation(x=300, y=200, provider="fake", model="fake")

    vision = TwoLooks()
    m = plan_mission("Barış Manço videosunu aç")
    run_mission(m, MissionPorts(device=device, vision=vision))

    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert typed == ["https://www.youtube.com/results?search_query=Barış+Manço"]
    assert vision.targets == ["Barış Manço", "Barış Manço"], (
        "looked at THIS tab first, then at the results"
    )
    assert [t.get("outcome") for t in m.trail if t.get("step") == "m1"][-2:] == [
        "prepared",
        "verified",
    ]


# ------ a site plus a TITLE is not "open the site" (full suite 2026-09-18: 16 corpus cases)


@pytest.mark.parametrize(
    "said",
    [
        "YouTube’dan Sezen Aksu Gülümse aç.",
        "YouTube’dan Tarkan Şımarık aç",
        "Bana YouTube’dan Güldür Güldür aç",
    ],
)
def test_a_site_with_a_title_is_the_media_players_not_a_page(said: str) -> None:
    """Opening youtube.com would lose the song; the media player (and its stop / volume
    family) keeps these exactly as the owner's corpus records them."""
    with pytest.raises(MissionClarificationNeeded):
        plan_mission(said)
    assert resolve_intent(said).intent is Intent.MEDIA_PLAY


@pytest.mark.parametrize(
    "said",
    [
        "YouTube’u aç",
        "Google Chrome’dan direkt YouTube ana sayfasını aç",
        "Chrome’da youtube.com adresini aç",
        "Lütfen Google’a git",
    ],
)
def test_a_site_alone_is_still_a_page_in_the_owners_chrome(said: str) -> None:
    assert plan_mission(said).steps[-1].kind == KIND_NAVIGATE


# ------------------------------------------ production 2026-09-19: "YouTube'u aç" did nothing


@pytest.mark.asyncio
async def test_a_dead_step_activity_fails_the_row_instead_of_leaving_it_planned() -> None:
    """Production 2026-09-19 09:39:58 (and both 08:16 missions): the step activity raised
    ``RuntimeError: no running event loop``, the workflow failed with it, and the ROW stayed
    "planned" - nothing running it, every later mission refused as in flight for six
    minutes, the owner told the mission was waiting for them. The workflow's answer to a
    step that cannot run is the row's FAILED, with the activity's own reason on it."""
    _STEPS.clear()
    _MARKS.clear()
    _SCRIPT[:] = [{"raise": "no running event loop"}]
    env = await WorkflowEnvironment.start_time_skipping()
    worker = Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[OperatorMissionWorkflow],
        activities=[fake_step, fake_mark, fake_cancel, fake_fail],
    )
    async with env, worker:
        handle = await env.client.start_workflow(
            OperatorMissionWorkflow.run,
            MissionRequest(mission_id="m-dead"),
            id="operator-mission-m-dead",
            task_queue=TASK_QUEUE,
        )
        result = await asyncio.wait_for(handle.result(), timeout=30)
    assert result["status"] == "failed", result
    assert _STEPS == ["m-dead"]
    assert _MARKS == ["failed:RuntimeError: no running event loop"]


@pytest.mark.asyncio
async def test_a_signal_to_a_workflow_that_already_finished_is_not_an_error() -> None:
    """Production 2026-09-19 09:40:25: the owner's cancel reached a workflow that had already
    failed; the row was cancelled (the tool writes it first), and the signal's NOT_FOUND was
    logged as an error. It is the expected shape after a finished workflow - any OTHER
    RPC failure still raises."""
    from temporalio.service import RPCError, RPCStatusCode

    client, handle = _fake_temporal_client()
    handle.signal = AsyncMock(
        side_effect=RPCError("workflow execution already completed", RPCStatusCode.NOT_FOUND, b"")
    )
    await mission_service.cancel_signal(client, uuid.uuid4())  # no raise
    handle.signal = AsyncMock(side_effect=RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b""))
    with pytest.raises(RPCError):
        await mission_service.cancel_signal(client, uuid.uuid4())


def _owners_edge_desktop(
    *, lands_on: str = "YouTube - Kişisel - Microsoft Edge"
) -> FakeDeviceAction:
    """The owner's Edge, open and in front, driven by the keyboard like Chrome is."""
    from tests.alarms_support import window_id as _wid

    edge = {**CHROME, "image": "msedge.exe", "title": "Yeni sekme - Kişisel - Microsoft Edge"}
    edge["window_id"] = _wid(7)
    state = {"entered": False, "looks": 0}

    def current(payload: dict[str, Any]) -> DeviceRunResult:
        if state["entered"]:
            state["looks"] += 1
        title = lands_on if state["entered"] and state["looks"] >= 2 else edge["title"]
        return ok(window={**edge, "title": title})

    def key(payload: dict[str, Any]) -> DeviceRunResult:
        if payload.get("key") == "enter":
            state["entered"] = True
        return ok(
            key=payload.get("key"),
            window_id=payload.get("window_id"),
            observed=_observed(payload, edge),
        )

    return FakeDeviceAction(
        results={
            "window.current": current,
            "window.list": ok(windows=[dict(edge)]),
            "window.activate": lambda p: ok(window={**edge, "window_id": str(p.get("window_id"))}),
            "keyboard.shortcut": lambda p: ok(
                keys=p.get("keys"), window_id=p.get("window_id"), observed=_observed(p, edge)
            ),
            "keyboard.type": lambda p: ok(
                typed_chars=len(str(p.get("text"))),
                window_id=p.get("window_id"),
                observed=_observed(p, edge),
            ),
            "keyboard.key": key,
        }
    )


def test_a_browser_the_owner_named_is_the_one_the_plan_drives(monkeypatch) -> None:
    """Production 2026-09-19 09:39:58: "Microsoft Edge'i aç ve YouTube'a git" was planned as
    Edge, then an IMPLICIT Chrome, then a navigate that only knew Chrome. The browser the
    owner named is the browser: no second one is opened, and the address is typed into
    Edge's own window."""
    from app.operator import task as task_module
    from tests.alarms_support import window_id as _wid

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    m = plan_mission("Microsoft Edge'i aç ve YouTube'a git")
    assert [s.kind for s in m.steps] == ["app_open", "navigate"], m.as_dict()
    assert m.steps[0].args.get("application") == "msedge"
    assert m.steps[1].args.get("browser") == "msedge.exe"

    device = _owners_edge_desktop()
    run_mission(m, MissionPorts(device=device))

    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert device.payload_for("keyboard.type")["text"] == "https://www.youtube.com/"
    assert device.payload_for("keyboard.shortcut")["window_id"] == _wid(7)
    assert "browser.session_open" not in device.capabilities_called()  # attach is Chrome's
    assert "app.launch" not in device.capabilities_called()  # Edge was there: activated


def test_chrome_alone_still_gets_no_browser_stamp_and_the_owners_chrome() -> None:
    m = plan_mission("Chrome’dan YouTube’u aç")
    assert all("browser" not in s.args for s in m.steps), m.as_dict()


def test_app_open_for_a_site_the_router_planned_as_a_mission_starts_that_mission() -> None:
    """Production 2026-09-19 09:39:15: "YouTube'u aç" - the router resolved a MISSION (a
    site is not an application); the model called app_open("YouTube") and the owner heard
    the allowlist read out. The owner's words win: the mission is started."""
    h = build_harness()
    sid = h.new_session()
    said = h.say(sid, "YouTube'u aç.")
    assert said["resolved_intents"][-1]["intent"] == "mission_start"
    call = h.tool(sid, "c-1", "operator.app_open", {"application": "YouTube"})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["capability"] == "operator.mission", body
    assert body["execution_status"] == "executed" and body.get("error_class") is None
    assert "Başlıyorum" in body["speech"] and "youtube.com" in body["speech"]
    with h.factory() as db:
        rows = list(db.scalars(select(OperatorMissionRow)))
        assert len(rows) == 1 and rows[0].goal == "YouTube'u aç."


def test_a_yes_to_a_mission_that_is_already_moving_is_a_calm_no_op() -> None:
    """Production 2026-09-19 09:40:01: the model "approved" a mission already moving; the
    owner heard "Görev sizin cevabınızı bekliyor" - untrue. Nothing waited; nothing changes."""
    from app.voice.realtime_sessions import tools_mission

    h = build_harness()
    with h.factory() as db:
        row = mission_service.start_mission_db(db, text="Not Defteri'ni aç ve merhaba yaz")
        before = mission_service.get_mission(db, row.id).status
        assert before == "planned"

        out = tools_mission.operator_mission(_mission_ctx(db, said=None), {"action": "approve"})

        assert out["execution_status"] == "noop", out
        assert out.get("error_class") is None
        assert "zaten yürüyor" in out["speech"]
        assert mission_service.get_mission(db, row.id).status == before


def test_a_browser_named_in_the_locative_is_that_browser_not_an_implicit_chrome() -> None:
    """ "Edge'de YouTube'u aç" (production 2026-09-19): the owner named Edge the way they
    name Chrome in "Chrome'da YouTube'u aç" - so Edge is opened (not implicitly Chrome), and
    its window is what the navigate drives."""
    m = plan_mission("Edge'de YouTube'u aç")
    assert [(s.kind, s.args.get("application"), s.args.get("implicit")) for s in m.steps] == [
        ("app_open", "msedge", None),
        ("navigate", None, None),
    ], m.as_dict()
    assert m.steps[1].args.get("browser") == "msedge.exe"


# ------------------------------------- production 2026-09-19 14:46: "arama kısmına ... yaz"


def _tab_in_front(title_before: str, lands_on: str) -> FakeDeviceAction:
    """The owner's Chrome with ``title_before`` in front; the title becomes ``lands_on`` on
    the second look after Enter, as a page load does."""
    chrome = {**CHROME, "title": title_before}
    state = {"entered": False, "looks": 0}

    def current(payload: dict[str, Any]) -> DeviceRunResult:
        if state["entered"]:
            state["looks"] += 1
        title = lands_on if state["entered"] and state["looks"] >= 2 else title_before
        return ok(window={**chrome, "title": title})

    def key(payload: dict[str, Any]) -> DeviceRunResult:
        if payload.get("key") == "enter":
            state["entered"] = True
        return ok(
            key=payload.get("key"),
            window_id=payload.get("window_id"),
            observed=_observed(payload, chrome),
        )

    return FakeDeviceAction(
        results={
            "window.current": current,
            "window.list": ok(windows=[dict(chrome)]),
            "window.activate": lambda p: ok(
                window={**chrome, "window_id": str(p.get("window_id"))}
            ),
            "browser.session_open": DeviceRunResult(False, "dependency_unavailable", "no CDP"),
            "keyboard.shortcut": lambda p: ok(
                keys=p.get("keys"), window_id=p.get("window_id"), observed=_observed(p, chrome)
            ),
            "keyboard.type": lambda p: ok(
                typed_chars=len(str(p.get("text"))),
                window_id=p.get("window_id"),
                observed=_observed(p, chrome),
            ),
            "keyboard.key": key,
        }
    )


@pytest.mark.parametrize(
    ("said", "expected_args"),
    [
        (
            "Şu anki YouTube sekmesinde arama kısmına Tosun Paşa yaz",
            {"query": "Tosun Paşa", "site": "youtube"},
        ),
        ("Arama kısmına Tosun Paşa'yı yaz", {"query": "Tosun Paşa"}),
        ("Aramaya Barış Manço yaz", {"query": "Barış Manço"}),
        ("Arama kutusuna kedi videoları yaz", {"query": "kedi videoları"}),
    ],
)
def test_the_search_box_named_as_a_place_is_a_query_never_typed_text(
    said: str, expected_args: dict[str, Any]
) -> None:
    """Production 2026-09-19 14:46: "şu anki youtube sekmesinde arama kısmına tosun paşa yaz"
    was TYPE_TEXT - the whole sentence went into the address bar. The search box named as a
    place makes the words a query on the open tab."""
    m = plan_mission(said)
    assert [s.kind for s in m.steps] == ["app_open", "site_search"], m.as_dict()
    assert m.steps[0].args.get("implicit") is True
    assert m.steps[1].args == expected_args


def test_a_site_search_lands_on_the_open_tabs_own_search(monkeypatch) -> None:
    from app.operator import task as task_module
    from tests.alarms_support import window_id as _wid

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    # A YouTube tab in front: YouTube's own results, reached through the address bar.
    device = _tab_in_front("(954) YouTube - Google Chrome", "tosun paşa - YouTube - Google Chrome")
    m = plan_mission("Arama kısmına Tosun Paşa yaz")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert device.payload_for("keyboard.type")["text"] == (
        "https://www.youtube.com/results?search_query=Tosun+Paşa"
    )
    assert device.payload_for("keyboard.shortcut")["window_id"] == _wid(2)
    # Any other tab: Google.
    device = _tab_in_front(
        "Yeni Sekme - Google Chrome", "Tosun Paşa - Google Arama - Google Chrome"
    )
    m = plan_mission("Arama kısmına Tosun Paşa yaz")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert (
        device.payload_for("keyboard.type")["text"] == "https://www.google.com/search?q=Tosun+Paşa"
    )


def test_the_asrs_straight_apostrophe_reads_like_the_curly_one() -> None:
    """The normaliser cuts the curly apostrophe ("youtube" + "da") and keeps the straight one
    glued ("youtube'da"); the production ASR writes the straight one. Every "YouTube'da X
    ara" was "none" to the router while the curly test sentences passed (2026-09-19)."""
    straight = plan_mission("YouTube'da Tosun Paşa ara")
    curly = plan_mission("YouTube’da Tosun Paşa ara")
    assert [(s.kind, s.args) for s in straight.steps] == [(s.kind, s.args) for s in curly.steps]
    assert straight.steps[-1].kind == "navigate"
    assert straight.steps[-1].args == {
        "url": "https://www.youtube.com/results?search_query=Tosun+Paşa",
        "search": True,
    }


@pytest.mark.parametrize(
    "said",
    [
        "Şu anki YouTube sekmesinde Tosun Paşa videosunu aç",
        "Şu anki sekmedeki Tosun Paşa videosunu aç",
        "Bu sekmede görünen Tosun Paşa videosuna tıkla",
        "YouTube'daki Tosun Paşa videosunu aç",  # the ASR's glued apostrophe on the site
    ],
)
def test_the_tab_and_the_site_place_a_video_and_are_never_its_name(said: str) -> None:
    m = plan_mission(said)
    assert [s.kind for s in m.steps] == ["click_text"], m.as_dict()
    assert m.steps[0].args["name"] == "Tosun Paşa"


def test_the_router_hands_the_search_box_sentence_to_the_planner() -> None:
    h = build_harness()
    for said in (
        "Şu anki YouTube sekmesinde arama kısmına Tosun Paşa yaz.",
        "Arama kısmına Tosun Paşa yaz.",
        "YouTube'da Tosun Paşa ara.",
    ):
        sid = h.new_session()
        out = h.say(sid, said)
        assert out["resolved_intents"][-1]["intent"] == "mission_start", (
            said,
            out["resolved_intents"],
        )


@pytest.mark.parametrize(
    ("said", "name"),
    [
        # Production 2026-09-19 16:48, as Chrome's recogniser wrote them ("şu an", not "şu anki"):
        (
            "şu an sekmedeki Pakistan video Olimpiyatları videosunu aç",
            "Pakistan video Olimpiyatları",
        ),
        (
            "şu an sekmedeki rastgele ürünler toplu paket açılışı videosunu aç",
            "rastgele ürünler toplu paket açılışı",
        ),
        ("şuan ekrandaki Tosun Paşa videosuna tıkla", "Tosun Paşa"),
    ],
)
def test_a_title_keeps_its_own_video_word_and_loses_the_recognisers_filler(
    said: str, name: str
) -> None:
    """The picture was asked for "an Pakistan": "an" is how Chrome writes "şu anki", and the
    name was cut at the title's OWN "video" instead of at the sentence's closing "videosunu
    aç"."""
    m = plan_mission(said)
    assert [s.kind for s in m.steps] == ["click_text"], m.as_dict()
    assert m.steps[0].args["name"] == name


# ------------------------------------ owner 2026-09-19: "videoyu durdur / başlat / başa al"


def _keys_pressed(device: Any) -> list[str]:
    return [str(c["payload"].get("key")) for c in device.calls if c["capability"] == "keyboard.key"]


def test_play_is_the_pages_own_key_first_and_costs_no_picture(monkeypatch) -> None:
    """ "Videoyu başlat": the page's play key, proven by MOTION - no paid look, no click."""
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _paused_player(starts_playing=False, key_starts=True)
    vision = FakeVisionProvider(location=(96, 54))
    m = plan_mission("Videoyu başlat")
    run_mission(m, MissionPorts(device=device, vision=vision))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert _keys_pressed(device) == ["k"]
    assert "pointer.click" not in device.capabilities_called()
    assert vision.targets == []
    assert device.video_state["playing"] is True


def test_a_video_already_playing_is_not_toggled_off_by_play(monkeypatch) -> None:
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _paused_player(starts_playing=False, key_starts=True, playing=True)
    m = plan_mission("Videoyu başlat")
    run_mission(m, MissionPorts(device=device, vision=FakeVisionProvider(location=(1, 1))))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert _keys_pressed(device) == [] and device.video_state["playing"] is True


def test_pause_stops_the_picture_and_is_proven_by_stillness(monkeypatch) -> None:
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _paused_player(starts_playing=False, key_starts=True, playing=True)
    m = plan_mission("Videoyu durdur")
    assert [(s.kind, s.args) for s in m.steps] == [("video_control", {"action": "pause"})]
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert _keys_pressed(device) == ["k"] and device.video_state["playing"] is False
    # ...and a video that is already still is left alone.
    still = _paused_player(starts_playing=False, key_starts=True, playing=False)
    m2 = plan_mission("Videoyu duraklat")
    run_mission(m2, MissionPorts(device=still))
    assert m2.status == MISSION_SUCCEEDED and _keys_pressed(still) == []


def test_a_pause_that_did_not_stop_the_picture_is_not_reported_as_stopped(monkeypatch) -> None:
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _paused_player(starts_playing=False, key_starts=False, playing=True)
    m = plan_mission("Videoyu durdur")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_PAUSED
    assert m.steps[0].error_class == "postcondition_failed"


def test_from_the_start_is_the_zero_key(monkeypatch) -> None:
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _paused_player(starts_playing=False, playing=True)
    m = plan_mission("Videoyu başa al")
    assert [(s.kind, s.args) for s in m.steps] == [("video_control", {"action": "restart"})]
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert _keys_pressed(device) == ["0"]


def test_an_agent_that_refuses_character_keys_gets_the_named_key_next(monkeypatch) -> None:
    """Until the device is updated "k" and "0" are validation errors; Space and Home do the
    same while the player has the focus."""
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    device = _paused_player(starts_playing=False, key_starts=True, playing=True, char_keys=False)
    m = plan_mission("Videoyu durdur")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert _keys_pressed(device) == ["k", "space"]


def test_the_video_is_where_youtube_is_not_where_the_owner_speaks_from(monkeypatch) -> None:
    """Owner, 2026-09-19: "Videoyu başlat dediğimde 1. ekrandaki videoyu başlatıyorum diyor
    ama video 2. ekranda". The window in front is the Agent's own page; the key goes to the
    window that is on YouTube."""
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    agent_page = {
        **YOUTUBE,
        "window_id": window_id(11),
        "title": "PersonalAgentOS Core - Google Chrome",
        "foreground": True,
    }
    video = {**YOUTUBE, "window_id": window_id(12), "foreground": False}
    device = _paused_player(starts_playing=False, key_starts=True, windows=[agent_page, video])
    m = plan_mission("Videoyu başlat")
    run_mission(m, MissionPorts(device=device, vision=FakeVisionProvider(location=(1, 1))))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    sent = [c["payload"] for c in device.calls if c["capability"] == "keyboard.key"]
    assert [p["window_id"] for p in sent] == [window_id(12)]


def test_a_tab_is_reached_by_its_name_through_the_browsers_own_tab_search(monkeypatch) -> None:
    """ "Tosun Paşa sekmesine geç" used to ignore the name and go to the NEXT tab."""
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    m = plan_mission("Tosun Paşa sekmesine geç")
    assert [(s.kind, s.args) for s in m.steps] == [("tab_switch", {"name": "Tosun Paşa"})]
    assert plan_mission("Yan sekmeye geç").steps[0].args == {"direction": "next"}
    assert plan_mission("Üçüncü sekmeye geç").steps[0].args == {"index": 3}

    state = {"title": CHROME["title"]}

    def key(payload: dict[str, Any]) -> DeviceRunResult:
        if payload.get("key") == "enter":
            state["title"] = "(954) Tosun Paşa - RESTORASYONLU 4K FULL - YouTube - Google Chrome"
        return ok(key=payload.get("key"), observed=_observed(payload, CHROME))

    device = FakeDeviceAction(
        results={
            "window.current": lambda _p: ok(window={**CHROME, "title": state["title"]}),
            "window.list": ok(windows=[dict(CHROME)]),
            "window.activate": lambda p: ok(
                window={**CHROME, "window_id": str(p.get("window_id"))}
            ),
            "keyboard.shortcut": lambda p: ok(keys=p.get("keys"), observed=_observed(p, CHROME)),
            "keyboard.type": lambda p: ok(
                typed_chars=len(str(p.get("text"))), observed=_observed(p, CHROME)
            ),
            "keyboard.key": key,
        }
    )
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert device.payload_for("keyboard.shortcut")["keys"] == ["ctrl", "shift", "a"]
    assert device.payload_for("keyboard.type")["text"] == "Tosun Paşa"


def test_between_two_ordinary_windows_the_one_on_youtube_is_the_videos(monkeypatch) -> None:
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    mail = {**YOUTUBE, "window_id": window_id(21), "title": "Gelen Kutusu - Gmail - Google Chrome"}
    video = {**YOUTUBE, "window_id": window_id(22), "foreground": False}
    device = _paused_player(starts_playing=False, key_starts=True, windows=[mail, video])
    m = plan_mission("Videoyu başlat")
    run_mission(m, MissionPorts(device=device, vision=FakeVisionProvider(location=(1, 1))))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    sent = [c["payload"]["window_id"] for c in device.calls if c["capability"] == "keyboard.key"]
    assert sent == [window_id(22)]


def test_a_tab_search_that_lands_on_another_tab_is_not_reported_as_that_tab(monkeypatch) -> None:
    from app.operator import task as task_module

    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)
    state = {"title": CHROME["title"]}

    def key(payload: dict[str, Any]) -> DeviceRunResult:
        if payload.get("key") == "enter":
            state["title"] = "Faaliyet Akışı | LinkedIn - Google Chrome"
        return ok(key=payload.get("key"), observed=_observed(payload, CHROME))

    device = FakeDeviceAction(
        results={
            "window.current": lambda _p: ok(window={**CHROME, "title": state["title"]}),
            "window.list": ok(windows=[dict(CHROME)]),
            "window.activate": lambda p: ok(
                window={**CHROME, "window_id": str(p.get("window_id"))}
            ),
            "keyboard.shortcut": lambda p: ok(keys=p.get("keys"), observed=_observed(p, CHROME)),
            "keyboard.type": lambda p: ok(
                typed_chars=len(str(p.get("text"))), observed=_observed(p, CHROME)
            ),
            "keyboard.key": key,
        }
    )
    m = plan_mission("Tosun Paşa sekmesine geç")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_PAUSED
    assert m.steps[0].error_class == "postcondition_failed"


# ------------------------------- 2026-09-19: typing into a web page, verified where it shows


def _typing_page(*, before: list[str], after: list[str]) -> Any:
    state = {"typed": False}

    def read(payload: dict[str, Any]) -> DeviceRunResult:
        texts = after if state["typed"] else before
        lines = [
            {"text": s, "x": 10, "y": 200 + 30 * i, "width": 300, "height": 22, "words": []}
            for i, s in enumerate(texts)
        ]
        return ok(width=1294, height=1407, scale=1, lines=lines, observed={"window": dict(CHROME)})

    def typed(payload: dict[str, Any]) -> DeviceRunResult:
        state["typed"] = True
        return ok(typed_chars=len(str(payload.get("text"))), observed=_observed(payload, CHROME))

    return FakeDeviceAction(
        results={
            "window.current": ok(window={**CHROME, "foreground": True}),
            "window.list": ok(windows=[dict(CHROME)]),
            "window.activate": lambda p: ok(
                window={**CHROME, "window_id": str(p.get("window_id")), "foreground": True}
            ),
            "screen.ocr": read,
            "keyboard.type": typed,
        }
    )


def test_typing_into_chrome_is_verified_by_ocr_not_by_a_tree_that_cannot_see_the_page() -> None:
    device = _typing_page(before=["YouTube", "Ara"], after=["YouTube", "Tosun Paşa"])
    m = plan_mission("Buraya Tosun Paşa yaz")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    called = device.capabilities_called()
    assert "ui.inspect" not in called
    assert called.count("screen.ocr") == 2 and called.index("screen.ocr") < called.index(
        "keyboard.type"
    )


def test_text_that_was_already_on_the_page_does_not_prove_the_typing() -> None:
    """A results page already shows the query; the count must GROW, not merely be there."""
    device = _typing_page(before=["tosun paşa - YouTube"], after=["tosun paşa - YouTube"])
    m = plan_mission("Buraya Tosun Paşa yaz")
    run_mission(m, MissionPorts(device=device))
    assert m.status == MISSION_PAUSED
    assert m.steps[0].error_class == "postcondition_failed"


@pytest.mark.parametrize(
    "said",
    [
        # The owner talking ABOUT a click, not asking for one (handoff 2026-09-19 #3):
        "Yanlış yere tıkladım",
        "Yanlış yere tıkladın",
        "Sen yanlış videoya tıklamışsın",
        "Videoyu ben başlattım",
        "Video kendi kendine oynatıyor",
        "Oraya tıklama",
        "Videoyu açtın ama yanlış video",
        "Videoyu başlattın",
        "Videoyu oynattın",
    ],
)
def test_a_remark_about_a_click_is_never_a_click_on_the_screen(said: str) -> None:
    """A stem match on "tıkla" read "tıkladım" as a command and searched the screen for
    "Yanlış yere". Past, progressive, perfect and negative forms are the owner remarking."""
    try:
        m = plan_mission(said)
    except MissionClarificationNeeded:
        return
    assert not any(s.kind in ("click_text", "video_play") for s in m.steps), m.as_dict()
    h = build_harness()
    out = h.say(h.new_session(), said)
    assert out["resolved_intents"][-1]["intent"] != "mission_start", (said, out)


@pytest.mark.parametrize(
    ("said", "name"),
    [
        ("Tarkan'a tıkla", "Tarkan"),
        ("Tarkan'a tıklar mısın", "Tarkan"),
        ("Tarkan'a tıklayabilir misin", "Tarkan"),
        ("Tarkan'a tıklasana", "Tarkan"),
        ("Ekranda Abone ol yazana tıklayın", "Abone ol"),
    ],
)
def test_every_way_of_asking_for_a_click_still_clicks(said: str, name: str) -> None:
    m = plan_mission(said)
    assert [s.kind for s in m.steps] == ["click_text"], m.as_dict()
    assert m.steps[0].args["name"] == name
