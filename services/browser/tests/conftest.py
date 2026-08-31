"""Shared fixtures: local fixture-site HTTP server and browser sessions.

Tests never touch the internet: the fixture site under tests/fixtures/site is
served by a stdlib http.server on a random loopback port. Two dynamic
endpoints supplement the static pages:

- ``POST /upload`` — accepts a multipart form upload and responds with an
  HTML page echoing the SHA-256 of the uploaded file bytes
  (``data-testid="upload-hash"``), so the upload scenario is asserted
  end-to-end against what the *server* received.
- ``GET /slow`` — sleeps ~5s before responding; used for deterministic
  navigation-timeout and cancellation scenarios.
"""

import hashlib
import threading
import time
from collections.abc import AsyncIterator, Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from browser_agent import BrowserSession

FIXTURE_SITE = Path(__file__).parent / "fixtures" / "site"
SAMPLE_FILE = FIXTURE_SITE / "files" / "sample.txt"

SLOW_ENDPOINT_DELAY_S = 5.0


def _extract_multipart_file(body: bytes, boundary: bytes) -> bytes | None:
    """Return the bytes of the first file part in a multipart/form-data body.

    Deterministic minimal parser for the fixture form's well-formed payload:
    parts are delimited by ``--<boundary>``; the file part carries a
    ``filename=`` disposition; part content starts after the blank line and
    ends before the trailing CRLF of the delimiter.
    """
    delimiter = b"--" + boundary
    for section in body.split(delimiter):
        if b'filename="' not in section:
            continue
        header_end = section.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        content = section[header_end + 4 :]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        return content
    return None


class _FixtureSiteHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # keep pytest output clean

    def _send_html(self, status: int, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.split("?", 1)[0] == "/slow":
            time.sleep(SLOW_ENDPOINT_DELAY_S)
            self._send_html(
                200,
                "<!DOCTYPE html><html><head><title>Slow</title></head>"
                "<body><h1>Slow page</h1></body></html>",
            )
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        if self.path.split("?", 1)[0] != "/upload":
            self._send_html(404, "<h1>Not found</h1>")
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        file_bytes: bytes | None = None
        if "boundary=" in content_type:
            boundary = content_type.split("boundary=", 1)[1].strip('"').encode("ascii")
            file_bytes = _extract_multipart_file(body, boundary)
        if file_bytes is None:
            self._send_html(400, "<h1>Bad upload</h1>")
            return
        digest = hashlib.sha256(file_bytes).hexdigest()
        self._send_html(
            200,
            "<!DOCTYPE html><html><head><title>Upload Result</title></head><body>"
            "<h1>Upload Result</h1>"
            f'<div data-testid="upload-hash">{digest}</div>'
            "</body></html>",
        )


@pytest.fixture(scope="session")
def site_url() -> Iterator[str]:
    """Serve the fixture site (static + dynamic endpoints) on a random port."""
    handler = partial(_FixtureSiteHandler, directory=str(FIXTURE_SITE))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
async def session() -> AsyncIterator[BrowserSession]:
    """Dedicated isolated headless Chromium session (browser-marked tests only)."""
    browser_session = await BrowserSession.launch_dedicated(
        headless=True, file_io_root=FIXTURE_SITE
    )
    try:
        yield browser_session
    finally:
        await browser_session.close()
