"""M13 contract v1.1 (§3a) browser e2e scenarios: Google through the real UI,
owner handoff, and ``fetch_evidence`` tab isolation.

Real (headless) Chromium against the fixture site, driven in-process like
``tests/browser/test_worker_e2e.py`` (``await worker._execute(...)`` directly,
no stdio/subprocess). ``--google-base-url`` points every worker in this file
at ``tests/fixtures/site/google-home.html`` (and its ``google-results.html`` /
``google-sorry.html`` companions) instead of the real Google, so the flow is
deterministic and offline — no stealth, no CAPTCHA solving, just the real
Playwright/DOM control surface against local fixtures.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from browser_agent import search_engines
from browser_agent.session import BrowserSession
from browser_agent.worker import Worker, build_arg_parser

pytestmark = pytest.mark.browser


def _open_payload(session_id: str = "s1") -> dict:
    return {
        "session_id": session_id,
        "profile": "isolated",
        "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": False},
    }


async def _make_worker(tmp_path, *, google_base_url: str, name: str = "worker-data") -> Worker:
    data_dir = tmp_path / name
    data_dir.mkdir()
    args = build_arg_parser().parse_args(
        [
            "--data-dir",
            str(data_dir),
            "--channel",
            "chromium",
            "--headless",
            "--allow-private-destinations",
            "--google-base-url",
            google_base_url,
        ]
    )
    worker = Worker(args)
    await worker._print_hello()
    return worker


@pytest.fixture()
async def google_worker(tmp_path, site_url):
    """A worker whose Google home page is the fixture site's google-home.html
    (normal case: the form GETs google-results.html)."""
    worker = await _make_worker(tmp_path, google_base_url=f"{site_url}/google-home.html")
    try:
        yield worker
    finally:
        await worker._close_all_sessions()


@pytest.fixture()
async def handoff_worker(tmp_path, site_url):
    """A worker whose Google home page is the ``simulate=sorry`` variant of
    google-home.html, so its very first search deterministically lands on
    the interstitial fixture (google-sorry.html) instead of results."""
    worker = await _make_worker(
        tmp_path,
        google_base_url=f"{site_url}/google-home.html?simulate=sorry",
        name="handoff-worker-data",
    )
    try:
        yield worker
    finally:
        await worker._close_all_sessions()


# --------------------------------------------------------------------------- #
# (a) Google through the real UI: type into the box, parse the real results
# --------------------------------------------------------------------------- #


async def test_google_search_via_ui_types_query_and_parses_results(
    google_worker, site_url
) -> None:
    worker = google_worker
    await worker._execute("browser.session_open", _open_payload())

    outcome = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": "yapay zeka ajanları", "engine": "auto", "max_results": 10},
    )

    assert outcome["provider"] == "google"
    assert outcome["requested_provider"] == "google"
    assert outcome["fallback"] is False
    assert outcome["path"] == "google_ui"
    assert outcome["state"] == "ok"
    assert outcome["schema_version"] == 2
    assert outcome["result_count"] == 2
    urls = [r["url"] for r in outcome["results"]]
    assert "https://openai.com/index/agents-update" in urls
    assert "https://www.anthropic.com/news/agents" in urls
    assert [a["outcome"] for a in outcome["attempts"]] == ["ok"]

    await worker._execute("browser.session_close", {"session_id": "s1"})


# --------------------------------------------------------------------------- #
# (b) A second search on the same session types into the loaded results page
# --------------------------------------------------------------------------- #


async def test_second_search_reuses_loaded_results_page_no_home_navigation(
    google_worker, site_url, monkeypatch
) -> None:
    worker = google_worker
    navigated: list[str] = []
    original_navigate = BrowserSession.navigate

    async def spy_navigate(self, url, *, timeout_ms=15_000):
        navigated.append(url)
        return await original_navigate(self, url, timeout_ms=timeout_ms)

    monkeypatch.setattr(BrowserSession, "navigate", spy_navigate)

    await worker._execute("browser.session_open", _open_payload())
    first = await worker._execute(
        "browser.search", {"session_id": "s1", "query": "yapay zeka ajanları", "engine": "auto"}
    )
    assert first["path"] == "google_ui"

    second = await worker._execute(
        "browser.search", {"session_id": "s1", "query": "otonom ajanlar", "engine": "auto"}
    )
    assert second["path"] == "google_ui"
    assert second["provider"] == "google"

    # Only the FIRST search navigates to the home page; the second types into
    # the already-loaded results page in place (no home navigation).
    home_navigations = [u for u in navigated if "/google-home.html" in u]
    assert len(home_navigations) == 1

    await worker._execute("browser.session_close", {"session_id": "s1"})


# --------------------------------------------------------------------------- #
# (c) Owner handoff: interstitial -> wait for clearance -> resume
# --------------------------------------------------------------------------- #


async def test_handoff_interstitial_then_clearance_then_resume(
    handoff_worker, site_url
) -> None:
    worker = handoff_worker
    await worker._execute("browser.session_open", _open_payload())
    query = "yapay zeka ajanları"

    pending = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": query, "engine": "auto", "interstitial": "handoff"},
    )
    assert pending["state"] == "waiting_for_owner_verification"
    assert pending["path"] == "handoff_pending"
    assert pending["provider"] is None
    assert pending["results"] == []
    assert pending["page_kind"] == "captcha"
    assert pending["verification_url"]
    # Never loops, never falls back on the first interstitial: exactly one
    # attempt, for google, nothing tried after it.
    assert [a["provider"] for a in pending["attempts"]] == ["google"]

    # Simulate the owner completing verification by hand: Google would
    # redirect back to the original results page.
    await worker._execute(
        "browser.navigate",
        {"session_id": "s1", "url": f"{site_url}/google-results.html?q={quote(query)}"},
    )

    waited = await worker._execute(
        "browser.wait", {"session_id": "s1", "for": "verification_cleared", "timeout_ms": 5_000}
    )
    assert waited["satisfied"] is True
    assert "elapsed_ms" in waited and "url" in waited

    resumed = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": query, "engine": "auto", "interstitial": "handoff"},
    )
    assert resumed["path"] == "handoff_cleared"
    assert resumed["provider"] == "google"
    assert resumed["state"] == "ok"
    assert resumed["result_count"] == 2

    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_wait_verification_cleared_times_out_while_still_on_interstitial(
    handoff_worker, site_url
) -> None:
    worker = handoff_worker
    await worker._execute("browser.session_open", _open_payload())
    await worker._execute(
        "browser.search",
        {
            "session_id": "s1",
            "query": "yapay zeka ajanları",
            "engine": "auto",
            "interstitial": "handoff",
        },
    )

    waited = await worker._execute(
        "browser.wait", {"session_id": "s1", "for": "verification_cleared", "timeout_ms": 800}
    )
    assert waited["satisfied"] is False

    await worker._execute("browser.session_close", {"session_id": "s1"})


# --------------------------------------------------------------------------- #
# (d) fetch_evidence tab=new leaves the results tab selected
# --------------------------------------------------------------------------- #


async def test_fetch_evidence_tab_new_keeps_results_tab_selected(
    google_worker, site_url
) -> None:
    worker = google_worker
    await worker._execute("browser.session_open", _open_payload())
    await worker._execute(
        "browser.search", {"session_id": "s1", "query": "yapay zeka ajanları", "engine": "auto"}
    )

    tabs_before = await worker._execute("browser.tab_list", {"session_id": "s1"})
    assert len(tabs_before["tabs"]) == 1
    before = await worker._execute("browser.inspect", {"session_id": "s1"})
    assert "google-results.html" in before["url"]

    result = await worker._execute(
        "browser.fetch_evidence",
        {
            "session_id": "s1",
            "url": f"{site_url}/article.html",
            "query": "q",
            "source_class": "news",
            "tab": "new",
        },
    )
    assert result["tab_used"] == "new"
    assert result["page_kind"] == "ok"
    assert result["final_url"].endswith("article.html")

    tabs_after = await worker._execute("browser.tab_list", {"session_id": "s1"})
    assert len(tabs_after["tabs"]) == len(tabs_before["tabs"])
    after = await worker._execute("browser.inspect", {"session_id": "s1"})
    assert "google-results.html" in after["url"]

    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_fetch_evidence_tab_same_is_the_default(google_worker, site_url) -> None:
    worker = google_worker
    await worker._execute("browser.session_open", _open_payload())

    result = await worker._execute(
        "browser.fetch_evidence",
        {"session_id": "s1", "url": f"{site_url}/article.html", "query": "q"},
    )
    assert result["tab_used"] == "same"
    tabs = await worker._execute("browser.tab_list", {"session_id": "s1"})
    assert len(tabs["tabs"]) == 1

    await worker._execute("browser.session_close", {"session_id": "s1"})


# --------------------------------------------------------------------------- #
# (e) interstitial="fallback" still falls back to duckduckgo (fixture)
# --------------------------------------------------------------------------- #


async def test_interstitial_fallback_falls_back_to_duckduckgo(
    tmp_path, site_url, monkeypatch
) -> None:
    worker = await _make_worker(
        tmp_path,
        google_base_url=f"{site_url}/google-home.html?simulate=sorry",
        name="fallback-worker-data",
    )
    real_build_search_url = search_engines.build_search_url

    def fake_build_search_url(engine, query, *, recency_days=None, locale=None):
        if engine == "duckduckgo":
            return f"{site_url}/duckduckgo-results.html"
        return real_build_search_url(engine, query, recency_days=recency_days, locale=locale)

    monkeypatch.setattr(search_engines, "build_search_url", fake_build_search_url)

    try:
        await worker._execute("browser.session_open", _open_payload())
        outcome = await worker._execute(
            "browser.search",
            {
                "session_id": "s1",
                "query": "ai agents",
                "engine": "auto",
                "interstitial": "fallback",
            },
        )
        assert outcome["provider"] == "duckduckgo"
        assert outcome["requested_provider"] == "google"
        assert outcome["fallback"] is True
        assert outcome["path"] == "fallback"
        assert outcome["state"] == "ok"
        assert outcome["result_count"] == 1
        assert [a["outcome"] for a in outcome["attempts"]] == ["captcha", "ok"]
        await worker._execute("browser.session_close", {"session_id": "s1"})
    finally:
        await worker._close_all_sessions()
