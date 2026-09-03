"""app.research.discovery: pure request/parse halves only (no network in unit tests)."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.research import discovery

HN_PAYLOAD = {
    "hits": [
        {
            "objectID": "1",
            "title": "New agent framework released",
            "url": "https://example.com/a",
            "created_at": "2026-09-01T10:00:00Z",
        },
        {
            "objectID": "2",
            "title": "Ask HN: agents in production",
            "url": None,
            "created_at": "2026-09-02T10:00:00Z",
        },
    ]
}

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2609.00001v1</id>
    <title>  A survey of multi-agent   systems  </title>
    <published>2026-09-01T00:00:00Z</published>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2609.00002v1</id>
    <title>Tool use in LLM agents</title>
    <published>2026-09-02T00:00:00Z</published>
  </entry>
</feed>
"""

RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>OpenAI ships new agent tools</title><link>https://openai.com/news/a</link>
  <pubDate>Tue, 01 Sep 2026 10:00:00 GMT</pubDate></item>
  <item><title>Another post</title><link>https://openai.com/news/b</link></item>
</channel></rss>
"""

ATOM_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Release v1.2.0</title>
    <link href="https://github.com/org/repo/releases/tag/v1.2.0"/>
    <updated>2026-09-01T00:00:00Z</updated>
  </entry>
</feed>
"""


def test_build_hn_request_shape() -> None:
    from datetime import UTC, datetime

    url, params = discovery.build_hn_request(
        "ai agents", window_start=datetime(2026, 9, 1, tzinfo=UTC), max_results=5
    )
    assert url == discovery.HN_ALGOLIA_URL
    assert params["query"] == "ai agents"
    assert params["tags"] == "story"
    assert params["hitsPerPage"] == 5
    assert "created_at_i>=" in params["numericFilters"]


def test_parse_hn_response_maps_hits_and_skips_urlless() -> None:
    candidates = discovery.parse_hn_response(HN_PAYLOAD, query_id="q1")
    assert len(candidates) == 2
    assert candidates[0].url == "https://example.com/a"
    assert candidates[0].discovered_by == "hn"
    assert candidates[0].publisher == "Hacker News"
    # objectID=2 has url=None -> falls back to the HN item URL, never dropped.
    assert candidates[1].url == "https://news.ycombinator.com/item?id=2"


def test_parse_hn_response_empty_hits() -> None:
    assert discovery.parse_hn_response({"hits": []}, query_id="q1") == []


def test_build_arxiv_request_shape() -> None:
    url, params = discovery.build_arxiv_request("multi-agent systems", max_results=3)
    assert url == discovery.ARXIV_API_URL
    assert params["search_query"] == "all:multi-agent systems"
    assert params["max_results"] == 3


def test_parse_arxiv_response_extracts_entries() -> None:
    candidates = discovery.parse_arxiv_response(ARXIV_XML, query_id="q1")
    assert len(candidates) == 2
    assert candidates[0].url == "http://arxiv.org/abs/2609.00001v1"
    assert candidates[0].title == "A survey of multi-agent systems"
    assert candidates[0].publisher == "arXiv"
    assert candidates[0].discovered_by == "arxiv"
    assert candidates[0].published_hint == "2026-09-01T00:00:00Z"


def test_parse_arxiv_response_invalid_xml_raises_discovery_error() -> None:
    with pytest.raises(discovery.DiscoveryError):
        discovery.parse_arxiv_response("not xml at all <<", query_id="q1")


def test_parse_rss_extracts_items() -> None:
    candidates = discovery.parse_rss_or_atom(RSS_XML, publisher="OpenAI", query_id="q1")
    assert len(candidates) == 2
    assert candidates[0].url == "https://openai.com/news/a"
    assert candidates[0].title == "OpenAI ships new agent tools"
    assert candidates[0].publisher == "OpenAI"
    assert candidates[0].discovered_by == "rss"
    assert candidates[0].published_hint == "Tue, 01 Sep 2026 10:00:00 GMT"


def test_parse_atom_extracts_entries() -> None:
    candidates = discovery.parse_rss_or_atom(ATOM_XML, publisher="GitHub", query_id="q1")
    assert len(candidates) == 1
    assert candidates[0].url == "https://github.com/org/repo/releases/tag/v1.2.0"
    assert candidates[0].title == "Release v1.2.0"


def test_parse_rss_or_atom_invalid_xml_raises() -> None:
    with pytest.raises(discovery.DiscoveryError):
        discovery.parse_rss_or_atom("{not xml}", publisher="X", query_id="q1")


