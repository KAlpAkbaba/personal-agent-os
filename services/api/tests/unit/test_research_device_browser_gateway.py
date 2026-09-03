"""DeviceBrowserGateway: exact browser.* payloads (BROWSER_CAPABILITIES.md §2-3)
over a scripted DeviceCommandClient — no real broker/WS/browser worker.
"""

import uuid

import pytest

from app.devices.commands import CommandExpired, CommandFailed, CommandSucceeded
from app.research import destination
from app.research.browser_gateway import (
    BrowserDispatchError,
    DeviceBrowserGateway,
    FetchQuery,
    fetch_idempotency_key,
)
from tests.device_command_support import FakeDeviceCommandClient

DEVICE_ID = uuid.uuid4()
TASK_ID = "task-abc"
PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _permissive_destination(monkeypatch):
    """These payload/idempotency tests use placeholder hostnames
    ("https://a") that do not resolve on the real internet; the
    destination-policy boundary itself is exercised by the dedicated tests
    below (which override this fixture per-test) and unit-tested standalone
    in tests/unit/test_research_destination.py."""
    monkeypatch.setattr(destination, "resolve_hostname", lambda host: [PUBLIC_IP])


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
    assert record.device_id == str(DEVICE_ID)
    assert record.command_id  # populated from the command outcome (spec §3/§5)


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


# ---------------------------------------------- destination policy (HIGH-2)


def test_fetch_url_refuses_a_url_that_resolves_to_a_private_address(monkeypatch) -> None:
    monkeypatch.setattr(destination, "resolve_hostname", lambda host: ["10.0.0.5"])
    client = FakeDeviceCommandClient(default_outcome=CommandSucceeded({"created": True}))
    with pytest.raises(BrowserDispatchError) as exc_info:
        _gateway(client).fetch_url("https://internal.example.com/a")
    assert exc_info.value.error_class == "security_scope_error"
    assert exc_info.value.retryable is False
    # No command was ever dispatched for the refused URL.
    assert client.calls == []


def test_fetch_url_refuses_a_non_http_scheme_before_dispatch(monkeypatch) -> None:
    client = FakeDeviceCommandClient(default_outcome=CommandSucceeded({"created": True}))
    with pytest.raises(BrowserDispatchError) as exc_info:
        _gateway(client).fetch_url("javascript:alert(1)")
    assert exc_info.value.error_class == "security_scope_error"
    assert client.calls == []


# ------------------------------------------------ forbidden-key scan (HIGH-3)


def test_fetch_url_refuses_result_containing_a_forbidden_key() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded(
            {
                "url": "https://a",
                "excerpt": "x",
                "fetched_at": "2026-09-03T09:00:00Z",
                "extraction_method": "dom_text",
                "cookie": "session=abc123",
            }
        )
    )
    with pytest.raises(BrowserDispatchError) as exc_info:
        _gateway(client).fetch_url("https://a")
    assert exc_info.value.error_class == "security_scope_error"
    assert exc_info.value.retryable is False


def test_fetch_url_refuses_result_with_nested_forbidden_key() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded(
            {
                "url": "https://a",
                "excerpt": "x",
                "fetched_at": "2026-09-03T09:00:00Z",
                "extraction_method": "dom_text",
                "metadata": {"nested": {"x-api-key": "secret-value"}},
            }
        )
    )
    with pytest.raises(BrowserDispatchError) as exc_info:
        _gateway(client).fetch_url("https://a")
    assert exc_info.value.error_class == "security_scope_error"


def test_search_refuses_result_containing_a_forbidden_key() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded(
            {
                "results": [{"url": "https://a", "title": "A", "Set-Cookie": "abc"}],
            }
        )
    )
    with pytest.raises(BrowserDispatchError) as exc_info:
        _gateway(client).search("q")
    assert exc_info.value.error_class == "security_scope_error"


def test_search_clean_result_still_works() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded({"results": [{"url": "https://a", "title": "A"}]})
    )
    hits = _gateway(client).search("q")
    assert len(hits) == 1


# --------------------------------------------- process-wide session registry (spec §5a)


def test_session_open_dispatched_once_across_many_gateway_instances() -> None:
    """Every activity in a job constructs its OWN DeviceBrowserGateway, so the
    "one session_open per job per process" guarantee (spec §5a) has to live
    in a registry keyed (device_id, session_id), not on the instance."""
    client = FakeDeviceCommandClient(default_outcome=CommandSucceeded({"created": True}))
    _gateway(client).ensure_session()
    _gateway(client).search("q1")
    _gateway(client).search("q2")
    session_opens = [c for c in client.calls if c.capability == "browser.session_open"]
    assert len(session_opens) == 1


