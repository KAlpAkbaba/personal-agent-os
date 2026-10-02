"""M13 BrowserGateway seam: unwired-by-default, deterministic fake for tests."""

import ast
import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.devices.commands import CommandFailed, CommandSucceeded
from app.research.browser_gateway import (
    CLOUD_SEARCH_ORDER,
    SEARCH_ORDER_BUDGET_S,
    BrowserDispatchError,
    BrowserGatewayNotConfiguredError,
    DeviceBrowserGateway,
    FakeBrowserGateway,
    FetchQuery,
    UnwiredBrowserGateway,
)
from tests.device_command_support import FakeDeviceCommandClient

NOW = datetime(2026, 9, 2, tzinfo=UTC)


def test_unwired_gateway_refuses_before_any_io() -> None:
    with pytest.raises(BrowserGatewayNotConfiguredError):
        UnwiredBrowserGateway().fetch_evidence([FetchQuery(query="q")])


def test_fake_gateway_is_deterministic_given_now() -> None:
    gateway = FakeBrowserGateway()
    queries = [FetchQuery(query="konu", source_class="news", max_results=2)]
    a = gateway.fetch_evidence(queries, now=NOW)
    b = gateway.fetch_evidence(queries, now=NOW)
    assert a == b


def test_fake_gateway_deterministic_without_explicit_now() -> None:
    gateway = FakeBrowserGateway()
    queries = [FetchQuery(query="konu")]
    a = gateway.fetch_evidence(queries)
    b = gateway.fetch_evidence(queries)
    assert a == b


def test_fake_gateway_respects_max_results() -> None:
    gateway = FakeBrowserGateway()
    result = gateway.fetch_evidence([FetchQuery(query="konu", max_results=3)], now=NOW)
    assert len(result) == 3


def test_fake_gateway_covers_every_query() -> None:
    gateway = FakeBrowserGateway()
    queries = [
        FetchQuery(query="a", max_results=1),
        FetchQuery(query="b", max_results=1),
    ]
    result = gateway.fetch_evidence(queries, now=NOW)
    assert {r.query for r in result} == {"a", "b"}


def test_fake_gateway_tags_source_class_and_fetched_at() -> None:
    gateway = FakeBrowserGateway()
    result = gateway.fetch_evidence(
        [FetchQuery(query="q", source_class="official", max_results=1)], now=NOW
    )
    assert result[0].source_class == "official"
    assert result[0].fetched_at == NOW


# ------------------------------------------------- the engine order of a run on the cloud
#
# Measured in the cloud image (2026-10-02): duckduckgo ended in its captcha page twice out of
# two, `auto` (google, duckduckgo) once out of one, bing answered ten results. A request that
# names ONE engine has no fallback in the worker, so a run on the cloud device walks an order.

REPO = Path(__file__).resolve().parents[4]
DEVICE_ID = uuid.uuid4()
TASK_ID = "task-order"
RESULTS = [{"url": "https://example.com/haber", "title": "Haber", "snippet": "s", "rank": 1}]


def _worker_engines() -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """``ENGINES``, ``AUTO_ORDER`` and whether ``auto`` is an engine name, read from the
    worker's own source (the api cannot import that package)."""
    source = (REPO / "services/browser/browser_agent/search_engines.py").read_text(encoding="utf-8")
    found: dict[str, tuple[str, ...]] = {}
    for node in ast.parse(source).body:
        name = getattr(getattr(node, "target", None), "id", "")
        if isinstance(node, ast.AnnAssign) and name in ("ENGINES", "AUTO_ORDER"):
            found[name] = tuple(ast.literal_eval(node.value))
    return found["ENGINES"], found["AUTO_ORDER"], 'auto_mode = engine == "auto"' in source


