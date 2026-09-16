"""B29 req 99-103, 105, 111, 116: UI Automation from the cloud, verified independently.

The device has answered ``ui.inspect`` / ``ui.invoke`` / ``ui.set_value`` / ``ui.select``
since M19 (rows 99-103: "Çağıran yok"). This file is about the callers this batch adds and
the one rule the batch is named for (req 111): **no action counts as a success until an
independent read says it happened.** An invoke whose element neither changed nor went
away is a failure; a set value is read back through the tree; a selection is read back
from the container. And the visual rung (req 105) is a provider interface: with a
provider it answers from a genuine capture, without one it says so.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.operator import adapters
from app.operator.capabilities import (
    CAPABILITY_INSPECT,
    CAPABILITY_SEE,
    CAPABILITY_UI,
    OPERATOR_CAPABILITIES,
    PLAN_BY_UI_ACTION,
    RECEIPT_BY_PLAN,
)
from app.operator.task import LEVEL_UI_AUTOMATION
from app.operator.vision import (
    ERROR_VISION_FAILED,
    FakeVisionProvider,
    OpenAIVisionProvider,
    VisionError,
    build_vision_provider,
)
from app.routines.dispatch import DeviceRunResult
from app.security import step_up
from app.voice.intents import Intent, resolve_intent
from app.voice.realtime_sessions.tools import ToolContext
from app.voice.realtime_sessions.tools_operator import operator_see
from tests.alarms_support import ONE_PIXEL_PNG_B64
from tests.alarms_support import window_id as window_id_for
from tests.unit.test_daily_intent_tools import _ctx as _plain_ctx
from tests.unit.test_operator_capability_regression import PLAN_STEPS
from tests.unit.test_operator_tools import _create, _focus_window, _say, _tool, _wired

REPO = Path(__file__).resolve().parents[4]

# ----------------------------------------------------------------- the vocabulary


def test_the_three_tools_are_declared_and_governed() -> None:
    assert {CAPABILITY_UI, CAPABILITY_INSPECT, CAPABILITY_SEE} <= set(OPERATOR_CAPABILITIES)
    for plan_name in PLAN_BY_UI_ACTION.values():
        assert RECEIPT_BY_PLAN[plan_name] == CAPABILITY_UI
    assert RECEIPT_BY_PLAN["ui_read"] == CAPABILITY_INSPECT
    for name in (CAPABILITY_UI, CAPABILITY_INSPECT, CAPABILITY_SEE):
        assert step_up.tier_of(name) == step_up.TIER_SENSITIVE, name


def test_every_plan_ends_in_a_verified_postcondition() -> None:
    """req 111, structurally: the LAST step of every plan the operator can build carries a
    postcondition, so no plan can end on "the device said ok" alone."""
    assert len(PLAN_STEPS) >= 20, "the plan table shrank"
    for plan_name, build in PLAN_STEPS.items():
        steps = build()
        assert steps, plan_name
        assert steps[-1].postcondition is not None, f"{plan_name} ends unverified"


def test_the_ui_query_keys_are_the_companions() -> None:
    """Both halves read each other: every query key the adapters emit is one the
    companion's ``ReadQuery`` reads."""
    source = (
        REPO
        / "devices/windows-agent/src/PagentOS.SessionCompanion/Operator/OperatorCapabilities.cs"
    ).read_text("utf-8")
    for key in adapters.QUERY_KEYS:
        assert f'"{key}"' in source, key
    for adapter in adapters.ADAPTERS:
        for query in [adapter.document_query or {}, *adapter.spoken_targets.values()]:
            assert set(query) <= set(adapters.QUERY_KEYS), (adapter.image, query)


# --------------------------------------------------------------------- adapters


def test_adapters_are_keyed_by_the_bare_image_and_fall_back_to_generic() -> None:
    assert adapters.adapter_for("C:\\Windows\\System32\\notepad.exe") is adapters.NOTEPAD
    assert adapters.adapter_for("NOTEPAD.EXE") is adapters.NOTEPAD
    assert adapters.adapter_for("unknownapp.exe") is adapters.GENERIC
    assert adapters.adapter_for(None) is adapters.GENERIC


def test_a_spoken_part_resolves_to_the_adapters_query_and_a_button_to_its_name() -> None:
    assert adapters.spoken_target_query(adapters.NOTEPAD, "belge") == {"control_type": "Edit"}
    assert adapters.spoken_target_query(adapters.NOTEPAD, "Metni") == {"control_type": "Edit"}
    assert adapters.spoken_target_query(adapters.GENERIC, "belge") is None
    assert adapters.button_query("Tamam") == {"name": "Tamam", "control_type": "Button"}
    assert adapters.dialog_button_names(adapters.NOTEPAD, "dont_save")[0] == "Kaydetme"


