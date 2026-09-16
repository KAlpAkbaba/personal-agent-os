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


def test_the_browser_step_verifies_the_host_twice_and_falls_back_to_its_own_profile() -> None:
    device = _chrome_device(already_open=True, navigate_url="https://www.google.com/")
    device.results["browser.session_open"] = lambda p: (
        DeviceRunResult(False, "capability_missing", "no enrollment")
        if p.get("profile") == "owner"
        else ok(session_id="s", profile="media")
    )
    m = plan_mission("Chrome'u aç ve YouTube'a gir")
    run_mission(m, MissionPorts(device=device))
    # The page reports google, not youtube: verification fails, re-observed, then the owner.
    assert m.status == MISSION_PAUSED and m.steps[1].error_class == "postcondition_failed"
    profiles = [
        c["payload"].get("profile")
        for c in device.calls
        if c["capability"] == "browser.session_open"
    ]
    assert profiles[:2] == ["owner", "media"]


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
    return _SCRIPT.pop(0) if _SCRIPT else {"status": "succeeded", "current_step": 2}


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
