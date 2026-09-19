"""OwnerBrowserGateway (ADR-0177): exact capability sequence against a scripted
DeviceCommandClient — no real broker/WS/Chrome/OCR.
"""

from __future__ import annotations

import uuid

import pytest

from app.devices.commands import CommandFailed, CommandSucceeded
from app.research import destination
from app.research.browser_gateway import BrowserDispatchError
from app.research.owner_browser_gateway import (
    OWNER_BROWSER_FALLBACK_ERROR_CLASSES,
    OwnerBrowserGateway,
)
from tests.device_command_support import FakeDeviceCommandClient

DEVICE_ID = uuid.uuid4()
TASK_ID = "task-owner"
PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _permissive_destination(monkeypatch):
    monkeypatch.setattr(destination, "resolve_hostname", lambda host: [PUBLIC_IP])


def _gateway(client: FakeDeviceCommandClient, **kwargs) -> OwnerBrowserGateway:
    return OwnerBrowserGateway(
        client, device_id=DEVICE_ID, task_id=TASK_ID, sleep_fn=lambda s: None, **kwargs
    )


def _line(text: str, x: int, y: int) -> dict:
    return {"text": text, "x": x, "y": y, "width": 200, "height": 20, "words": []}


ONE_WINDOW = {
    "windows": [
        {"window_id": "w1", "image": "chrome.exe", "title": "Yeni Sekme - Google Chrome",
         "foreground": True}
    ]
}


def _router(handlers: dict) -> callable:
    def factory(*, capability, payload, **_kwargs):
        handler = handlers.get(capability)
        if handler is None:
            raise AssertionError(f"unexpected capability {capability!r}")
        return handler(payload)

    return factory


def _sequential(*outcomes):
    """Returns a new outcome each call, repeating the last one once exhausted."""
    calls = {"n": 0}

    def handler(_payload):
        i = min(calls["n"], len(outcomes) - 1)
        calls["n"] += 1
        return outcomes[i]

    return handler


# ------------------------------------------------------------------- happy path


def test_happy_path_capability_sequence_and_tab_lifecycle() -> None:
    """window.list, window.activate, Ctrl+T, type, Enter, window.current(x2 - blank
    then real), screen.ocr, pagedown, screen.ocr(no new lines), Ctrl+W."""
    window_current = _sequential(
        CommandSucceeded({"window": {"window_id": "w1", "title": "Yeni Sekme - Google Chrome"}}),
        CommandSucceeded({"window": {"window_id": "w1", "title": "Örnek Sayfa - Google Chrome"}}),
    )
    ocr = _sequential(
        CommandSucceeded({"height": 1000, "lines": [_line("Gövde metni bir.", 100, 500)]}),
        CommandSucceeded({"height": 1000, "lines": [_line("Gövde metni bir.", 100, 500)]}),
    )
    client = FakeDeviceCommandClient(
        factory=_router(
            {
                "window.list": lambda _p: CommandSucceeded(ONE_WINDOW),
                "window.activate": lambda _p: CommandSucceeded({}),
                "keyboard.shortcut": lambda _p: CommandSucceeded({}),
                "keyboard.type": lambda _p: CommandSucceeded({"typed_chars": 10}),
                "keyboard.key": lambda _p: CommandSucceeded({}),
                "window.current": window_current,
                "screen.ocr": ocr,
            }
        )
    )
    record = _gateway(client).fetch_url("https://a", query="q", source_class="news")

    caps_in_order = [c.capability for c in client.calls]
    assert caps_in_order[0] == "window.list"
    assert caps_in_order[1] == "window.activate"
    # Ctrl+T (new tab)
    tab_new_call = next(c for c in client.calls if c.payload.get("keys") == ["ctrl", "t"])
    assert caps_in_order.index(tab_new_call.capability) <= caps_in_order.index("keyboard.type")
    assert any(
        c.capability == "keyboard.type" and c.payload["text"] == "https://a" for c in client.calls
    )
    assert any(
        c.capability == "keyboard.key" and c.payload["key"] == "enter" for c in client.calls
    )
    assert caps_in_order.count("window.current") == 2
    assert caps_in_order.count("screen.ocr") == 2
    # Ctrl+W (tab closed) is the LAST call.
    assert client.calls[-1].payload.get("keys") == ["ctrl", "w"]

    assert record.title == "Örnek Sayfa"
    assert record.excerpt == "Gövde metni bir."
    assert record.extraction_method == "owner_browser_ocr"
    assert record.url == "https://a"
    assert record.final_url == "https://a"
    assert record.device_id == str(DEVICE_ID)
    assert record.injection_suspected is False


# ------------------------------------------------------------------------ merge