# ---------------------------------------------------------------------- intents


@pytest.mark.parametrize(
    ("text", "target"),
    [
        ("Tamam düğmesine tıkla.", "Tamam"),
        ("Kaydet düğmesine bas.", "Kaydet"),
        ("İptal butonuna tıkla.", "İptal"),
    ],
)
def test_a_named_button_resolves_with_its_name(text: str, target: str) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is Intent.UI_INVOKE
    assert resolved.ui_target == target
    assert resolved.capability == CAPABILITY_UI


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Ekrandaki metni oku.", Intent.UI_READ),
        ("Ne yazıyor?", Intent.UI_READ),
        ("Ekranda ne var?", Intent.SCREEN_DESCRIBE),
        ("Ekranı anlat.", Intent.SCREEN_DESCRIBE),
        ("Belgeyi oku.", Intent.DOCUMENT_READ),  # the document family keeps its noun
        ("Ne görüyorsun?", Intent.EXPLAIN),  # the eye's own question stays the explanation's
        ("Şu düğmeye bas.", Intent.NONE),  # names nothing a tree can find
        ("Enter'a bas.", Intent.OPERATOR_KEY),
        ("Ekranı kapat.", Intent.DISPLAY_OFF),
        ("Ekran durumu ne?", Intent.DISPLAY_QUERY),
    ],
)
def test_the_reads_and_their_neighbours(text: str, intent: Intent) -> None:
    assert resolve_intent(text).intent is intent, text


# ------------------------------------------------------------------ operator.ui


def test_a_named_button_is_invoked_through_the_tree_and_verified_by_its_disappearance() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Tamam düğmesine tıkla.")
    body = _tool(client, sid, "operator.ui", {"action": "invoke", "name": "Başka"})["result"]
    assert body["execution_status"] == "executed", body
    assert body["terminal_status"] == "verified"
    assert device.capabilities_called() == ["window.list", "window.activate", "ui.invoke"]
    # The owner's word wins over the model's name; the query is a Button by that name.
    assert device.payload_for("ui.invoke") == {
        "window_id": window_id_for(1),
        "name": "Tamam",
        "control_type": "Button",
    }
    server = body["observed_after"]["server"]
    assert server["interaction_level"] == LEVEL_UI_AUTOMATION
    assert server["query"] == {"name": "Tamam", "control_type": "Button"}
    assert body["speech"] == "Tamam düğmesine bastım efendim."


def test_an_invoke_whose_element_did_not_change_is_a_failure_not_a_success() -> None:
    """req 111. The device says "invoked" and the element is still there, unchanged: the
    button did nothing observable, and the owner is told so."""
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    element = {"automation_id": "", "name": "Tamam", "control_type": "Button", "enabled": True}
    device.results["ui.invoke"] = DeviceRunResult(
        True,
        result={
            "invoked": True,
            "method": "Invoke",
            "element": element,
            "window_id": window_id_for(1),
            "observed": {"element": dict(element), "element_present": True, "window": {}},
        },
    )
    sid = _create(client)
    _say(client, sid, "Tamam düğmesine tıkla.")
    body = _tool(client, sid, "operator.ui", {})["result"]
    assert body["execution_status"] == "failed"
    assert body["error_class"] == "postcondition_failed"
    assert body["terminal_status"] == "failed"
    assert body["speech"] == "Tamam düğmesine bastım ama bir sonuç göremedim efendim."


def test_an_expected_outcome_is_read_from_an_independent_source() -> None:
    """``expect=window_gone``: the dialog's own window must be absent from ``window.list``
    - a read the invoke did not write."""
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    row = {"window_id": window_id_for(1), "title": "Kaydet?", "foreground": True}

    # The desktop as the device reads it: the dialog is listed until the button was
    # invoked, and gone afterwards - the SAME window.list answers the resolver before
    # the plan and the verification after it.
    def _listing(_payload):
        invoked = any(call["capability"] == "ui.invoke" for call in device.calls)
        return DeviceRunResult(True, result={"windows": [] if invoked else [row]})

    device.results["window.list"] = _listing
    sid = _create(client)
    body = _tool(
        client,
        sid,
        "operator.ui",
        {"action": "invoke", "name": "Kaydetme", "expect": "window_gone"},
    )["result"]
    assert body["execution_status"] == "executed", body
    assert device.capabilities_called() == [
        "window.list",
        "window.activate",
        "ui.invoke",
        "window.list",
    ]


