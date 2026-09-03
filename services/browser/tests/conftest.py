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
import os as _os
import subprocess as _subprocess
import sys as _sys
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
        if self.path.split("?", 1)[0] == "/error503":
            self._send_html(
                503,
                "<!DOCTYPE html><html><head><title>Service Unavailable</title></head>"
                "<body><h1>503 - try again later</h1></body></html>",
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


@pytest.fixture()
async def worker(tmp_path):
    """A real M13 :class:`~browser_agent.worker.Worker`, driven in-process.

    Uses the ``chromium`` channel (Playwright's bundled build — CI-friendly,
    no system Chrome install required) so ``-m browser`` stays deterministic;
    the live/real-Chrome path is exercised separately under ``-m live``.
    Tests call ``await worker._execute(capability, payload)`` directly
    (no stdio/subprocess) except the one dedicated real-stdio-subprocess
    scenario (tests/browser/test_worker_stdio_e2e.py).
    """
    from browser_agent.worker import Worker, build_arg_parser

    data_dir = tmp_path / "worker-data"
    data_dir.mkdir()
    args = build_arg_parser().parse_args(
        # The fixture site lives on loopback, which the destination policy refuses
        # in production; the flag exists for exactly this suite.
        [
            "--data-dir",
            str(data_dir),
            "--channel",
            "chromium",
            "--headless",
            "--allow-private-destinations",
        ]
    )
    w = Worker(args)
    await w._print_hello()  # populates browser_info; also sanity-checks the channel resolves
    try:
        yield w
    finally:
        await w._close_all_sessions()


# ---------------------------------------------------------------------------
# Desktop hygiene guards (2026-09-03): a background test iteration on the owner's
# machine opened dozens of "Chrome for Testing" windows. No test outside the `live`
# marker may launch a visible browser, and every Chromium this pytest process spawned
# must be gone when the session ends, whatever the tests did.

_POWERSHELL = _os.path.join(
    _os.environ.get("SystemRoot", r"C:/Windows"),
    "System32",
    "WindowsPowerShell",
    "v1.0",
    "powershell.exe",
)


@pytest.fixture(autouse=True)
def _headless_only_outside_live(request, monkeypatch):
    """Force every ManagedBackend to headless unless the test is marked `live`."""
    if request.node.get_closest_marker("live") is not None:
        yield
        return
    from browser_agent import backends as _backends

    original_init = _backends.ManagedBackend.__init__

    def headless_init(self, *args, **kwargs):
        kwargs["headless"] = True
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(_backends.ManagedBackend, "__init__", headless_init)
    yield


def _reap_chromium_spawned_by_this_pytest() -> None:
    """Kill Playwright Chromium roots whose ancestor is this pytest process (Windows)."""
    if _sys.platform != "win32" or not _os.path.exists(_POWERSHELL):
        return
    lines = [
        "$me = " + str(_os.getpid()) + ";",
        "$all = Get-CimInstance Win32_Process;",
        "$byId = @{}; foreach ($p in $all) { $byId[[int]$p.ProcessId] = $p };",
        "function Is-Descendant($pid0) { $cur = $pid0; for ($i = 0; $i -lt 12; $i++) {",
        "  if ($cur -eq $me) { return $true };",
        "  if (-not $byId.ContainsKey($cur)) { return $false };",
        "  $cur = [int]$byId[$cur].ParentProcessId; if ($cur -le 0) { return $false } };",
        "  return $false };",
        "foreach ($p in $all) { if (($p.Name -eq 'chrome.exe'",
        "  -or $p.Name -eq 'chrome-headless-shell.exe')",
        "  -and $p.ExecutablePath -like '*ms-playwright*'",
        "  -and $p.CommandLine -notlike '*--type=*'",
        "  -and (Is-Descendant ([int]$p.ProcessId))) {",
        "  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue;",
        "  Write-Output ('reaped ' + $p.ProcessId) } }",
    ]
    script = " ".join(lines)
    try:
        out = _subprocess.run(
            [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if out.stdout.strip():
            print("[conftest] leftover Chromium reaped:", " ".join(out.stdout.split()))
    except Exception:  # noqa: BLE001 - hygiene must never fail the suite
        pass


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    _reap_chromium_spawned_by_this_pytest()