def test_merge_drops_overlapping_lines_and_top_chrome_band() -> None:
    """A line in the top 12% of the window is chrome, never content. Consecutive
    screens overlap; a line already seen (by normalised text) is not duplicated."""
    ocr = _sequential(
        CommandSucceeded(
            {
                "height": 1000,
                "lines": [
                    _line("youtube.com adres cubugu", 50, 20),  # y=20 < 120 -> chrome band
                    _line("Birinci paragraf metni burada.", 50, 500),
                    _line("Ikinci paragraf metni burada.", 50, 600),
                ],
            }
        ),
        CommandSucceeded(
            {
                "height": 1000,
                "lines": [
                    _line("Ikinci paragraf metni burada.", 50, 200),  # overlap, dropped
                    _line("Ucuncu paragraf metni burada.", 50, 600),  # new
                ],
            }
        ),
        CommandSucceeded({"height": 1000, "lines": []}),  # adds nothing -> stop
    )
    client = FakeDeviceCommandClient(
        factory=_router(
            {
                "window.list": lambda _p: CommandSucceeded(ONE_WINDOW),
                "window.activate": lambda _p: CommandSucceeded({}),
                "keyboard.shortcut": lambda _p: CommandSucceeded({}),
                "keyboard.type": lambda _p: CommandSucceeded({}),
                "keyboard.key": lambda _p: CommandSucceeded({}),
                "window.current": lambda _p: CommandSucceeded(
                    {"window": {"window_id": "w1", "title": "Sayfa - Google Chrome"}}
                ),
                "screen.ocr": ocr,
            }
        )
    )
    record = _gateway(client).fetch_url("https://a")

    assert "adres cubugu" not in record.excerpt  # top band never content
    lines = record.excerpt.split("\n")
    assert lines.count("Ikinci paragraf metni burada.") == 1  # de-duplicated
    assert "Birinci paragraf metni burada." in lines
    assert "Ucuncu paragraf metni burada." in lines

    ocr_calls = [c for c in client.calls if c.capability == "screen.ocr"]
    assert len(ocr_calls) == 3  # stopped once a screen (the 3rd) added nothing new
    pagedown_calls = [
        c
        for c in client.calls
        if c.capability == "keyboard.key" and c.payload.get("key") == "pagedown"
    ]
    assert len(pagedown_calls) == 2  # one between screen 1->2, one between 2->3; none after


def test_stops_at_max_screens_without_an_extra_pagedown() -> None:
    ocr_result = CommandSucceeded(
        {"height": 1000, "lines": [_line("Her seferinde farkli bir satir " * 1, 50, 500)]}
    )

    calls_seen: list[int] = []

    def ocr_handler(_payload):
        calls_seen.append(1)
        # A unique line every call so "added" is never zero (never stops early).
        return CommandSucceeded(
            {"height": 1000, "lines": [_line(f"Benzersiz satir {len(calls_seen)}", 50, 500)]}
        )

    client = FakeDeviceCommandClient(
        factory=_router(
            {
                "window.list": lambda _p: CommandSucceeded(ONE_WINDOW),
                "window.activate": lambda _p: CommandSucceeded({}),
                "keyboard.shortcut": lambda _p: CommandSucceeded({}),
                "keyboard.type": lambda _p: CommandSucceeded({}),
                "keyboard.key": lambda _p: CommandSucceeded({}),
                "window.current": lambda _p: CommandSucceeded(
                    {"window": {"window_id": "w1", "title": "Sayfa - Google Chrome"}}
                ),
                "screen.ocr": ocr_handler,
            }
        )
    )
    _gateway(client, max_screens=4).fetch_url("https://a")
    ocr_calls = [c for c in client.calls if c.capability == "screen.ocr"]
    pagedown_calls = [
        c
        for c in client.calls
        if c.capability == "keyboard.key" and c.payload.get("key") == "pagedown"
    ]
    assert len(ocr_calls) == 4
    assert len(pagedown_calls) == 3  # never a pagedown after the LAST screen is read
    del ocr_result


# --------------------------------------------------------- tab closed on failure


def test_tab_closed_when_ocr_fails_midway() -> None:
    ocr = _sequential(
        CommandSucceeded({"height": 1000, "lines": [_line("Ilk ekran metni.", 50, 500)]}),
        CommandFailed("capability_missing", "screen.ocr not available", False),
    )
    client = FakeDeviceCommandClient(
        factory=_router(
            {
                "window.list": lambda _p: CommandSucceeded(ONE_WINDOW),
                "window.activate": lambda _p: CommandSucceeded({}),
                "keyboard.shortcut": lambda _p: CommandSucceeded({}),
                "keyboard.type": lambda _p: CommandSucceeded({}),
                "keyboard.key": lambda _p: CommandSucceeded({}),
                "window.current": lambda _p: CommandSucceeded(
                    {"window": {"window_id": "w1", "title": "Sayfa - Google Chrome"}}
                ),
                "screen.ocr": ocr,
            }
        )
    )
    with pytest.raises(BrowserDispatchError) as exc_info:
        _gateway(client).fetch_url("https://a")
    assert exc_info.value.error_class == "capability_missing"
    assert exc_info.value.error_class in OWNER_BROWSER_FALLBACK_ERROR_CLASSES

    # The tab this fetch opened is still closed even though the read failed.
    tab_close_calls = [c for c in client.calls if c.payload.get("keys") == ["ctrl", "w"]]
    assert len(tab_close_calls) == 1


# -------------------------------------------------------------- window selection