def test_an_expected_outcome_that_does_not_hold_fails_the_action() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    # The window is still listed after the invoke: the dialog did not close.
    device.results["window.list"] = DeviceRunResult(
        True,
        result={
            "windows": [{"window_id": window_id_for(1), "title": "Kaydet?", "foreground": True}]
        },
    )
    sid = _create(client)
    body = _tool(
        client, sid, "operator.ui", {"action": "invoke", "name": "Kaydet", "expect": "window_gone"}
    )["result"]
    assert body["execution_status"] == "failed"
    assert body["error_class"] == "postcondition_failed"
    assert body["observed_after"]["server"]["stopped_at"] == "ui_invoke:verify_window_gone"


def test_a_target_the_tree_does_not_have_is_named_in_the_refusal() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    device.results["ui.invoke"] = DeviceRunResult(
        False, "ui_target_not_found", "no element matches name=Yok in this window"
    )
    sid = _create(client)
    body = _tool(client, sid, "operator.ui", {"action": "invoke", "name": "Yok"})["result"]
    assert body["execution_status"] == "failed"
    assert body["error_class"] == "ui_target_not_found"
    assert body["speech"] == "Bunu pencerede bulamadım efendim."


def test_set_value_is_read_back_through_the_tree_and_carries_the_secret_flag() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    body = _tool(
        client, sid, "operator.ui", {"action": "set_value", "target": "belge", "value": "merhaba"}
    )["result"]
    # "belge" is not known to the GENERIC adapter (the fake window has no image), so it is
    # a plain name; the value is set, then read back through ui.inspect on the same query.
    assert body["execution_status"] == "executed", body
    assert device.capabilities_called() == [
        "window.list",
        "window.activate",
        "ui.set_value",
        "ui.inspect",
    ]
    payload = device.payload_for("ui.set_value")
    assert payload["value"] == "merhaba" and payload["secret"] is False
    assert body["speech"] == "Değeri yazdım efendim."


def test_set_value_refuses_a_secret_before_any_device_call() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    body = _tool(
        client,
        sid,
        "operator.ui",
        {"action": "set_value", "name": "Parola", "value": "şifrem 1234"},
    )["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "secret_refused"
    assert device.calls == []


def test_a_selection_is_read_back_from_the_container() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    device.results["ui.inspect"] = DeviceRunResult(
        True,
        result={
            "root": {
                "name": "Liste",
                "control_type": "List",
                "children": [
                    {"name": "Bir", "control_type": "ListItem", "selected": True},
                    {"name": "İki", "control_type": "ListItem", "selected": False},
                ],
            }
        },
    )
    sid = _create(client)
    body = _tool(
        client, sid, "operator.ui", {"action": "select", "name": "Liste", "item": "Bir"}
    )["result"]
    assert body["execution_status"] == "executed", body
    assert device.payload_for("ui.select") == {
        "window_id": window_id_for(1),
        "name": "Liste",
        "item": "Bir",
    }
    assert body["speech"] == "Bir seçildi efendim."


def test_no_target_is_a_question() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    call = _tool(client, sid, "operator.ui", {"action": "invoke"})
    assert call["status"] == "needs_clarification"
    assert call["result"]["speech"] == "Hangi düğme ya da alan?"
    assert device.calls == []


# ------------------------------------------------------------- operator.inspect


def test_reading_the_text_is_one_inspect_and_speaks_the_value() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    _say(client, sid, "Ekrandaki metni oku.")
    body = _tool(client, sid, "operator.inspect", {})["result"]
    assert body["execution_status"] == "executed", body
    assert device.capabilities_called() == ["window.list", "ui.inspect"]
    assert body["speech"] == "Şöyle yazıyor: merhaba"
    assert body["text"] == "merhaba"


def test_inspecting_without_a_target_names_the_windows_controls() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)
    body = _tool(client, sid, "operator.inspect", {})["result"]
    assert body["execution_status"] == "executed", body
    assert body["node_count"] == 2
    assert "Metin Düzenleyici" in body["speech"]
    assert body["root"]["name"] == "*Adsız - Not Defteri"


