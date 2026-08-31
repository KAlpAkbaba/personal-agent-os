"""Shared fixtures: local fixture-site HTTP server and browser sessions.

Tests never touch the internet: the fixture site under tests/fixtures/site is
served by a stdlib http.server on a random loopback port.
"""

import threading
from collections.abc import AsyncIterator, Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from browser_agent import BrowserSession

FIXTURE_SITE = Path(__file__).parent / "fixtures" / "site"
SAMPLE_FILE = FIXTURE_SITE / "files" / "sample.txt"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # keep pytest output clean


@pytest.fixture(scope="session")
def site_url() -> Iterator[str]:
    """Serve the static fixture site on a random localhost port."""
    handler = partial(_QuietHandler, directory=str(FIXTURE_SITE))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
async def session() -> AsyncIterator[BrowserSession]:
    """Dedicated headless Chromium session (browser-marked tests only)."""
    browser_session = await BrowserSession.launch_dedicated(headless=True)
    try:
        yield browser_session
    finally:
        await browser_session.close()
