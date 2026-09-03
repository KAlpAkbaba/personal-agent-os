"""M13 browser e2e scenarios for the stdio worker (real Chromium, in-process driver).

Each test calls ``await worker._execute(capability, payload)`` directly
against the ``worker`` fixture (tests/conftest.py) — a real
:class:`~browser_agent.worker.Worker` instance driving real (headless)
Chromium, but without spawning an actual OS subprocess or doing real stdio
JSON-line I/O for every call (that full real-stdio path is covered once, end
to end, by test_worker_stdio_e2e.py). This keeps the scenario matrix fast
while still exercising the real dispatch/policy/page_kind/session code paths
against a real browser and the real fixture site.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import time

import pytest

from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.worker import Worker

pytestmark = pytest.mark.browser


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _open_session(
    worker: Worker,
    session_id: str = "s1",
    *,
    classes: tuple[str, ...] = ("READ", "NAVIGATE"),
    profile: str = "isolated",
) -> dict:
    return await worker._execute(
        "browser.session_open",
        {
            "session_id": session_id,
            "profile": profile,
            "policy": {"allowed_risk_classes": list(classes), "visible": False},
        },
    )


# --------------------------------------------------------------------------- #
# hostile page: report only, never act
# --------------------------------------------------------------------------- #


async def test_hostile_page_fetch_evidence_only_reports_never_acts(worker, site_url) -> None:
    await _open_session(worker)
    downloads_dir = worker._data_dir / "downloads"

    result = await worker._execute(
        "browser.fetch_evidence",
        {
            "session_id": "s1",
            "url": f"{site_url}/hostile.html",
            "query": "injection test",
            "source_class": "unknown",
        },
    )

    assert result["page_kind"] == "ok"
    assert result["injection_markers"] > 0
    # The worker landed on (and stayed on) the hostile page itself — it never
    # followed the "click here to continue" link, submitted the auto-submit
    # form, or navigated anywhere else on its own.
    assert result["final_url"].endswith("hostile.html")
    assert "Ignore previous instructions" in result["excerpt"] or "reveal" in result["excerpt"]
    # No download was ever triggered by the lure link.
    assert not downloads_dir.exists() or not any(downloads_dir.iterdir())

    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_hostile_page_extract_reports_links_as_data_not_actions(worker, site_url) -> None:
    await _open_session(worker)
    await worker._execute(
        "browser.navigate", {"session_id": "s1", "url": f"{site_url}/hostile.html"}
    )
    result = await worker._execute(
        "browser.extract", {"session_id": "s1", "mode": "all", "max_chars": 24000}
    )
    assert result["page_kind"] == "ok"
    hrefs = [link["href"] for link in result["links"]]
    assert any(href.endswith("result.html") for href in hrefs)
    assert any(href.endswith("files/sample.txt") for href in hrefs)
    # The links are reported, not followed: we are still on hostile.html.
    assert result["url"].endswith("hostile.html")
    await worker._execute("browser.session_close", {"session_id": "s1"})


# --------------------------------------------------------------------------- #
# page_kind classification
# --------------------------------------------------------------------------- #


async def test_auth_wall_page_kind(worker, site_url) -> None:
    await _open_session(worker)
    result = await worker._execute(
        "browser.navigate", {"session_id": "s1", "url": f"{site_url}/auth-wall.html"}
    )
    assert result["page_kind"] == "auth_wall"
    assert result["site_error"]["kind"] == "auth_wall"
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_captcha_page_kind(worker, site_url) -> None:
    await _open_session(worker)
    result = await worker._execute(
        "browser.navigate", {"session_id": "s1", "url": f"{site_url}/captcha.html"}
    )
    assert result["page_kind"] == "captcha"
    assert result["site_error"]["kind"] == "captcha"
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_error_503_is_page_kind_error_page_command_still_succeeds(worker, site_url) -> None:
    await _open_session(worker)
    # No exception: a website error is a *successful* command result.
    result = await worker._execute(
        "browser.navigate", {"session_id": "s1", "url": f"{site_url}/error503"}
    )
    assert result["page_kind"] == "error_page"
    assert result["http_status"] == 503
    assert result["site_error"]["kind"] == "http_error"
    assert result["site_error"]["http_status"] == 503
    await worker._execute("browser.session_close", {"session_id": "s1"})


# --------------------------------------------------------------------------- #
# browser-level (transport) errors
# --------------------------------------------------------------------------- #


async def test_dead_port_navigation_is_dependency_unavailable(worker) -> None:
    await _open_session(worker)
    dead_url = f"http://127.0.0.1:{_free_port()}/index.html"
    with pytest.raises(BrowserError) as exc_info:
        await worker._execute(
            "browser.navigate", {"session_id": "s1", "url": dead_url, "timeout_ms": 5000}
        )
    assert exc_info.value.error_class == ErrorClass.DEPENDENCY_UNAVAILABLE
    assert exc_info.value.retryable is True
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_navigation_timeout_is_typed_timeout(worker, site_url) -> None:
    await _open_session(worker)
    with pytest.raises(BrowserError) as exc_info:
        await worker._execute(
            "browser.navigate", {"session_id": "s1", "url": f"{site_url}/slow", "timeout_ms": 800}
        )
    assert exc_info.value.error_class == ErrorClass.TIMEOUT
    assert exc_info.value.retryable is True
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_tab_closed_unexpectedly_then_tab_select_recovers(worker, site_url) -> None:
    await _open_session(worker)
    await worker._execute("browser.navigate", {"session_id": "s1", "url": f"{site_url}/index.html"})
    await worker._execute("browser.tab_new", {"session_id": "s1", "url": f"{site_url}/second.html"})
    # Tab 1 (second.html) is now current. Close it directly (not through
    # browser.tab_close) to simulate an *unexpected* external closure (a
    # crash, or the page closing itself) rather than a deliberate command.
    state = worker._sessions["s1"]
    stale_page = state.browser_session.backend.current_page
    await stale_page.close()

    # NOTE (deviation from the task brief's literal wording): the underlying
    # M2 backend layer (backends.py `current_page` property) already types a
    # closed *current* page as `dependency_unavailable` (retryable) rather
    # than `ui_state_changed` — that pre-existing, already-tested M2 behavior
    # was kept rather than special-cased for M13, since changing it would
    # affect the whole session/backend layer, not just the worker. The
    # recovery story is the same either way: the next operation on this tab
    # fails typed+retryable, and selecting a still-open tab recovers cleanly.
    with pytest.raises(BrowserError) as exc_info:
        await worker._execute("browser.inspect", {"session_id": "s1"})
    assert exc_info.value.error_class == ErrorClass.DEPENDENCY_UNAVAILABLE

    tabs = await worker._execute("browser.tab_list", {"session_id": "s1"})
    assert len(tabs["tabs"]) == 1  # the closed tab is filtered out

    recovered = await worker._execute(
        "browser.tab_select", {"session_id": "s1", "index": tabs["tabs"][0]["index"]}
    )
    assert recovered["page_kind"] == "ok"
    inspect_result = await worker._execute("browser.inspect", {"session_id": "s1"})
    assert inspect_result["url"].endswith("index.html")

    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_browser_process_killed_then_next_session_open_recreates(worker, site_url) -> None:
    await _open_session(worker)
    await worker._execute("browser.navigate", {"session_id": "s1", "url": f"{site_url}/index.html"})
    state = worker._sessions["s1"]
    native_browser = state.backend.native_browser
    assert native_browser is not None
    cdp = await native_browser.new_browser_cdp_session()
    info = await cdp.send("SystemInfo.getProcessInfo")
    pid = next(p["id"] for p in info["processInfo"] if p["type"] == "browser")

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and await state.backend.is_alive():
        await asyncio.sleep(0.1)
    assert not await state.backend.is_alive()

    with pytest.raises(BrowserError) as exc_info:
        await worker._execute(
            "browser.navigate", {"session_id": "s1", "url": f"{site_url}/index.html"}
        )
    assert exc_info.value.error_class == ErrorClass.DEPENDENCY_UNAVAILABLE

    reopened = await _open_session(worker)
    assert reopened["created"] is True  # a fresh session, not a reuse of the dead one
    result = await worker._execute(
        "browser.navigate", {"session_id": "s1", "url": f"{site_url}/index.html"}
    )
    assert result["page_kind"] == "ok"

    await worker._execute("browser.session_close", {"session_id": "s1"})


# --------------------------------------------------------------------------- #
# concurrency: per-session serial, cross-session concurrent
# --------------------------------------------------------------------------- #


async def test_per_session_serial_vs_cross_session_concurrent(worker, site_url) -> None:
    await _open_session(worker, "s1")
    await _open_session(worker, "s2")
    for sid in ("s1", "s2"):
        await worker._execute(
            "browser.navigate", {"session_id": sid, "url": f"{site_url}/index.html"}
        )

    async def wait_call(session_id: str) -> dict:
        return await worker._execute(
            "browser.wait",
            {
                "session_id": session_id,
                "for": "text",
                "text": "text-that-will-never-appear-xyz",
                "timeout_ms": 1200,
            },
        )

    start = time.monotonic()
    same_session_results = await asyncio.gather(wait_call("s1"), wait_call("s1"))
    same_session_elapsed = time.monotonic() - start
    assert all(r["satisfied"] is False for r in same_session_results)
    # Serialized: two ~1.2s waits on the SAME session take roughly 2x as long.
    assert same_session_elapsed >= 2.0

    start = time.monotonic()
    cross_session_results = await asyncio.gather(wait_call("s1"), wait_call("s2"))
    cross_session_elapsed = time.monotonic() - start
    assert all(r["satisfied"] is False for r in cross_session_results)
    # Concurrent: one ~1.2s wait each on DIFFERENT sessions overlaps.
    assert cross_session_elapsed < same_session_elapsed

    await worker._execute("browser.session_close", {"session_id": "s1"})
    await worker._execute("browser.session_close", {"session_id": "s2"})


# --------------------------------------------------------------------------- #
# risk-class enforcement
# --------------------------------------------------------------------------- #


async def test_click_submit_button_refused_in_read_navigate_session(worker, site_url) -> None:
    await _open_session(worker, classes=("READ", "NAVIGATE"))
    await worker._execute("browser.navigate", {"session_id": "s1", "url": f"{site_url}/form.html"})
    with pytest.raises(BrowserError) as exc_info:
        await worker._execute(
            "browser.click",
            {"session_id": "s1", "target": {"role": "button", "name": "Submit form"}},
        )
    err = exc_info.value
    assert err.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    assert err.retryable is False
    assert "EXTERNAL_COMMUNICATION" in err.message
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_click_plain_link_allowed_in_read_navigate_session(worker, site_url) -> None:
    await _open_session(worker, classes=("READ", "NAVIGATE"))
    await worker._execute("browser.navigate", {"session_id": "s1", "url": f"{site_url}/index.html"})
    result = await worker._execute(
        "browser.click",
        {"session_id": "s1", "target": {"role": "link", "name": "Second page"}},
    )
    assert result["clicked"] is True
    assert result["resolved"]["role"] == "link"
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_download_refused_without_authorization_ref(worker, site_url) -> None:
    await _open_session(worker, classes=("READ", "NAVIGATE", "HIGH_IMPACT"))
    await worker._execute("browser.navigate", {"session_id": "s1", "url": f"{site_url}/index.html"})
    with pytest.raises(BrowserError) as exc_info:
        await worker._execute(
            "browser.download",
            {
                "session_id": "s1",
                "target": {"role": "link", "name": "Download sample"},
            },
        )
    assert exc_info.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_download_with_authorization_ref_succeeds(worker, site_url) -> None:
    await _open_session(worker, classes=("READ", "NAVIGATE", "HIGH_IMPACT"))
    await worker._execute("browser.navigate", {"session_id": "s1", "url": f"{site_url}/index.html"})
    result = await worker._execute(
        "browser.download",
        {
            "session_id": "s1",
            "target": {"role": "link", "name": "Download sample"},
            "authorization_ref": "owner-approved-1234",
        },
    )
    assert result["sha256"]
    assert result["bytes"] > 0
    assert result["path"].startswith(str(worker._data_dir / "downloads"))
    await worker._execute("browser.session_close", {"session_id": "s1"})


# --------------------------------------------------------------------------- #
# extract: metadata + JSON-LD
# --------------------------------------------------------------------------- #


async def test_extract_metadata_from_article_fixture(worker, site_url) -> None:
    await _open_session(worker)
    await worker._execute(
        "browser.navigate", {"session_id": "s1", "url": f"{site_url}/article.html"}
    )
    result = await worker._execute("browser.extract", {"session_id": "s1", "mode": "metadata"})
    metadata = result["metadata"]
    assert metadata["canonical_url"] == "https://example.com/agents-ship-faster-now"
    assert metadata["published_at"] == "2026-09-01T10:00:00Z"
    assert metadata["modified_at"] == "2026-09-02T08:30:00Z"
    assert metadata["publisher"] == "Fixture Times"
    assert metadata["author"] == "Ada Researcher"
    assert metadata["language"] == "en"
    assert metadata["description"] == "A fixture article about agent shipping velocity."
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_extract_structured_json_ld(worker, site_url) -> None:
    await _open_session(worker)
    await worker._execute(
        "browser.navigate", {"session_id": "s1", "url": f"{site_url}/article.html"}
    )
    result = await worker._execute("browser.extract", {"session_id": "s1", "mode": "structured"})
    json_ld = result["structured"]["json_ld"]
    assert len(json_ld) == 1
    assert json_ld[0]["@type"] == "NewsArticle"


# --------------------------------------------------------------------------- #
# session_open reopen semantics: never widens
# --------------------------------------------------------------------------- #


async def test_reopen_never_widens_policy(worker) -> None:
    first = await _open_session(worker, classes=("READ", "NAVIGATE"))
    assert first["created"] is True
    assert sorted(first["policy"]["allowed_risk_classes"]) == ["NAVIGATE", "READ"]

    widened = await _open_session(worker, classes=("READ", "NAVIGATE", "HIGH_IMPACT"))
    assert widened["created"] is False
    # HIGH_IMPACT was requested but must not be granted — intersection only.
    assert sorted(widened["policy"]["allowed_risk_classes"]) == ["NAVIGATE", "READ"]

    narrowed = await _open_session(worker, classes=("READ",))
    assert narrowed["created"] is False
    assert narrowed["policy"]["allowed_risk_classes"] == ["READ"]

    await worker._execute("browser.session_close", {"session_id": "s1"})
