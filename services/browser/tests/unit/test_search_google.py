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
    GoogleHandoffPending,
    append_recency_param,
    build_search_url,
    detect_google_interstitial,
    is_verification_cleared,
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


class TestSchemaVersion:
    async def test_search_result_carries_schema_version_2(self) -> None:
        from browser_agent.search_engines import SEARCH_SCHEMA_VERSION

        async def fetch(engine: str, url: str):
            return _read("google.html"), "ok", 200, url

        d = (await run_search("q", "auto", fetch=fetch)).as_dict()
        assert SEARCH_SCHEMA_VERSION == 2 and d["schema_version"] == 2

    def test_worker_advertises_the_search_contract(self) -> None:
        from browser_agent.worker import CONTRACTS, WORKER_VERSION

        assert CONTRACTS["browser.search"] == 2
        assert tuple(int(x) for x in WORKER_VERSION.split(".")) >= (0, 2, 0)


class TestVerificationCleared:
    """``browser.wait for=verification_cleared`` decision (contract §3a), a
    pure helper tested with fake page states — no browser required."""

    def test_sorry_page_is_not_cleared(self) -> None:
        sorry = _read("google-sorry.html")
        assert is_verification_cleared("https://www.google.com/sorry/index?c=x", sorry) is False
        assert is_verification_cleared("https://www.google.com/search?q=x", sorry) is False

    def test_consent_page_is_not_cleared(self) -> None:
        consent = _read("google-consent.html")
        assert is_verification_cleared("https://consent.google.com/m?c=x", consent) is False

    def test_normal_results_page_is_cleared(self) -> None:
        ok = _read("google.html")
        assert is_verification_cleared("https://www.google.com/search?q=x", ok) is True

    def test_empty_page_with_no_interstitial_markers_is_cleared(self) -> None:
        assert is_verification_cleared("https://www.google.com/search?q=x", "<html></html>") is True


class TestAppendRecencyParam:
    def test_adds_tbs_and_preserves_other_params(self) -> None:
        url = append_recency_param("https://www.google.com/search?q=ai+agents&hl=tr", 3)
        assert "tbs=qdr%3Aw" in url
        assert "q=ai+agents" in url
        assert "hl=tr" in url

    def test_overwrites_an_existing_tbs(self) -> None:
        url = append_recency_param("https://www.google.com/search?q=x&tbs=qdr:y", 1)
        assert "tbs=qdr%3Ad" in url
        assert "tbs=qdr%3Ay" not in url


class TestHandoffOutcome:
    """``run_search`` turns a ``GoogleHandoffPending`` from ``fetch`` into a
    SUCCESSFUL, waiting outcome — never a fallback, never a second attempt."""

    async def test_handoff_pending_outcome_shape(self) -> None:
        calls: list[str] = []

        async def fetch(engine: str, url: str):
            calls.append(engine)
            raise GoogleHandoffPending(
                page_kind="captcha", verification_url="https://www.google.com/sorry/index"
            )

        outcome = await run_search("yapay zeka ajanları", "auto", fetch=fetch)
        # Never falls back: duckduckgo is never even attempted.
        assert calls == ["google"]
        d = outcome.as_dict()
        assert d["state"] == "waiting_for_owner_verification"
        assert d["path"] == "handoff_pending"
        assert d["provider"] is None
        assert d["requested_provider"] == "google"
        assert d["results"] == []
        assert d["result_count"] == 0
        assert d["page_kind"] == "captcha"
        assert d["verification_url"] == "https://www.google.com/sorry/index"
        # Not a fallback: nothing else was ever tried on the requested provider's behalf.
        assert d["fallback"] is False
        assert d["fallback_reason"] is None
        assert [a["provider"] for a in d["attempts"]] == ["google"]
        assert d["schema_version"] == 2

    async def test_handoff_pending_explicit_google_engine(self) -> None:
        async def fetch(engine: str, url: str):
            raise GoogleHandoffPending(
                page_kind="consent", verification_url="https://consent.google.com/m"
            )

        outcome = await run_search("q", "google", fetch=fetch)
        assert outcome.state == "waiting_for_owner_verification"
        assert outcome.page_kind == "consent"

    async def test_verification_url_key_omitted_when_not_waiting(self) -> None:
        async def fetch(engine: str, url: str):
            return _read("google.html"), "ok", 200, url

        d = (await run_search("q", "auto", fetch=fetch)).as_dict()
        assert "verification_url" not in d


class TestPathValues:
    async def test_google_success_defaults_to_google_ui_path(self) -> None:
        async def fetch(engine: str, url: str):
            return _read("google.html"), "ok", 200, url

        outcome = await run_search("q", "auto", fetch=fetch)
        assert outcome.path == "google_ui"
        assert outcome.state == "ok"

    async def test_fetch_may_override_the_path_with_a_fifth_element(self) -> None:
        async def fetch(engine: str, url: str):
            return _read("google.html"), "ok", 200, url, "google_url"

        outcome = await run_search("q", "auto", fetch=fetch)
        assert outcome.path == "google_url"

    async def test_handoff_cleared_path_via_fetch_hint(self) -> None:
        async def fetch(engine: str, url: str):
            return _read("google.html"), "ok", 200, url, "handoff_cleared"

        outcome = await run_search("q", "auto", fetch=fetch)
        assert outcome.path == "handoff_cleared"
        assert outcome.provider == "google"

    async def test_fallback_to_duckduckgo_has_fallback_path(self) -> None:
        async def fetch(engine: str, url: str):
            if engine == "google":
                return _read("google-sorry.html"), "ok", 200, "https://www.google.com/sorry/index"
            return _read("duckduckgo.html"), "ok", 200, url

        outcome = await run_search("q", "auto", fetch=fetch)
        assert outcome.provider == "duckduckgo"
        assert outcome.path == "fallback"

    async def test_explicit_non_google_engine_has_fallback_path(self) -> None:
        async def fetch(engine: str, url: str):
            return _read("duckduckgo.html"), "ok", 200, url

        outcome = await run_search("q", "duckduckgo", fetch=fetch)
        assert outcome.path == "fallback"
