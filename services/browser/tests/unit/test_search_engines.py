"""Unit tests for browser_agent.search_engines: SERP parsers + auto fallover.

No browser: parsers run against saved HTML fixture files
(tests/fixtures/serp/*.html), and ``run_search``'s fallover logic is driven
with an injected fake ``fetch`` coroutine instead of a real navigation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.search_engines import (
    build_search_url,
    parse_bing_html,
    parse_brave_html,
    parse_duckduckgo_html,
    parse_engine_html,
    resolve_result_url,
    run_search,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "serp"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestDuckDuckGoParser:
    def test_drops_ads_and_own_domain_keeps_organic_results(self) -> None:
        results = parse_duckduckgo_html(_read("duckduckgo.html"))
        urls = [r.url for r in results]
        assert "https://sponsor.example.com/ad" not in urls
        assert not any("duckduckgo.com" in u for u in urls)
        assert urls == [
            "https://example.com/ai-agents-overview",
            "https://blog.example.org/agents-in-production",
        ]

    def test_extracts_title_and_snippet(self) -> None:
        results = parse_duckduckgo_html(_read("duckduckgo.html"))
        first = results[0]
        assert first.title == "AI agents: an overview"
        assert "survey of current AI agent frameworks" in first.snippet

    def test_published_hint_extracted_from_snippet(self) -> None:
        results = parse_duckduckgo_html(_read("duckduckgo.html"))
        assert results[0].published_hint == "2 days ago"

    def test_max_results_caps_output(self) -> None:
        results = parse_duckduckgo_html(_read("duckduckgo.html"), max_results=1)
        assert len(results) == 1


class TestBingParser:
    def test_drops_ads_and_own_domain(self) -> None:
        results = parse_bing_html(_read("bing.html"))
        urls = [r.url for r in results]
        assert "https://www.bing.com/aclk?ad=1" not in urls
        assert not any(u.startswith("https://www.bing.com/search") for u in urls)
        assert urls == [
            "https://example.com/bing-ai-agents",
            "https://news.example.net/agent-roundup",
        ]

    def test_published_hint(self) -> None:
        results = parse_bing_html(_read("bing.html"))
        assert results[0].published_hint == "3 days ago"


class TestBraveParser:
    def test_drops_ad_data_type_and_own_domain(self) -> None:
        results = parse_brave_html(_read("brave.html"))
        urls = [r.url for r in results]
        assert "https://sponsor.example.com/brave-ad" not in urls
        assert not any("search.brave.com" in u for u in urls)
        assert urls == [
            "https://example.com/brave-ai-agents",
            "https://research.example.edu/agents-paper",
        ]

    def test_title_and_snippet(self) -> None:
        results = parse_brave_html(_read("brave.html"))
        assert results[0].title == "Understanding AI agents"
        assert "primer on AI agent architectures" in results[0].snippet


def test_parse_engine_html_dispatches_by_name() -> None:
    results = parse_engine_html("bing", _read("bing.html"))
    assert len(results) == 2


def test_parse_engine_html_unknown_engine_is_validation_error() -> None:
    with pytest.raises(BrowserError) as exc_info:
        parse_engine_html("altavista", "<html></html>")
    assert exc_info.value.error_class == ErrorClass.VALIDATION_ERROR


class TestBuildSearchUrl:
    def test_base_urls_match_contract(self) -> None:
        assert build_search_url("duckduckgo", "ai agents").startswith(
            "https://html.duckduckgo.com/html/?"
        )
        assert build_search_url("bing", "ai agents").startswith("https://www.bing.com/search?")
        assert build_search_url("brave", "ai agents").startswith("https://search.brave.com/search?")

    def test_recency_days_maps_to_engine_param(self) -> None:
        assert "df=d" in build_search_url("duckduckgo", "x", recency_days=1)
        assert "freshness=Week" in build_search_url("bing", "x", recency_days=5)
        assert "tf=pm" in build_search_url("brave", "x", recency_days=20)

    def test_no_recency_param_when_absent(self) -> None:
        url = build_search_url("duckduckgo", "x")
        assert "df=" not in url


class TestRunSearchFallover:
    # Provider abstraction: auto = google -> duckduckgo (see test_search_google.py for the
    # Google parser and evidence); these pin the fallover mechanics.
    async def test_auto_uses_the_primary_provider_when_it_succeeds(self) -> None:
        calls: list[str] = []

        async def fetch(engine: str, url: str) -> tuple[str, str, int | None]:
            calls.append(engine)
            return _read("google.html"), "ok", 200

        outcome = await run_search("ai agents", "auto", fetch=fetch)
        assert calls == ["google"]
        assert outcome.engine == "google" and outcome.fallback is False
        assert len(outcome.results) == 3
        assert outcome.page_kind == "ok"

    async def test_auto_falls_over_on_captcha_to_the_fallback_provider(self) -> None:
        calls: list[str] = []

        async def fetch(engine: str, url: str) -> tuple[str, str, int | None]:
            calls.append(engine)
            if engine == "google":
                return "<html>captcha challenge</html>", "captcha", 200
            return _read("duckduckgo.html"), "ok", 200

        outcome = await run_search("ai agents", "auto", fetch=fetch)
        assert calls == ["google", "duckduckgo"]
        assert outcome.engine == "duckduckgo" and outcome.fallback is True
        assert outcome.fallback_reason == "google:captcha"
        assert len(outcome.results) == 2

    async def test_auto_reports_empty_when_the_fallback_is_empty_too(self) -> None:
        calls: list[str] = []

        async def fetch(engine: str, url: str) -> tuple[str, str, int | None]:
            calls.append(engine)
            if engine == "google":
                return "<html>blocked</html>", "blocked", 403
            return "<html></html>", "empty", 200

        outcome = await run_search("ai agents", "auto", fetch=fetch)
        assert calls == ["google", "duckduckgo"]
        assert outcome.results == () and outcome.page_kind == "empty"
        assert [a.outcome for a in outcome.attempts] == ["blocked", "empty"]

    async def test_all_engines_captcha_or_blocked_raises_provider_rate_limited(self) -> None:
        async def fetch(engine: str, url: str) -> tuple[str, str, int | None]:
            return "<html>captcha</html>", "captcha" if engine != "duckduckgo" else "blocked", 200

        with pytest.raises(BrowserError) as exc_info:
            await run_search("ai agents", "auto", fetch=fetch)
        assert exc_info.value.error_class == ErrorClass.PROVIDER_RATE_LIMITED
        assert exc_info.value.retryable is True

    async def test_explicit_engine_never_falls_over(self) -> None:
        calls: list[str] = []

        async def fetch(engine: str, url: str) -> tuple[str, str, int | None]:
            calls.append(engine)
            return "<html>captcha</html>", "captcha", 200

        with pytest.raises(BrowserError) as exc_info:
            await run_search("ai agents", "bing", fetch=fetch)
        assert calls == ["bing"]
        assert exc_info.value.error_class == ErrorClass.PROVIDER_RATE_LIMITED

    async def test_auto_falls_over_on_transport_error_from_one_engine(self) -> None:
        # Observed live against Brave Search: a network-level abort (bot filtering) raises
        # BrowserError from the navigation itself, before any page_kind can be read.
        calls: list[str] = []

        async def fetch(engine: str, url: str) -> tuple[str, str, int | None]:
            calls.append(engine)
            if engine == "google":
                raise BrowserError(
                    ErrorClass.DEPENDENCY_UNAVAILABLE, "net::ERR_ABORTED", retryable=True
                )
            return _read("duckduckgo.html"), "ok", 200

        outcome = await run_search("ai agents", "auto", fetch=fetch)
        assert calls == ["google", "duckduckgo"]
        assert outcome.engine == "duckduckgo"
        assert outcome.attempts[0].outcome == "transport_error"


# --------------------------------------------------------------- real redirects
# Shapes copied from real SERPs fetched with headful Chrome on 2026-09-03: the
# engines wrap every organic link in a click-tracking redirect on their own
# domain, which the first parser version dropped as "own domain" (0 results).

DDG_REAL = """
<div class="result results_links results_links_deep web-result">
 <div class="links_main links_deep result__body">
  <h2 class="result__title">
   <a class="result__a" rel="nofollow"
      href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fagents&amp;rut=4348"
      >AI Agents News</a>
  </h2>
  <a class="result__snippet"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fagents">Daily updates on agents.</a>
 </div>
