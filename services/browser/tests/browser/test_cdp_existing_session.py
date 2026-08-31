"""E2E: existing-session (CDP) route.

Launches a separate Chromium with --remote-debugging-port on a random free
port (standing in for an owner's already-running Chrome/Edge), then attaches
via BrowserSession.connect_existing_cdp. No real user browser is touched.
"""

import socket
from collections.abc import AsyncIterator

import pytest
from playwright.async_api import async_playwright

from browser_agent import BrowserError, BrowserSession, ErrorClass, TargetSpec

pytestmark = pytest.mark.browser


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
async def external_chromium() -> AsyncIterator[str]:
    """A separately-launched headless Chromium exposing a CDP endpoint."""
    port = _free_port()
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=True, args=[f"--remote-debugging-port={port}"]
    )
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await browser.close()
        await playwright.stop()


async def test_connect_navigate_click_over_cdp(external_chromium: str, site_url: str) -> None:
    session = await BrowserSession.connect_existing_cdp(external_chromium)
    try:
        await session.navigate(f"{site_url}/index.html")
        await session.click(TargetSpec(role="button", name="Greet"))
        greeting = await session.read_text(TargetSpec(test_id="greeting"))
        assert greeting == "Hello, Agent!"
    finally:
        await session.close()  # disconnects; the external browser stays up


async def test_unreachable_cdp_endpoint_is_dependency_unavailable(site_url: str) -> None:
    dead_endpoint = f"http://127.0.0.1:{_free_port()}"
    with pytest.raises(BrowserError) as excinfo:
        await BrowserSession.connect_existing_cdp(dead_endpoint, timeout_ms=3000)
    assert excinfo.value.error_class is ErrorClass.DEPENDENCY_UNAVAILABLE
    assert excinfo.value.retryable is True
    assert excinfo.value.evidence["endpoint_url"] == dead_endpoint
