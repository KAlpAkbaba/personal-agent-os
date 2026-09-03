"""Google-primary search provider abstraction (owner decision 2026-09-03).

Google first, DuckDuckGo as the fallback, every attempt recorded as evidence
(requested_provider / provider / fallback / fallback_reason / query /
result_count / attempts); the unusual-traffic interstitial and the consent
page are recognised and never answered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.search_engines import (
    AUTO_ORDER,
    PRIMARY_PROVIDER,
    build_search_url,
    detect_google_interstitial,
    parse_engine_html,
    parse_google_html,
    run_search,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "serp"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_google_is_the_primary_provider_and_duckduckgo_the_fallback() -> None:
    assert PRIMARY_PROVIDER == "google"
    assert AUTO_ORDER == ("google", "duckduckgo")


class TestGoogleUrl:
    def test_locale_maps_to_hl_and_gl_without_assuming_one(self) -> None:
        url = build_search_url("google", "yapay zeka", locale="tr-TR")
        assert url.startswith("https://www.google.com/search?")
        assert "hl=tr" in url and "gl=tr" in url and "num=10" in url
        bare = build_search_url("google", "ai agents")
        assert "hl=" not in bare and "gl=" not in bare
        assert "hl=de" in build_search_url("google", "x", locale="de")

    def test_recency_maps_to_tbs(self) -> None:
        assert "tbs=qdr%3Ad" in build_search_url("google", "x", recency_days=1)
        assert "tbs=qdr%3Aw" in build_search_url("google", "x", recency_days=3)


class TestGoogleParser:
    def test_extracts_ranked_organic_results_only(self) -> None:
        results = parse_google_html(_read("google.html"))
        urls = [r.url for r in results]
        assert urls == [
            "https://openai.com/index/agents-update",
            "https://www.anthropic.com/news/agents",
            "https://blog.google/technology/ai/agents/",
        ]
        assert results[0].title == "OpenAI ajan güncellemesi"
        assert "yeni özellikler" in results[0].snippet
        assert results[0].published_hint == "2 gün önce"
        assert results[1].snippet.startswith("Anthropic, Claude")

    def test_excluded_surfaces(self) -> None:
        urls = [r.url for r in parse_google_html(_read("google.html"))]
        assert not any("googleadservices" in u for u in urls), "ads"
        assert "https://example.org/paa-answer" not in urls, "People also ask"
        assert "https://en.wikipedia.org/wiki/Intelligent_agent" not in urls, "knowledge panel"
        assert "https://www.youtube.com/watch?v=abc" not in urls, "carousel"
        assert not any("google.com/maps" in u for u in urls), "Google's own domain"
        assert "https://example.com/no-heading" not in urls, "no h3"

    def test_max_results_and_dispatch(self) -> None:
        assert len(parse_google_html(_read("google.html"), max_results=2)) == 2
        assert len(parse_engine_html("google", _read("google.html"))) == 3

    def test_empty_and_malformed_markup(self) -> None:
        assert parse_google_html("") == []
        assert parse_google_html("<html><body><p>nothing</p></body></html>") == []
        assert parse_google_html("<div class=g><h3>no link</h3></div>") == []
        assert parse_google_html("<<<>>> not html at all") == []


class TestInterstitials:
    def test_sorry_page_is_captcha_by_url_and_by_text(self) -> None:
        sorry = _read("google-sorry.html")
        sorry_url = "https://www.google.com/sorry/index?c=x"
        assert detect_google_interstitial(sorry, sorry_url) == "captcha"
        assert detect_google_interstitial(_read("google-sorry.html"), None) == "captcha"

    def test_consent_page_is_consent(self) -> None:
        consent = _read("google-consent.html")
        assert detect_google_interstitial(consent, "https://consent.google.com/m?c=x") == "consent"
        assert detect_google_interstitial(_read("google-consent.html"), None) == "consent"

    def test_normal_results_are_not_an_interstitial(self) -> None:
        ok = _read("google.html")
        assert detect_google_interstitial(ok, "https://www.google.com/search?q=x") is None


class TestProviderAbstraction:
    async def test_google_success_records_provider_evidence(self) -> None:
        calls: list[str] = []

        async def fetch(engine: str, url: str):
            calls.append(engine)
            return _read("google.html"), "ok", 200, "https://www.google.com/search?q=x"

        outcome = await run_search("yapay zeka ajanları", "auto", fetch=fetch, locale="tr-TR")
        assert calls == ["google"]
        d = outcome.as_dict()
        assert d["requested_provider"] == "google" and d["provider"] == "google"
        assert d["fallback"] is False and d["fallback_reason"] is None
        assert d["query"] == "yapay zeka ajanları" and d["result_count"] == 3
        assert d["locale"] == "tr-TR"
        assert d["attempts"] == [{"provider": "google", "outcome": "ok", "detail": "3 results"}]
        assert [r["rank"] for r in d["results"]] == [1, 2, 3]
        assert d["engine"] == "google"

    async def test_google_captcha_falls_back_to_duckduckgo_with_reason(self) -> None:
        calls: list[str] = []

        async def fetch(engine: str, url: str):
            calls.append(engine)
            if engine == "google":
                sorry_url = "https://www.google.com/sorry/index?continue=x"
                return _read("google-sorry.html"), "ok", 200, sorry_url
            return _read("duckduckgo.html"), "ok", 200, url

        outcome = await run_search("ai agents", "auto", fetch=fetch)
        assert calls == ["google", "duckduckgo"]
        d = outcome.as_dict()
        assert d["requested_provider"] == "google" and d["provider"] == "duckduckgo"
        assert d["fallback"] is True and d["fallback_reason"] == "google:captcha"
        assert d["result_count"] == 2
        assert [a["outcome"] for a in d["attempts"]] == ["captcha", "ok"]

    async def test_google_consent_and_empty_are_recorded_reasons(self) -> None:
        async def consent(engine: str, url: str):
            if engine == "google":
                return _read("google-consent.html"), "ok", 200, "https://consent.google.com/m?x"
            return _read("duckduckgo.html"), "ok", 200, url

        d = (await run_search("q", "auto", fetch=consent)).as_dict()
        assert d["fallback_reason"] == "google:consent"

        async def empty(engine: str, url: str):
            if engine == "google":
                return "<html><body><div id=rso></div></body></html>", "ok", 200, url
            return _read("duckduckgo.html"), "ok", 200, url

        d = (await run_search("q", "auto", fetch=empty)).as_dict()
        assert d["provider"] == "duckduckgo" and d["fallback_reason"] == "google:empty"

    async def test_transport_error_on_google_falls_back(self) -> None:
        async def fetch(engine: str, url: str):
            if engine == "google":
                raise BrowserError(
                    ErrorClass.DEPENDENCY_UNAVAILABLE, "net::ERR_ABORTED", retryable=True
                )
            return _read("duckduckgo.html"), "ok", 200, url

        d = (await run_search("q", "auto", fetch=fetch)).as_dict()
        assert d["provider"] == "duckduckgo"
        assert d["attempts"][0] == {
            "provider": "google", "outcome": "transport_error", "detail": "dependency_unavailable"
        }

    async def test_all_providers_blocked_raises_rate_limited_with_attempts(self) -> None:
        async def fetch(engine: str, url: str):
            if engine == "google":
                return _read("google-sorry.html"), "ok", 200, "https://www.google.com/sorry/index"
            return "<html>captcha</html>", "captcha", 200, url

        with pytest.raises(BrowserError) as exc_info:
            await run_search("q", "auto", fetch=fetch)
        assert exc_info.value.error_class == ErrorClass.PROVIDER_RATE_LIMITED
        assert exc_info.value.retryable is True
        assert exc_info.value.evidence["requested_provider"] == "google"
        assert [a["outcome"] for a in exc_info.value.evidence["attempts"]] == ["captcha", "captcha"]

    async def test_explicit_google_never_falls_back(self) -> None:
        calls: list[str] = []

        async def fetch(engine: str, url: str):
            calls.append(engine)
            return _read("google-sorry.html"), "ok", 200, "https://www.google.com/sorry/index"

        with pytest.raises(BrowserError) as exc_info:
            await run_search("q", "google", fetch=fetch)
        assert calls == ["google"]
        assert exc_info.value.error_class == ErrorClass.PROVIDER_RATE_LIMITED

    async def test_three_tuple_fetch_still_works(self) -> None:
        async def fetch(engine: str, url: str):
            return _read("google.html"), "ok", 200

        d = (await run_search("q", "auto", fetch=fetch)).as_dict()
        assert d["provider"] == "google" and d["result_count"] == 3
