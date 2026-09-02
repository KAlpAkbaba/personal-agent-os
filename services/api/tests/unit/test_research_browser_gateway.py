"""M13 BrowserGateway seam: unwired-by-default, deterministic fake for tests."""

from datetime import UTC, datetime

import pytest

from app.research.browser_gateway import (
    BrowserGatewayNotConfiguredError,
    FakeBrowserGateway,
    FetchQuery,
    UnwiredBrowserGateway,
)

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
