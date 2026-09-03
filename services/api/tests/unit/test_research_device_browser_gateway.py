"""DeviceBrowserGateway: exact browser.* payloads (BROWSER_CAPABILITIES.md §2-3)
over a scripted DeviceCommandClient — no real broker/WS/browser worker.
"""

import uuid

import pytest

from app.devices.commands import CommandExpired, CommandFailed, CommandSucceeded
from app.research.browser_gateway import (
    BrowserDispatchError,
    DeviceBrowserGateway,
    FetchQuery,
    fetch_idempotency_key,
)
from tests.device_command_support import FakeDeviceCommandClient

DEVICE_ID = uuid.uuid4()
TASK_ID = "task-abc"


def _gateway(client: FakeDeviceCommandClient) -> DeviceBrowserGateway:
    return DeviceBrowserGateway(client, device_id=DEVICE_ID, task_id=TASK_ID)


def test_session_open_uses_research_profile_read_navigate_policy() -> None:
    client = FakeDeviceCommandClient(default_outcome=CommandSucceeded({"created": True}))
    gw = _gateway(client)
    gw.ensure_session()
    call = client.calls[0]
    assert call.capability == "browser.session_open"
    assert call.payload["session_id"] == TASK_ID
    assert call.payload["profile"] == "research"
    assert call.payload["policy"]["allowed_risk_classes"] == ["READ", "NAVIGATE"]
    assert call.payload["channel"] == "chrome"


def test_session_open_is_only_dispatched_once() -> None:
    client = FakeDeviceCommandClient(default_outcome=CommandSucceeded({"created": True}))
    gw = _gateway(client)
    gw.ensure_session()
    gw.ensure_session()
    session_opens = [c for c in client.calls if c.capability == "browser.session_open"]
    assert len(session_opens) == 1


def test_search_payload_matches_contract() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded(
            {
                "results": [
                    {
                        "url": "https://a",
                        "title": "A",
                        "snippet": "s",
                        "published_hint": "2 gün önce",
                    }
                ]
            }
        )
    )
    gw = _gateway(client)
    hits = gw.search("yapay zeka ajanları", source_class="news", max_results=5)
    call = next(c for c in client.calls if c.capability == "browser.search")
    assert call.payload["query"] == "yapay zeka ajanları"
    assert call.payload["engine"] == "auto"
    assert call.payload["max_results"] == 5
    assert len(hits) == 1
    assert hits[0].url == "https://a"
    assert hits[0].published_hint == "2 gün önce"


def test_fetch_url_payload_and_idempotency_key() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded(
            {
                "url": "https://a",
                "final_url": "https://a",
                "title": "T",
                "excerpt": "metin",
                "text_chars": 100,
                "fetched_at": "2026-09-03T09:00:00Z",
                "extraction_method": "dom_text",
                "page_kind": "ok",
                "http_status": 200,
                "metadata": {"publisher": "A Yayın"},
                "injection_markers": 0,
            }
        )
    )
    gw = _gateway(client)
    record = gw.fetch_url("https://a", query="q", source_class="news")
    call = next(c for c in client.calls if c.capability == "browser.fetch_evidence")
    assert call.payload["url"] == "https://a"
    assert call.payload["query"] == "q"
    assert call.payload["source_class"] == "news"
    assert call.idempotency_key == fetch_idempotency_key(TASK_ID, "https://a", attempt=1)
    assert record.title == "T"
    assert record.publisher == "A Yayın"
    assert record.page_kind == "ok"
    assert record.injection_suspected is False


def test_fetch_url_flags_injection_markers() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded(
            {
                "url": "https://a",
                "excerpt": "x",
                "fetched_at": "2026-09-03T09:00:00Z",
                "extraction_method": "dom_text",
                "injection_markers": 3,
            }
        )
    )
    record = _gateway(client).fetch_url("https://a")
    assert record.injection_suspected is True


def test_fetch_url_failed_raises_browser_dispatch_error_with_retryable_flag() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandFailed("timeout", "navigation timed out", True)
    )
    with pytest.raises(BrowserDispatchError) as exc_info:
        _gateway(client).fetch_url("https://a")
    assert exc_info.value.error_class == "timeout"
    assert exc_info.value.retryable is True


def test_fetch_url_expired_raises_dispatch_error() -> None:
    client = FakeDeviceCommandClient(default_outcome=CommandExpired())
    with pytest.raises(BrowserDispatchError) as exc_info:
        _gateway(client).fetch_url("https://a")
    assert exc_info.value.error_class == "timeout"
    assert exc_info.value.retryable is True


def _factory_session_open_ok(**outcomes_by_capability):
    def factory(*, capability, **_kwargs):
        if capability == "browser.session_open":
            return CommandSucceeded({"created": True})
        return outcomes_by_capability[capability]

    return factory


def test_fetch_url_different_attempts_use_different_idempotency_keys() -> None:
    client = FakeDeviceCommandClient(
        factory=_factory_session_open_ok(**{"browser.fetch_evidence": CommandExpired()})
    )
    gw = _gateway(client)
    with pytest.raises(BrowserDispatchError):
        gw.fetch_url("https://a", attempt=1)
    with pytest.raises(BrowserDispatchError):
        gw.fetch_url("https://a", attempt=2)
    fetch_calls = [c for c in client.calls if c.capability == "browser.fetch_evidence"]
    assert len(fetch_calls) == 2
    assert fetch_calls[0].idempotency_key != fetch_calls[1].idempotency_key


def test_close_session_is_best_effort_never_raises() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandFailed("dependency_unavailable", "gone", True)
    )
    gw = _gateway(client)
    gw._session_opened = True  # simulate a session already open (as close_session_activity does)
    gw.close_session()  # must not raise even though browser.session_close itself fails


def test_close_session_noop_when_never_opened() -> None:
    client = FakeDeviceCommandClient()
    gw = _gateway(client)
    gw.close_session()
    assert client.calls == []


def test_fetch_evidence_protocol_composes_search_then_fetch() -> None:
    def factory(*, capability, payload, **_kwargs):
        if capability == "browser.session_open":
            return CommandSucceeded({"created": True})
        if capability == "browser.session_close":
            return CommandSucceeded({"closed": True})
        if capability == "browser.search":
            return CommandSucceeded({"results": [{"url": "https://a/1"}, {"url": "https://a/2"}]})
        return CommandSucceeded(
            {
                "url": payload["url"],
                "excerpt": "x",
                "fetched_at": "2026-09-03T09:00:00Z",
                "extraction_method": "dom_text",
            }
        )

    client = FakeDeviceCommandClient(factory=factory)
    gw = _gateway(client)
    records = gw.fetch_evidence([FetchQuery(query="q", source_class="news", max_results=2)])
    assert {r.url for r in records} == {"https://a/1", "https://a/2"}
    assert any(c.capability == "browser.session_close" for c in client.calls)