def _answers(by_engine: dict[str, object]):
    """A worker that answers each engine as scripted: a result list (an answer, possibly
    empty) or a ``CommandFailed``. An engine the test did not script is a test error."""

    def factory(*, capability, payload, **_kwargs):
        if capability == "browser.session_open":
            return CommandSucceeded({"created": True})
        assert capability == "browser.search"
        answer = by_engine[payload["engine"]]
        if isinstance(answer, CommandFailed):
            return answer
        provider = "duckduckgo" if payload["engine"] == "auto" else payload["engine"]
        requested = "google" if payload["engine"] == "auto" else payload["engine"]
        attempts = [{"provider": provider, "outcome": "ok" if answer else "empty", "detail": ""}]
        if payload["engine"] == "auto":
            attempts.insert(0, {"provider": "google", "outcome": "captcha", "detail": ""})
        return CommandSucceeded(
            {
                "schema_version": 3,
                "requested_provider": requested,
                "provider": provider,
                "fallback": provider != requested,
                "fallback_reason": "captcha" if provider != requested else None,
                "state": "ok",
                "path": "fallback",
                "page_kind": "ok" if answer else "empty",
                "results": answer,
                "result_count": len(answer),
                "attempts": attempts,
            }
        )

    return factory


RATE_LIMITED = CommandFailed(
    "provider_rate_limited", "every provider (x) ended in captcha", retryable=True
)


def _searches(client: FakeDeviceCommandClient) -> list:
    return [c for c in client.calls if c.capability == "browser.search"]


def _cloud_gateway(client: FakeDeviceCommandClient, **kw) -> DeviceBrowserGateway:
    return DeviceBrowserGateway(
        client, device_id=DEVICE_ID, task_id=TASK_ID, search_order=CLOUD_SEARCH_ORDER, **kw
    )


def test_the_cloud_order_is_bing_first_then_every_other_engine_the_worker_has() -> None:
    engines, auto_order, has_auto = _worker_engines()
    assert CLOUD_SEARCH_ORDER[0] == "bing"
    assert len(CLOUD_SEARCH_ORDER) > 1  # never a single engine that has no fallback
    assert has_auto and all(e == "auto" or e in engines for e in CLOUD_SEARCH_ORDER)
    covered = {e for name in CLOUD_SEARCH_ORDER for e in (auto_order if name == "auto" else [name])}
    assert covered == set(engines)
    # No engine is asked twice: ``auto`` already tries these.
    assert not set(auto_order) & set(CLOUD_SEARCH_ORDER)


def test_a_gateway_without_an_order_sends_the_one_search_it_always_sent() -> None:
    client = FakeDeviceCommandClient(factory=_answers({"duckduckgo": RESULTS}))
    gw = DeviceBrowserGateway(client, device_id=DEVICE_ID, task_id=TASK_ID)

    hits = gw.search("ai agents", source_class="news", interstitial="handoff")

    digest = hashlib.sha256(b"ai agents:news").hexdigest()[:16]
    (call,) = _searches(client)
    assert call.payload == {
        "session_id": TASK_ID,
        "query": "ai agents",
        "engine": "duckduckgo",
        "max_results": 10,
        "recency_days": 3,
        "interstitial": "handoff",
    }
    assert call.idempotency_key == f"{TASK_ID}:search:{digest}"
    assert [h.url for h in hits] == ["https://example.com/haber"]


def test_a_cloud_search_asks_bing_first_and_stops_at_its_answer() -> None:
    client = FakeDeviceCommandClient(factory=_answers({"bing": RESULTS}))
    gw = _cloud_gateway(client, search_provider="duckduckgo")

    hits = gw.search("ai agents", source_class="news")

    assert [c.payload["engine"] for c in _searches(client)] == ["bing"]
    assert len(hits) == 1
    evidence = gw.last_search_evidence
    assert (evidence.requested_provider, evidence.provider, evidence.fallback) == (
        "bing",
        "bing",
        False,
    )