def test_parse_rss_respects_max_items() -> None:
    candidates = discovery.parse_rss_or_atom(
        RSS_XML, publisher="OpenAI", query_id="q1", max_items=1
    )
    assert len(candidates) == 1


def test_discovered_candidate_is_json_serializable_shape() -> None:
    c = discovery.DiscoveredCandidate(
        url="https://a", title="t", publisher="p", discovered_by="rss", query_id="q1"
    )
    json.dumps(
        {"url": c.url, "title": c.title, "publisher": c.publisher, "discovered_by": c.discovered_by}
    )


# --------------------------------------------------- bounded body (fake transport)


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_fetch_hn_end_to_end_with_fake_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/search_by_date"
        return httpx.Response(200, json=HN_PAYLOAD)

    candidates = discovery.fetch_hn(
        "ai agents", window_start=datetime(2026, 9, 1, tzinfo=UTC), transport=_transport(handler)
    )
    assert len(candidates) == 2
    assert candidates[0].url == "https://example.com/a"


def test_fetch_arxiv_end_to_end_with_fake_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=ARXIV_XML, headers={"content-type": "application/xml"})

    candidates = discovery.fetch_arxiv("multi-agent systems", transport=_transport(handler))
    assert len(candidates) == 2
    assert candidates[0].publisher == "arXiv"


def test_fetch_rss_end_to_end_with_fake_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=RSS_XML, headers={"content-type": "application/rss+xml"})

    candidates = discovery.fetch_rss(
        "https://openai.com/feed", publisher="OpenAI", query_id="q1", transport=_transport(handler)
    )
    assert len(candidates) == 2
    assert candidates[0].publisher == "OpenAI"


def test_fetch_hn_http_error_raises_discovery_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    with pytest.raises(discovery.DiscoveryError):
        discovery.fetch_hn(
            "ai agents",
            window_start=datetime(2026, 9, 1, tzinfo=UTC),
            transport=_transport(handler),
        )


def test_fetch_hn_invalid_json_raises_discovery_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json at all")

    with pytest.raises(discovery.DiscoveryError):
        discovery.fetch_hn(
            "ai agents",
            window_start=datetime(2026, 9, 1, tzinfo=UTC),
            transport=_transport(handler),
        )


def test_oversize_response_body_is_a_discovery_error_not_an_unbounded_read() -> None:
    """The body is capped at MAX_DISCOVERY_BODY_BYTES (2 MiB); a response
    that exceeds it must fail as a recorded DiscoveryError, streamed and cut
    off rather than fully buffered first."""
    oversized = b"x" * (discovery.MAX_DISCOVERY_BODY_BYTES + 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized)

    with pytest.raises(discovery.DiscoveryError):
        discovery.fetch_rss(
            "https://openai.com/feed",
            publisher="OpenAI",
            query_id="q1",
            transport=_transport(handler),
        )


def test_response_just_under_the_cap_is_accepted() -> None:
    """A response comfortably under the cap (but still invalid feed XML) must
    fail on PARSING, not on the size check — proves the cap itself isn't
    accidentally rejecting normal-sized bodies."""
    small_body = "<rss version='2.0'><channel></channel></rss>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=small_body)

    candidates = discovery.fetch_rss(
        "https://openai.com/feed",
        publisher="OpenAI",
        query_id="q1",
        transport=_transport(handler),
    )
    assert candidates == []


def test_fetch_rss_follows_a_bounded_redirect() -> None:
    # Seen live: Google's AI feed moved and answers 301; a feed behind one redirect
    # must still parse, and the follow is bounded (max_redirects) by the client.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/technology/ai/rss/":
            return httpx.Response(301, headers={"location": "https://blog.example/new/rss/"})
        assert request.url.path == "/new/rss/"
        return httpx.Response(200, text=RSS_XML, headers={"content-type": "application/rss+xml"})

    candidates = discovery.fetch_rss(
        "https://blog.example/technology/ai/rss/",
        publisher="Google AI Blog",
        query_id="q1",
        transport=_transport(handler),
    )
    assert len(candidates) == 2


def test_fetch_rss_redirect_loop_is_a_discovery_error_not_a_hang() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": str(request.url)})

    with pytest.raises(discovery.DiscoveryError):
        discovery.fetch_rss(
            "https://blog.example/loop/",
            publisher="x",
            query_id="q1",
            transport=_transport(handler),
        )
