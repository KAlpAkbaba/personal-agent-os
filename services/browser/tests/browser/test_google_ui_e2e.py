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

import json
from urllib.parse import quote

import pytest

from browser_agent import search_engines
from browser_agent.errors import BrowserError
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


async def test_google_search_via_ui_types_query_and_parses_results(google_worker, site_url) -> None:
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
    assert outcome["schema_version"] == 3
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


async def test_handoff_interstitial_then_clearance_then_resume(handoff_worker, site_url) -> None:
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
    assert pending["page_kind"] == "waiting"
    assert pending["verification_url"]
    # schema 3: the interstitial kind is evidence exactly once, here
    assert pending["verification"] == {
        "handoffs": 1,
        "outcome": "pending",
        "interstitial": "captcha",
        "verification_url": pending["verification_url"],
    }
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
    assert resumed["fallback"] is False and resumed["fallback_reason"] is None
    assert resumed["verification"]["outcome"] == "cleared"
    assert resumed["verification"]["interstitial"] == "captcha"

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


async def test_fetch_evidence_tab_new_keeps_results_tab_selected(google_worker, site_url) -> None:
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


# --------------------------------------------------------------------------- #
# (e) owner handoff policy (2026-09-04): search modes, retry once, never loop
# --------------------------------------------------------------------------- #


def _ddg_fixture(monkeypatch, site_url: str) -> None:
    real_build_search_url = search_engines.build_search_url

    def fake_build_search_url(engine, query, *, recency_days=None, locale=None):
        if engine == "duckduckgo":
            return f"{site_url}/duckduckgo-results.html"
        return real_build_search_url(engine, query, recency_days=recency_days, locale=locale)

    monkeypatch.setattr(search_engines, "build_search_url", fake_build_search_url)


async def test_search_mode_unattended_is_the_deterministic_fallback(
    handoff_worker, site_url, monkeypatch
) -> None:
    _ddg_fixture(monkeypatch, site_url)
    worker = handoff_worker
    await worker._execute("browser.session_open", _open_payload())
    outcome = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": "ai agents", "engine": "auto", "mode": "unattended"},
    )
    assert outcome["mode"] == "unattended"
    assert outcome["provider"] == "duckduckgo" and outcome["fallback"] is True
    assert outcome["fallback_reason"] == "google:captcha"
    assert outcome["verification_handoffs"] == 0
    assert outcome["verification"] == {
        "handoffs": 0,
        "outcome": None,
        "interstitial": None,
        "verification_url": None,
    }
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_search_mode_interactive_hands_off_and_rejects_unknown_mode(
    handoff_worker, site_url
) -> None:
    worker = handoff_worker
    await worker._execute("browser.session_open", _open_payload())
    with pytest.raises(BrowserError):
        await worker._execute(
            "browser.search", {"session_id": "s1", "query": "x", "mode": "whatever"}
        )
    pending = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": "ai agents", "engine": "auto", "mode": "interactive"},
    )
    assert pending["state"] == "waiting_for_owner_verification"
    assert pending["mode"] == "interactive"
    assert pending["verification_handoffs"] == 1
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_handoff_timeout_fallback_never_attempts_google_again(
    handoff_worker, site_url, monkeypatch
) -> None:
    """The owner did not complete the page: a fallback-mode search on the still
    pending query records the interstitial and goes to DuckDuckGo WITHOUT a
    second Google navigation (path=handoff_timeout_fallback)."""
    _ddg_fixture(monkeypatch, site_url)
    worker = handoff_worker
    await worker._execute("browser.session_open", _open_payload())
    query = "ai agents"
    pending = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": query, "engine": "auto", "interstitial": "handoff"},
    )
    assert pending["state"] == "waiting_for_owner_verification"
    interstitial_url = pending["verification_url"]

    # asking again in handoff mode while still blocked: same pending state, no new page
    again = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": query, "engine": "auto", "interstitial": "handoff"},
    )
    assert again["state"] == "waiting_for_owner_verification"
    assert again["verification_url"] == interstitial_url
    assert again["verification_handoffs"] == 1

    outcome = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": query, "engine": "auto", "interstitial": "fallback"},
    )
    assert outcome["provider"] == "duckduckgo"
    assert outcome["fallback"] is True
    assert outcome["fallback_reason"] == "google:verification_timeout"
    assert outcome["path"] == "handoff_timeout_fallback"
    assert [a["outcome"] for a in outcome["attempts"]] == ["verification_timeout", "ok"]
    assert "not retried" in outcome["attempts"][0]["detail"]
    assert outcome["requested_provider"] == "google"
    assert outcome["verification"] == {
        "handoffs": 1,
        "outcome": "timeout",
        "interstitial": "captcha",
        "verification_url": interstitial_url,
    }
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_second_interstitial_after_clearance_is_not_handed_off_again(
    handoff_worker, site_url, monkeypatch
) -> None:
    """Retry once, never loop: after one cleared verification, a further
    interstitial in the same session is recorded and the provider fallback
    applies (path=handoff_repeat_fallback) instead of a second handoff."""
    _ddg_fixture(monkeypatch, site_url)
    worker = handoff_worker
    await worker._execute("browser.session_open", _open_payload())
    query = "yapay zeka ajanları"
    pending = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": query, "engine": "auto", "mode": "interactive"},
    )
    assert pending["state"] == "waiting_for_owner_verification"
    # the owner completes the page: Google returns the results
    await worker._execute(
        "browser.navigate",
        {"session_id": "s1", "url": f"{site_url}/google-results.html?q={quote(query)}"},
    )
    waited = await worker._execute(
        "browser.wait", {"session_id": "s1", "for": "verification_cleared", "timeout_ms": 5_000}
    )
    assert waited["satisfied"] is True
    resumed = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": query, "engine": "auto", "mode": "interactive"},
    )
    assert resumed["path"] == "handoff_cleared" and resumed["provider"] == "google"

    # Google blocks the NEXT query of the same session again
    await worker._execute(
        "browser.navigate", {"session_id": "s1", "url": f"{site_url}/google-sorry.html"}
    )
    outcome = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": "başka bir sorgu", "engine": "auto", "mode": "interactive"},
    )
    assert outcome["state"] == "ok"
    assert outcome["provider"] == "duckduckgo"
    assert outcome["fallback"] is True
    assert outcome["fallback_reason"] == "google:interstitial_after_verification"
    assert outcome["path"] == "handoff_repeat_fallback"
    assert outcome["verification_handoffs"] == 1
    assert outcome["verification"]["outcome"] == "repeat"
    assert outcome["verification"]["interstitial"] == "captcha"
    assert "not handed off a second time" in outcome["attempts"][0]["detail"]
    await worker._execute("browser.session_close", {"session_id": "s1"})