def test_no_chrome_window_raises_typed_dependency_unavailable_before_any_action() -> None:
    client = FakeDeviceCommandClient(
        factory=_router({"window.list": lambda _p: CommandSucceeded({"windows": []})})
    )
    with pytest.raises(BrowserDispatchError) as exc_info:
        _gateway(client).fetch_url("https://a")
    assert exc_info.value.error_class == "dependency_unavailable"
    assert exc_info.value.retryable is False
    assert exc_info.value.error_class in OWNER_BROWSER_FALLBACK_ERROR_CLASSES
    # Nothing beyond the window lookup was ever dispatched.
    assert [c.capability for c in client.calls] == ["window.list"]


def test_prefers_a_window_not_showing_the_agents_own_page() -> None:
    windows = {
        "windows": [
            {
                "window_id": "agent-window",
                "image": "chrome.exe",
                "title": "PersonalAgentOS - Panel - Google Chrome",
                "foreground": True,
            },
            {
                "window_id": "news-window",
                "image": "chrome.exe",
                "title": "Bir Haber Sitesi - Google Chrome",
                "foreground": False,
            },
        ]
    }
    activated: list[str] = []

    def activate(payload):
        activated.append(payload["window_id"])
        return CommandSucceeded({})

    client = FakeDeviceCommandClient(
        factory=_router(
            {
                "window.list": lambda _p: CommandSucceeded(windows),
                "window.activate": activate,
                "keyboard.shortcut": lambda _p: CommandSucceeded({}),
                "keyboard.type": lambda _p: CommandSucceeded({}),
                "keyboard.key": lambda _p: CommandSucceeded({}),
                "window.current": lambda _p: CommandSucceeded(
                    {"window": {"window_id": "news-window", "title": "Sayfa - Google Chrome"}}
                ),
                "screen.ocr": lambda _p: CommandSucceeded({"height": 1000, "lines": []}),
            }
        )
    )
    _gateway(client).fetch_url("https://a")
    assert activated == ["news-window"]


def test_falls_back_to_any_chrome_window_when_only_the_agents_own_is_open() -> None:
    windows = {
        "windows": [
            {
                "window_id": "agent-window",
                "image": "chrome.exe",
                "title": "PersonalAgentOS - Panel - Google Chrome",
                "foreground": True,
            }
        ]
    }
    activated: list[str] = []

    def activate(payload):
        activated.append(payload["window_id"])
        return CommandSucceeded({})

    client = FakeDeviceCommandClient(
        factory=_router(
            {
                "window.list": lambda _p: CommandSucceeded(windows),
                "window.activate": activate,
                "keyboard.shortcut": lambda _p: CommandSucceeded({}),
                "keyboard.type": lambda _p: CommandSucceeded({}),
                "keyboard.key": lambda _p: CommandSucceeded({}),
                "window.current": lambda _p: CommandSucceeded(
                    {"window": {"window_id": "agent-window", "title": "Sayfa - Google Chrome"}}
                ),
                "screen.ocr": lambda _p: CommandSucceeded({"height": 1000, "lines": []}),
            }
        )
    )
    _gateway(client).fetch_url("https://a")
    assert activated == ["agent-window"]


# --------------------------------------------------------------------- security


def test_fetch_url_refuses_a_url_that_resolves_to_a_private_address(monkeypatch) -> None:
    monkeypatch.setattr(destination, "resolve_hostname", lambda host: ["10.0.0.5"])
    client = FakeDeviceCommandClient(default_outcome=CommandSucceeded({}))
    with pytest.raises(BrowserDispatchError) as exc_info:
        _gateway(client).fetch_url("https://internal.example.com/a")
    assert exc_info.value.error_class == "security_scope_error"
    assert client.calls == []


def test_fetch_url_refuses_result_with_forbidden_key() -> None:
    client = FakeDeviceCommandClient(
        factory=_router(
            {
                "window.list": lambda _p: CommandSucceeded(
                    {**ONE_WINDOW, "cookie": "session=abc123"}
                ),
            }
        )
    )
    with pytest.raises(BrowserDispatchError) as exc_info:
        _gateway(client).fetch_url("https://a")
    assert exc_info.value.error_class == "security_scope_error"


# ----------------------------------------------------------------- page_kind


def test_page_kind_reflects_a_detected_interstitial() -> None:
    client = FakeDeviceCommandClient(
        factory=_router(
            {
                "window.list": lambda _p: CommandSucceeded(ONE_WINDOW),
                "window.activate": lambda _p: CommandSucceeded({}),
                "keyboard.shortcut": lambda _p: CommandSucceeded({}),
                "keyboard.type": lambda _p: CommandSucceeded({}),
                "keyboard.key": lambda _p: CommandSucceeded({}),
                "window.current": lambda _p: CommandSucceeded(
                    {"window": {"window_id": "w1", "title": "Prove you are human"}}
                ),
                "screen.ocr": lambda _p: CommandSucceeded(
                    {"height": 1000, "lines": [_line("Prove you are human", 50, 500)]}
                ),
            }
        )
    )
    record = _gateway(client).fetch_url("https://a")
    assert record.page_kind == "captcha"