def test_session_open_dispatched_again_for_a_different_session_id() -> None:
    client = FakeDeviceCommandClient(default_outcome=CommandSucceeded({"created": True}))
    DeviceBrowserGateway(client, device_id=DEVICE_ID, task_id="task-abc").ensure_session()
    DeviceBrowserGateway(client, device_id=DEVICE_ID, task_id="task-xyz").ensure_session()
    session_opens = [c for c in client.calls if c.capability == "browser.session_open"]
    assert len(session_opens) == 2


def test_unknown_session_error_triggers_exactly_one_reopen_and_retry() -> None:
    session_open_calls: list[str] = []
    search_calls: list[str] = []

    def factory(*, capability, idempotency_key, **_kwargs):
        if capability == "browser.session_open":
            session_open_calls.append(idempotency_key)
            return CommandSucceeded({"created": True})
        if capability == "browser.search":
            search_calls.append(idempotency_key)
            if len(search_calls) == 1:
                return CommandFailed("validation_error", "unknown session: task-abc", False)
            return CommandSucceeded({"results": [{"url": "https://a"}]})
        raise AssertionError(capability)  # pragma: no cover

    client = FakeDeviceCommandClient(factory=factory)
    hits = _gateway(client).search("q")

    assert [h.url for h in hits] == ["https://a"]
    # Opened, invalidated by "unknown session", reopened exactly once.
    assert len(session_open_calls) == 2
    assert session_open_calls[0] != session_open_calls[1]
    # The retried search used a NEW idempotency key — replaying the failed
    # command's key would just replay the same terminal failure forever.
    assert len(search_calls) == 2
    assert search_calls[0] != search_calls[1]


def test_unknown_session_error_only_retried_once_then_raises() -> None:
    """A SECOND "unknown session" answer (even after the one reopen) must
    propagate rather than loop forever."""
    client = FakeDeviceCommandClient(
        factory=lambda *, capability, **_kwargs: (
            CommandSucceeded({"created": True})
            if capability == "browser.session_open"
            else CommandFailed("validation_error", "unknown session: task-abc", False)
        )
    )
    with pytest.raises(BrowserDispatchError) as exc_info:
        _gateway(client).search("q")
    assert exc_info.value.error_class == "validation_error"


def test_a_non_session_error_is_not_retried() -> None:
    calls = {"search": 0}

    def factory(*, capability, **_kwargs):
        if capability == "browser.session_open":
            return CommandSucceeded({"created": True})
        calls["search"] += 1
        return CommandFailed("timeout", "navigation timed out", True)

    client = FakeDeviceCommandClient(factory=factory)
    with pytest.raises(BrowserDispatchError) as exc_info:
        _gateway(client).search("q")
    assert exc_info.value.error_class == "timeout"
    assert calls["search"] == 1  # no unknown-session-style retry for an unrelated error


# ------------------------------------------------------- interstitial (spec §3a)


def test_search_defaults_interstitial_to_fallback() -> None:
    client = FakeDeviceCommandClient(default_outcome=CommandSucceeded({"results": []}))
    _gateway(client).search("q")
    call = next(c for c in client.calls if c.capability == "browser.search")
    assert call.payload["interstitial"] == "fallback"


def test_search_passes_interstitial_handoff_through() -> None:
    client = FakeDeviceCommandClient(default_outcome=CommandSucceeded({"results": []}))
    _gateway(client).search("q", interstitial="handoff")
    call = next(c for c in client.calls if c.capability == "browser.search")
    assert call.payload["interstitial"] == "handoff"


def test_search_evidence_carries_owner_handoff_fields() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded(
            {
                "schema_version": 2,
                "requested_provider": "google",
                "provider": None,
                "state": "waiting_for_owner_verification",
                "path": "handoff_pending",
                "page_kind": "captcha",
                "verification_url": "https://www.google.com/sorry/index",
                "results": [],
                "result_count": 0,
            }
        )
    )
    gw = _gateway(client)
    hits = gw.search("q", interstitial="handoff")
    assert hits == []
    evidence = gw.last_search_evidence
    assert evidence is not None
    assert evidence.state == "waiting_for_owner_verification"
    assert evidence.waiting_for_owner_verification is True
    assert evidence.path == "handoff_pending"
    assert evidence.page_kind == "captcha"
    assert evidence.verification_url == "https://www.google.com/sorry/index"
    assert evidence.as_dict()["verification_url"] == "https://www.google.com/sorry/index"


