"""ADR-0197 (owner addition 3, 2026-09-21): "Dünya gözünü aç."

The page is the Cloud Core's aux workload; the tool opens it in the owner's own browser
through the REAL relay against the fake device: session on the owner-attached profile, a
NEW tab, navigate, session closed - the tab stays. An unconfigured URL is said aloud.
"""

from __future__ import annotations

import pytest

from app.routines.dispatch import DeviceRunResult
from app.voice.intents import Intent, resolve_intent
from app.voice.realtime_sessions import tools_godseye
from tests.unit.test_operator_tools import _create, _say, _tool, _wired


@pytest.mark.parametrize(
    "text",
    [
        "Dünya gözünü aç.",
        "dünya gözünü aç",
        "God's eye view'ı aç.",
        "Gods eye'ı aç.",
        "Tanrının gözünü aç.",
        "Dünya gözü aç.",
    ],
)
def test_the_owners_names_for_the_page_open_it(text: str) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is Intent.GODS_EYE_OPEN, resolved
    assert resolved.capability == "godseye.open"


@pytest.mark.parametrize(
    "text", ["Gözünü aç.", "Dünya haritasını aç.", "Chrome'u aç.", "Dünya gözü."]
)
def test_what_does_not_open_it(text: str) -> None:
    assert resolve_intent(text).intent is not Intent.GODS_EYE_OPEN


def test_the_page_opens_in_a_new_tab_of_the_owners_browser_and_the_session_is_closed() -> None:
    client, factory, device, _operator = _wired()
    device.results["browser.tab_new"] = DeviceRunResult(True, result={"tab_id": "t-2"})
    device.results["browser.navigate"] = DeviceRunResult(
        True, result={"url": "http://pagentos-core:4173/"}
    )
    sid = _create(client)
    answer = _say(client, sid, "Dünya gözünü aç.")
    assert answer["resolved_intents"][-1]["tool"] == "godseye.open"
    call = _tool(client, sid, "godseye.open", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["status"] == "opened" and body["url"] == "http://pagentos-core:4173/"
    assert body["speech"] == tools_godseye.SPEECH_OPENED_TR
    assert device.capabilities_called() == [
        "browser.session_open",
        "browser.tab_new",
        "browser.navigate",
        "browser.session_close",
    ]
    opened = device.payload_for("browser.session_open")
    assert opened["profile"] == "owner" and opened["policy"]["visible"] is True
    assert opened["policy"]["allowed_risk_classes"] == ["READ", "NAVIGATE"]
    assert device.payload_for("browser.navigate")["url"] == "http://pagentos-core:4173/"


def test_no_browser_is_said_not_worked_around() -> None:
    client, factory, device, _operator = _wired()
    device.results["browser.session_open"] = DeviceRunResult(
        False, "capability_missing", "no browser enrolled for attach"
    )
    sid = _create(client)
    _say(client, sid, "Dünya gözünü aç.")
    body = _tool(client, sid, "godseye.open", {})["result"]
    assert body["status"] == "refused" and body["reason"] == "browser_unavailable"
    assert body["speech"] == tools_godseye.SPEECH_NO_BROWSER_TR
    assert device.capabilities_called() == ["browser.session_open"]


def test_a_failed_navigation_still_closes_the_session() -> None:
    client, factory, device, _operator = _wired()
    device.results["browser.navigate"] = DeviceRunResult(False, "timeout", "page did not load")
    sid = _create(client)
    _say(client, sid, "Dünya gözünü aç.")
    body = _tool(client, sid, "godseye.open", {})["result"]
    assert body["status"] == "refused" and body["reason"] == "navigate_failed"
    assert device.capabilities_called()[-1] == "browser.session_close"


def test_an_unconfigured_page_is_said_aloud(monkeypatch) -> None:
    client, factory, device, _operator = _wired()
    settings = tools_godseye.get_settings()
    monkeypatch.setattr(
        tools_godseye, "get_settings", lambda: settings.model_copy(update={"gods_eye_url": ""})
    )
    sid = _create(client)
    _say(client, sid, "Dünya gözünü aç.")
    body = _tool(client, sid, "godseye.open", {})["result"]
    assert body["status"] == "refused" and body["reason"] == "not_deployed"
    assert body["speech"] == tools_godseye.SPEECH_NOT_DEPLOYED_TR
    assert device.capabilities_called() == []