def test_the_adapters_document_query_is_used_for_a_known_application() -> None:
    """A Notepad window (the device says notepad.exe) and "metni oku": the adapter turns
    the word into the Edit control's query, not a name lookup."""
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    device.results["window.list"] = DeviceRunResult(
        True,
        result={
            "windows": [
                {
                    "window_id": window_id_for(1),
                    "title": "Adsız - Not Defteri",
                    "image": "C:\\Windows\\System32\\notepad.exe",
                    "foreground": True,
                }
            ]
        },
    )
    sid = _create(client)
    _say(client, sid, "Ekrandaki metni oku.")
    body = _tool(client, sid, "operator.inspect", {})["result"]
    assert body["execution_status"] == "executed", body
    assert device.payload_for("ui.inspect") == {
        "window_id": window_id_for(1),
        "control_type": "Edit",
    }


# ----------------------------------------------------------------- operator.see


def _see_device(png_b64: str | None = ONE_PIXEL_PNG_B64):
    from tests.unit.test_daily_intent_tools import FakeDeviceAction

    result = {"width": 1, "height": 1}
    if png_b64:
        result["png_base64"] = png_b64
    return FakeDeviceAction(results={"screen.capture": DeviceRunResult(True, result=result)})


def test_see_captures_once_and_speaks_the_providers_answer() -> None:
    provider = FakeVisionProvider("Not Defteri açık; belgede 'merhaba' yazıyor.")
    device = _see_device()
    ctx = _plain_ctx(None, {"device_action": device, "vision_provider": provider})
    body = operator_see(ctx, {"question": "Hangi pencere açık?"})
    assert body["execution_status"] == "executed"
    assert body["terminal_status"] == "verified"
    assert body["speech"] == "Not Defteri açık; belgede 'merhaba' yazıyor."
    assert device.called() == ["screen.capture"]
    assert provider.questions == ["Hangi pencere açık?"]
    assert provider.image_sizes == [len(base64.b64decode(ONE_PIXEL_PNG_B64))]
    server = body["observed_after"]["server"]
    assert server["interaction_level"] == "visual" and server["provider"] == "fake"
    assert "png_base64" not in str(body)


def test_no_vision_provider_is_an_honest_refusal_and_no_capture() -> None:
    device = _see_device()
    body = operator_see(_plain_ctx(None, {"device_action": device}), {})
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "dependency_unavailable"
    assert "sağlayıcısı tanımlı değil" in body["speech"]
    assert device.calls == []


def test_a_provider_failure_is_a_failed_receipt_with_the_class() -> None:
    class Broken:
        name = "broken"

        def describe(self, png: bytes, *, question: str):
            raise VisionError(ERROR_VISION_FAILED, "boom")

    body = operator_see(
        _plain_ctx(None, {"device_action": _see_device(), "vision_provider": Broken()}), {}
    )
    assert body["execution_status"] == "failed"
    assert body["error_class"] == ERROR_VISION_FAILED


def test_the_openai_provider_sends_the_png_inline_and_never_logs_the_key() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "  Bir Not Defteri penceresi.  "}}]}
        )

    provider = OpenAIVisionProvider(
        api_key="sk-test-never-logged", model="gpt-4o-mini", transport=httpx.MockTransport(handler)
    )
    answer = provider.describe(b"\x89PNG\r\n\x1a\n", question="Ne var?")
    assert answer.text == "Bir Not Defteri penceresi."
    assert answer.provider == "openai" and answer.model == "gpt-4o-mini"
    assert seen["auth"] == "Bearer sk-test-never-logged"
    body = seen["body"]
    assert body["model"] == "gpt-4o-mini"
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Ne var?"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,iVBOR")


def test_the_openai_provider_turns_a_transport_error_into_a_named_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    provider = OpenAIVisionProvider(
        api_key="k", model="m", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(VisionError) as caught:
        provider.describe(b"x", question="q")
    assert caught.value.error_class == ERROR_VISION_FAILED


def test_build_vision_provider_needs_a_key_and_honours_none() -> None:
    assert build_vision_provider(Settings(_env_file=None)) is None
    with_key = build_vision_provider(Settings(_env_file=None, openai_api_key="sk-x"))
    assert isinstance(with_key, OpenAIVisionProvider)
    off = build_vision_provider(
        Settings(_env_file=None, openai_api_key="sk-x", vision_provider="none")
    )
    assert off is None


def test_the_tool_context_type_is_what_main_wires() -> None:
    """``create_app`` registers ``vision_provider`` on the live sources; the tool reads
    exactly that key."""
    source = (REPO / "services/api/app/main.py").read_text("utf-8")
    # B43 (req 498): ONE provider, built once and handed to both readers of it - the
    # creative semantic check and the voice live sources - never two providers that could
    # disagree about what an image shows.
    assert "vision_provider = build_vision_provider(settings)" in source
    assert source.count("vision_provider=vision_provider,") == 2
    assert isinstance(_plain_ctx(None), ToolContext)