</div>
"""

BING_REAL = """
<ol id="b_results">
 <li class="b_algo"><h2><a
   href="https://www.bing.com/ck/a?!&amp;&amp;p=c83369cf&amp;u=a1aHR0cHM6Ly9haWFnZW50c3RvcmUuYWkvYWktYWdlbnQtbmV3cy90aGlzLXdlZWs&amp;ntb=1"
   >AI Agents News</a></h2><div class="b_caption"><p>Weekly agent news.</p></div></li>
 <li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=zz-not-base64-!!!">Broken</a></h2></li>
</ol>
"""


class TestRealRedirectShapes:
    def test_duckduckgo_uddg_redirect_is_unwrapped(self) -> None:
        results = parse_duckduckgo_html(DDG_REAL)
        assert [r.url for r in results] == ["https://example.com/agents"]
        assert results[0].snippet == "Daily updates on agents."

    def test_bing_ck_redirect_is_base64url_decoded_and_broken_ones_dropped(self) -> None:
        results = parse_bing_html(BING_REAL)
        assert [r.url for r in results] == ["https://aiagentstore.ai/ai-agent-news/this-week"]

    def test_resolve_result_url_rejects_non_http_destinations(self) -> None:
        bad = "//duckduckgo.com/l/?uddg=javascript%3Aalert(1)"
        assert resolve_result_url(bad, "duckduckgo") is None
        assert resolve_result_url("mailto:a@b", "bing") is None
        assert resolve_result_url("https://example.com/x", "brave") == "https://example.com/x"