def test_a_cloud_search_goes_to_the_next_engine_when_bing_is_blocked_and_says_so() -> None:
    client = FakeDeviceCommandClient(factory=_answers({"bing": RATE_LIMITED, "auto": RESULTS}))
    gw = _cloud_gateway(client)

    hits = gw.search("ai agents", source_class="news")

    searches = _searches(client)
    assert [c.payload["engine"] for c in searches] == ["bing", "auto"]
    assert len({c.idempotency_key for c in searches}) == 2  # a new dispatch, not a replay
    assert len(hits) == 1
    evidence = gw.last_search_evidence
    assert (evidence.requested_provider, evidence.provider, evidence.fallback) == (
        "bing",
        "duckduckgo",
        True,
    )
    assert evidence.fallback_reason
    assert [(a["provider"], a["outcome"]) for a in evidence.attempts] == [
        ("bing", "provider_rate_limited"),
        ("google", "captcha"),
        ("duckduckgo", "ok"),
    ]


def test_a_cloud_search_goes_on_when_an_engine_answers_nothing() -> None:
    client = FakeDeviceCommandClient(factory=_answers({"bing": [], "auto": RESULTS}))
    gw = _cloud_gateway(client)

    assert len(gw.search("ai agents")) == 1
    assert [c.payload["engine"] for c in _searches(client)] == ["bing", "auto"]
    assert [(a["provider"], a["outcome"]) for a in gw.last_search_evidence.attempts][0] == (
        "bing",
        "empty",
    )


def test_a_cloud_search_every_engine_refused_raises_the_last_refusal() -> None:
    client = FakeDeviceCommandClient(
        factory=_answers(dict.fromkeys(CLOUD_SEARCH_ORDER, RATE_LIMITED))
    )
    gw = _cloud_gateway(client)

    with pytest.raises(BrowserDispatchError) as raised:
        gw.search("ai agents")

    assert raised.value.error_class == "provider_rate_limited"
    assert [c.payload["engine"] for c in _searches(client)] == list(CLOUD_SEARCH_ORDER)


def test_a_cloud_search_never_hands_a_headless_window_to_the_owner() -> None:
    client = FakeDeviceCommandClient(factory=_answers({"bing": RATE_LIMITED, "auto": RESULTS}))
    gw = _cloud_gateway(client)

    gw.search("ai agents", interstitial="handoff")

    assert [c.payload["interstitial"] for c in _searches(client)] == ["fallback", "fallback"]


def test_a_cloud_browser_that_cannot_open_is_not_asked_once_per_engine() -> None:
    def factory(*, capability, **_kwargs):
        assert capability == "browser.session_open"
        return CommandFailed("dependency_unavailable", "no browser", retryable=True)

    client = FakeDeviceCommandClient(factory=factory)
    gw = _cloud_gateway(client)

    with pytest.raises(BrowserDispatchError) as raised:
        gw.search("ai agents")

    assert raised.value.error_class == "dependency_unavailable"
    assert len(client.calls) == 1


def test_a_cloud_search_starts_no_further_engine_once_its_time_is_spent() -> None:
    """The discover activity has 90 s (``browser_workflow._MEDIUM``): the walk has a budget
    under it, and an engine that used it up is the last one asked."""
    now = [0.0]
    answers = _answers({"bing": RATE_LIMITED, "auto": RESULTS})

    def slow(**kwargs):
        if kwargs["capability"] == "browser.search":
            now[0] += SEARCH_ORDER_BUDGET_S
        return answers(**kwargs)

    client = FakeDeviceCommandClient(factory=slow)
    gw = _cloud_gateway(client, clock=lambda: now[0])

    with pytest.raises(BrowserDispatchError):
        gw.search("ai agents")

    assert [c.payload["engine"] for c in _searches(client)] == ["bing"]


def test_the_search_budget_is_inside_the_discover_activitys_timeout() -> None:
    workflow = (REPO / "services/api/app/research/browser_workflow.py").read_text(encoding="utf-8")
    assert "_MEDIUM = timedelta(seconds=90)" in workflow
    calls = [
        c
        for c in workflow.split("execute_activity(")[1:]
        if c.lstrip().startswith("discover_activity,")
    ]
    assert calls and all("start_to_close_timeout=_MEDIUM" in c[:600] for c in calls)
    assert SEARCH_ORDER_BUDGET_S < 90