# --------------------------------------------------------------------------- #
# (f) 2026-09-04 owner incident: consent flow, owner declines, evidence shape
# --------------------------------------------------------------------------- #


@pytest.fixture()
async def consent_worker(tmp_path, site_url):
    """First Google search lands on the consent fixture (google-consent.html)."""
    worker = await _make_worker(
        tmp_path,
        google_base_url=f"{site_url}/google-home.html?simulate=consent",
        name="consent-worker-data",
    )
    try:
        yield worker
    finally:
        await worker._close_all_sessions()


async def test_consent_interstitial_handoff_then_clearance_then_resume(
    consent_worker, site_url
) -> None:
    worker = consent_worker
    await worker._execute("browser.session_open", _open_payload())
    query = "yapay zeka ajanları"
    pending = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": query, "engine": "auto", "mode": "interactive"},
    )
    assert pending["state"] == "waiting_for_owner_verification"
    assert pending["verification"]["interstitial"] == "consent"
    assert pending["verification"]["outcome"] == "pending"
    assert [a["outcome"] for a in pending["attempts"]] == ["verification_pending"]
    # the owner accepts/rejects on the consent page: Google continues to the results
    await worker._execute(
        "browser.navigate",
        {"session_id": "s1", "url": f"{site_url}/google-results.html?q={quote(query)}"},
    )
    waited = await worker._execute(
        "browser.wait", {"session_id": "s1", "for": "verification_cleared", "timeout_ms": 5_000}
    )
    assert waited["satisfied"] is True
    resumed = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": query, "engine": "auto", "mode": "interactive"},
    )
    assert resumed["path"] == "handoff_cleared" and resumed["provider"] == "google"
    assert resumed["verification"] == {
        "handoffs": 1,
        "outcome": "cleared",
        "interstitial": "consent",
        "verification_url": pending["verification"]["verification_url"],
    }
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_consent_timeout_then_owner_fallback(consent_worker, site_url, monkeypatch) -> None:
    _ddg_fixture(monkeypatch, site_url)
    worker = consent_worker
    await worker._execute("browser.session_open", _open_payload())
    pending = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": "ai agents", "engine": "auto", "mode": "interactive"},
    )
    assert pending["verification"]["interstitial"] == "consent"
    outcome = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": "ai agents", "engine": "auto", "interstitial": "fallback"},
    )
    assert outcome["provider"] == "duckduckgo"
    assert outcome["fallback_reason"] == "google:verification_timeout"
    assert outcome["verification"]["outcome"] == "timeout"
    assert outcome["verification"]["interstitial"] == "consent"
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_timeout_then_owner_declines_closes_the_session_cleanly(handoff_worker) -> None:
    """CAPTCHA -> timeout -> the owner declines the fallback: no further search is
    issued, session_close tears the browser down like any other session."""
    worker = handoff_worker
    await worker._execute("browser.session_open", _open_payload())
    pending = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": "ai agents", "engine": "auto", "mode": "interactive"},
    )
    assert pending["state"] == "waiting_for_owner_verification"
    waited = await worker._execute(
        "browser.wait", {"session_id": "s1", "for": "verification_cleared", "timeout_ms": 700}
    )
    assert waited["satisfied"] is False
    closed = await worker._execute("browser.session_close", {"session_id": "s1"})
    assert closed["closed"] is True and closed["browser_pid_exited"] is True
    assert "s1" not in worker._sessions


async def test_search_evidence_names_the_interstitial_exactly_once(
    handoff_worker, site_url, monkeypatch
) -> None:
    """Schema 3: the interstitial kind is evidence in ``verification`` only - never at the
    top level, never as an attempt outcome on the handoff paths."""
    _ddg_fixture(monkeypatch, site_url)
    worker = handoff_worker
    await worker._execute("browser.session_open", _open_payload())
    pending = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": "ai agents", "engine": "auto", "mode": "interactive"},
    )
    fallback = await worker._execute(
        "browser.search",
        {"session_id": "s1", "query": "ai agents", "engine": "auto", "interstitial": "fallback"},
    )
    for result in (pending, fallback):
        assert result["schema_version"] == 3
        serialized = json.dumps(result)
        assert serialized.count('"interstitial"') == 1
        assert result["page_kind"] != "captcha"
        assert "captcha" not in [a["outcome"] for a in result["attempts"]]
        assert result["verification"]["interstitial"] == "captcha"
    await worker._execute("browser.session_close", {"session_id": "s1"})