def test_search_evidence_defaults_state_ok_when_absent() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded({"results": [{"url": "https://a"}]})
    )
    gw = _gateway(client)
    gw.search("q")
    assert gw.last_search_evidence.state == "ok"
    assert gw.last_search_evidence.waiting_for_owner_verification is False
    assert gw.last_search_evidence.verification_url is None


# --------------------------------------------------------- await_verification


def test_await_verification_payload_matches_contract() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded(
            {"satisfied": True, "url": "https://www.google.com/search?q=x", "elapsed_ms": 4200}
        )
    )
    gw = _gateway(client)
    result = gw.await_verification(timeout_s=60.0, iteration=0)
    call = next(c for c in client.calls if c.capability == "browser.wait")
    assert call.payload["session_id"] == TASK_ID
    assert call.payload["for"] == "verification_cleared"
    assert call.payload["timeout_ms"] == 60000
    assert result == {
        "satisfied": True, "url": "https://www.google.com/search?q=x", "elapsed_ms": 4200,
    }


def test_await_verification_caps_timeout_at_60s() -> None:
    client = FakeDeviceCommandClient(default_outcome=CommandSucceeded({"satisfied": False}))
    _gateway(client).await_verification(timeout_s=600.0, iteration=0)
    call = next(c for c in client.calls if c.capability == "browser.wait")
    assert call.payload["timeout_ms"] == 60000


def test_await_verification_not_satisfied_on_timeout() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded(
            {"satisfied": False, "url": "https://x", "elapsed_ms": 60000}
        )
    )
    result = _gateway(client).await_verification(timeout_s=60.0, iteration=1)
    assert result["satisfied"] is False


def test_await_verification_iterations_use_distinct_idempotency_keys() -> None:
    client = FakeDeviceCommandClient(default_outcome=CommandSucceeded({"satisfied": False}))
    gw = _gateway(client)
    gw.await_verification(timeout_s=60.0, iteration=0)
    gw.await_verification(timeout_s=60.0, iteration=1)
    waits = [c for c in client.calls if c.capability == "browser.wait"]
    assert len(waits) == 2
    assert waits[0].idempotency_key != waits[1].idempotency_key


def test_await_verification_calls_heartbeat() -> None:
    client = FakeDeviceCommandClient(default_outcome=CommandSucceeded({"satisfied": True}))
    seen = {"count": 0}

    def heartbeat() -> None:
        seen["count"] += 1

    _gateway(client).await_verification(timeout_s=60.0, iteration=0, heartbeat=heartbeat)
    assert seen["count"] >= 1


# ---------------------------------------------------------------- fetch tab=new


def test_fetch_url_defaults_tab_to_same() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded(
            {"url": "https://a", "excerpt": "x", "fetched_at": "2026-09-03T09:00:00Z",
             "extraction_method": "dom_text"}
        )
    )
    _gateway(client).fetch_url("https://a")
    call = next(c for c in client.calls if c.capability == "browser.fetch_evidence")
    assert call.payload["tab"] == "same"


def test_fetch_url_passes_tab_new_through() -> None:
    client = FakeDeviceCommandClient(
        default_outcome=CommandSucceeded(
            {"url": "https://a", "excerpt": "x", "fetched_at": "2026-09-03T09:00:00Z",
             "extraction_method": "dom_text"}
        )
    )
    _gateway(client).fetch_url("https://a", tab="new")
    call = next(c for c in client.calls if c.capability == "browser.fetch_evidence")
    assert call.payload["tab"] == "new"


def test_search_evidence_from_an_old_worker_is_a_contract_mismatch_not_a_crash() -> None:
    from app.research.browser_gateway import SearchEvidence

    legacy = {
        "engine": "duckduckgo",
        "query": "q",
        "results": [{"url": "https://a", "title": "t"}],
        "page_kind": "ok",
    }
    ev = SearchEvidence.from_result("q", legacy)
    assert ev.schema_version == 0 and ev.contract_ok is False
    assert ev.provider == "unknown" and ev.requested_provider == "unknown"
    assert ev.result_count == 1
    assert ev.as_dict()["contract_ok"] is False

    current = {
        "schema_version": 2,
        "requested_provider": "google",
        "provider": "duckduckgo",
        "fallback": True,
        "fallback_reason": "google:captcha", "query": "q", "result_count": 2, "locale": "tr-TR",
        "attempts": [{"provider": "google", "outcome": "captcha", "detail": ""}],
        "results": [{}, {}],
    }
    ev2 = SearchEvidence.from_result("q", current)
    assert ev2.contract_ok and ev2.provider == "duckduckgo" and ev2.fallback
    assert ev2.locale == "tr-TR"
    assert ev2.as_dict()["fallback_reason"] == "google:captcha"
