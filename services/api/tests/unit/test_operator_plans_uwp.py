"""A Store/UWP application opened by alias is known by its title (owner trial 2026-09-30).

"Hesap makinesini aç" on MAIL: ``app.launch calc`` succeeded, ``window.current`` reported a
foreground window titled "Hesap Makinesi" under ``applicationframehost.exe`` (pid unlike the
launched one), and the task ended ``postcondition_failed`` - twice - because only
``systemsettings.exe`` was a known UWP-hosted image (B39 req 123). The titles now come from
the same allowlist contract the alias table is read from.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.operator import plans
from app.operator.task import new_task, run_task
from tests.alarms_support import FakeDeviceAction, ok, window_id


def _window(image: str, title: str, *, pid: int = 4242, foreground: bool = True) -> dict[str, Any]:
    return {
        "window_id": window_id(7),
        "pid": pid,
        "image": image,
        "title": title,
        "state": "normal",
        "foreground": foreground,
    }


def _open(application: str, executable: str, window: dict[str, Any]) -> str:
    device = FakeDeviceAction(
        results={
            "app.launch": ok(pid=7777, executable=executable),
            "window.current": ok(window=window),
        }
    )
    task = new_task(
        goal="t", plan_name="open_application", steps=plans.open_application(application)
    )
    run_task(task, device)
    return task.status


CALC_EXE = "C:\\Windows\\System32\\calc.exe"


def test_the_trials_calculator_window_satisfies_the_postcondition() -> None:
    frame = _window("applicationframehost.exe", "Hesap Makinesi")
    assert _open("calc", CALC_EXE, frame) == "succeeded"


def test_a_foreground_frame_host_window_with_an_unrelated_title_still_fails() -> None:
    frame = _window("applicationframehost.exe", "Ayarlar")
    assert _open("calc", CALC_EXE, frame) != "succeeded"


def test_a_background_calculator_frame_window_is_not_an_open_calculator() -> None:
    frame = _window("applicationframehost.exe", "Hesap Makinesi", foreground=False)
    assert _open("calc", CALC_EXE, frame) != "succeeded"


def test_settings_keeps_its_b39_behaviour() -> None:
    settings = "C:\\Windows\\ImmersiveControlPanel\\SystemSettings.exe"
    assert _open("settings", settings, _window("applicationframehost.exe", "Ayarlar")) == (
        "succeeded"
    )
    assert _open("settings", settings, _window("applicationframehost.exe", "Hesap Makinesi")) != (
        "succeeded"
    )


@pytest.mark.parametrize("title", ["Hesap Makinesi", "Adsız - Not Defteri"])
def test_a_classic_app_still_needs_its_own_image(title: str) -> None:
    notepad = "C:\\Windows\\System32\\notepad.exe"
    assert _open("notepad", notepad, _window("applicationframehost.exe", title)) != "succeeded"
    assert _open("notepad", notepad, _window("notepad.exe", title)) == "succeeded"
