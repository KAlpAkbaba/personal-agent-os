"""browser-redirect-guard (cycle d20261003): every request the worker's browser makes is
held to the destination policy - redirect hops and sub-requests included - and
``fetch_evidence`` gains ``selector`` / ``text_sha256`` / ``selector_matched``.

Why: the worker checked only the REQUESTED url. A public page that redirects - or a
sub-request - into the tailnet (100.64.0.0/10, Cloud Core's own API at 100.90.158.26:8001),
loopback or link-local went through unchecked, and the cloud worker sits on the Cloud Core
host (changedetection.io GHSA-3c45-4pj5-ch7m / GHSA-gwph-fp79-379w broke the same way).
``page.route`` is NOT called for redirect hops (measured on Playwright 1.62: the handler sees
only the first url, the hop reaches the server), so the guard is a CDP ``Fetch`` interception
at the request stage, which pauses every hop BEFORE it is sent.

Two layers of tests:
- no browser (the default suite): the guard against a fake CDP session, the post-hoc
  redirect-chain check against a fake response, the text digest and the selector rules;
- ``-m browser`` (real headless Chromium, never a window): a worker WITHOUT
  ``--allow-private-destinations``, a fake resolver for the policy and Chromium's
  ``--host-resolver-rules`` mapping ``public.test`` to the loopback fixture, so a "public"
  page really redirects into 100.64.0.1 / 100.90.158.26 / 127.0.0.1 / 169.254.169.254.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from browser_agent import media
from browser_agent.destination import RequestGuard, TrustedOrigin, require_public_destination
from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.evidence import validate_selector, whitespace_digest
from browser_agent.worker import Worker, build_arg_parser

_PUBLIC = {"public.test": ["93.184.216.34"], "evil.test": ["100.90.158.26"]}


def _fake_resolver(host: str) -> list[str]:
    return list(_PUBLIC.get(host, ["93.184.216.35"]))


def _policy_check(url: str) -> None:
    require_public_destination(url, op="request", resolver=_fake_resolver)


# --------------------------------------------------------------------------- #
# no browser: the guard against a fake CDP session
# --------------------------------------------------------------------------- #


class _FakeCdp:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.sent: list[tuple[str, dict]] = []

    def on(self, event: str, callback) -> None:
        self.handlers[event] = callback

    async def send(self, method: str, params: dict | None = None) -> dict:
        self.sent.append((method, dict(params or {})))
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "MAIN"}}}
        return {}


def _paused(url: str, *, frame: str = "MAIN", kind: str = "Document", hop: bool = False) -> dict:
    event = {
        "requestId": f"interception-{url}",
        "request": {"url": url, "method": "GET"},
        "frameId": frame,
        "resourceType": kind,
    }
    if hop:
        event["redirectedRequestId"] = "interception-previous"
    return event


class TestRequestGuardUnit:
    async def test_attach_enables_request_stage_interception_for_every_url(self) -> None:
        guard = RequestGuard(_policy_check)
        cdp = _FakeCdp()
        await guard.attach(cdp)
        assert "Fetch.requestPaused" in cdp.handlers
        enable = [p for m, p in cdp.sent if m == "Fetch.enable"]
        assert enable == [{"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}]

    @pytest.mark.parametrize(
        "target",
        [
            "http://100.64.0.1/",
            "http://100.90.158.26:8001/v1/health",
            "http://127.0.0.1:8001/",
            "http://169.254.169.254/latest/meta-data/",
            "http://evil.test/",  # a name resolving into the tailnet
        ],
    )
    async def test_a_redirect_hop_into_a_forbidden_destination_is_failed_before_it_is_sent(
        self, target: str
    ) -> None:
        guard = RequestGuard(_policy_check)
        cdp = _FakeCdp()
        await guard.attach(cdp)
        mark = guard.mark()
        await guard.on_request_paused(cdp, "MAIN", _paused(target, hop=True))
        assert cdp.sent[-1] == (
            "Fetch.failRequest",
            {"requestId": f"interception-{target}", "errorReason": "BlockedByClient"},
        )
        violation = guard.main_frame_violation_since(mark)
        assert violation is not None and violation.redirect_hop is True

    async def test_a_public_hop_continues(self) -> None:
        guard = RequestGuard(_policy_check)
        cdp = _FakeCdp()
        await guard.attach(cdp)
        await guard.on_request_paused(cdp, "MAIN", _paused("https://public.test/x", hop=True))
        assert cdp.sent[-1] == (
            "Fetch.continueRequest",
            {"requestId": "interception-https://public.test/x"},
        )
        assert guard.violations == []

    @pytest.mark.parametrize("kind", ["Image", "Fetch", "XHR", "Script"])
    async def test_a_sub_request_is_blocked_but_is_not_a_navigation_violation(
        self, kind: str
    ) -> None:
        guard = RequestGuard(_policy_check)
        cdp = _FakeCdp()
        await guard.attach(cdp)
        mark = guard.mark()
        await guard.on_request_paused(cdp, "MAIN", _paused("http://100.64.0.1/x", kind=kind))
        assert cdp.sent[-1][0] == "Fetch.failRequest"
        assert len(guard.violations) == 1
        assert guard.main_frame_violation_since(mark) is None

    async def test_an_iframe_document_is_blocked_but_is_not_the_page_navigation(self) -> None:
        guard = RequestGuard(_policy_check)
        cdp = _FakeCdp()
        await guard.attach(cdp)
        mark = guard.mark()
        await guard.on_request_paused(cdp, "MAIN", _paused("http://100.64.0.1/", frame="CHILD"))
        assert cdp.sent[-1][0] == "Fetch.failRequest"
        assert guard.main_frame_violation_since(mark) is None

    async def test_a_check_that_cannot_decide_fails_closed(self) -> None:
        def broken(_url: str) -> None:
            raise BrowserError(ErrorClass.DEPENDENCY_UNAVAILABLE, "dns down", retryable=True)

        guard = RequestGuard(broken)
        cdp = _FakeCdp()
        await guard.attach(cdp)
        await guard.on_request_paused(cdp, "MAIN", _paused("https://public.test/"))
        assert cdp.sent[-1][0] == "Fetch.failRequest"
        assert guard.violations == []  # not a policy refusal, but never let through

    async def test_violation_evidence_carries_no_query_string(self) -> None:
        guard = RequestGuard(_policy_check)
        cdp = _FakeCdp()
        await guard.attach(cdp)
        await guard.on_request_paused(cdp, "MAIN", _paused("http://100.64.0.1/a?token=secret"))
        assert "secret" not in guard.violations[0].url


# --------------------------------------------------------------------------- #
# no browser: the worker's post-hoc chain check (what a response says it went through)
# --------------------------------------------------------------------------- #


def _worker(tmp_path, *extra: str) -> Worker:
    args = build_arg_parser().parse_args(
        ["--data-dir", str(tmp_path / "data"), "--channel", "chromium", "--headless", *extra]
    )
    worker = Worker(args)
    worker._destination_resolver = _fake_resolver
    return worker


def _response(*urls: str, server_ip: str | None = "93.184.216.34"):
    request = None
    for url in urls:
        request = SimpleNamespace(url=url, redirected_from=request)

    async def server_addr():
        return None if server_ip is None else {"ipAddress": server_ip, "port": 443}

    return SimpleNamespace(url=urls[-1], request=request, server_addr=server_addr)


class TestNavigationChainUnit:
    @pytest.mark.parametrize(
        "hop", ["http://100.64.0.1/", "http://100.90.158.26:8001/", "http://evil.test/"]
    )
    async def test_a_forbidden_hop_in_the_middle_of_the_chain_is_refused(
        self, tmp_path, hop: str
    ) -> None:
        worker = _worker(tmp_path)
        response = _response("https://public.test/a", hop, "https://public.test/b")
        with pytest.raises(BrowserError) as info:
            await worker._verify_navigation(response, "https://public.test/b", op="navigate")
        assert info.value.error_class is ErrorClass.SECURITY_SCOPE_ERROR
        assert info.value.retryable is False

    async def test_a_forbidden_final_url_is_refused(self, tmp_path) -> None:
        worker = _worker(tmp_path)
        response = _response("https://public.test/a")
        with pytest.raises(BrowserError) as info:
            await worker._verify_navigation(response, "http://100.64.0.1/", op="navigate")
        assert info.value.error_class is ErrorClass.SECURITY_SCOPE_ERROR

    async def test_a_public_name_served_from_a_tailnet_address_is_refused(self, tmp_path) -> None:
        # DNS rebinding: the policy resolved a public address, Chromium connected elsewhere.
        worker = _worker(tmp_path)
        response = _response("https://public.test/a", server_ip="100.90.158.26")
        with pytest.raises(BrowserError) as info:
            await worker._verify_navigation(response, "https://public.test/a", op="navigate")
        assert info.value.error_class is ErrorClass.SECURITY_SCOPE_ERROR

    async def test_a_public_chain_passes(self, tmp_path) -> None:
        worker = _worker(tmp_path)
        response = _response("https://public.test/a", "https://public.test/b")
        await worker._verify_navigation(response, "https://public.test/b", op="navigate")

    async def test_the_trusted_report_view_passes_although_its_address_is_private(
        self, tmp_path
    ) -> None:
        worker = _worker(tmp_path, "--trusted-origin", "http://100.90.158.26:8001")
        view = "http://100.90.158.26:8001/v1/artifacts/renders/view?t=abc"
        response = _response(view, server_ip="100.90.158.26")
        await worker._verify_navigation(response, view, op="navigate")
        other = "http://100.90.158.26:8001/v1/devices"
        with pytest.raises(BrowserError):
            await worker._verify_navigation(
                _response(other, server_ip="100.90.158.26"), other, op="navigate"
            )

    async def test_allow_private_destinations_skips_the_chain_check(self, tmp_path) -> None:
        worker = _worker(tmp_path, "--allow-private-destinations")
        response = _response("http://127.0.0.1:1/a", server_ip="127.0.0.1")
        await worker._verify_navigation(response, "http://127.0.0.1:1/a", op="navigate")


# --------------------------------------------------------------------------- #
# no browser: digest and selector rules
# --------------------------------------------------------------------------- #


class TestDigestAndSelectorUnit:
    def test_digest_is_sha256_of_the_whitespace_collapsed_text(self) -> None:
        expected = hashlib.sha256(b"12 TL\n34 TL".replace(b"\n", b" ")).hexdigest()
        assert whitespace_digest("  12   TL\n\n 34\tTL  ") == expected
        assert whitespace_digest("12 TL 34 TL") == expected

    def test_digest_changes_with_the_text(self) -> None:
        assert whitespace_digest("a b") != whitespace_digest("a c")

    def test_selector_absent_is_none(self) -> None:
        assert validate_selector(None) is None

    @pytest.mark.parametrize("value", ["", "   ", "a" * 201, 5, ["div"], {"css": "div"}])
    def test_a_bad_selector_is_validation_error(self, value) -> None:
        with pytest.raises(BrowserError) as info:
            validate_selector(value)
        assert info.value.error_class is ErrorClass.VALIDATION_ERROR

    def test_a_200_character_selector_is_accepted(self) -> None:
        assert validate_selector("d" * 200) == "d" * 200


# --------------------------------------------------------------------------- #
# -m browser: real headless Chromium through the real worker
# --------------------------------------------------------------------------- #

_LONG_A = "".join("abcdefghij"[i % 10] for i in range(5000))
_LONG_B = _LONG_A[:4000] + "Z" + _LONG_A[4001:]


class _Hits:
    def __init__(self) -> None:
        self.paths: list[str] = []


def _serve(handler_factory) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture()
def sites() -> Iterator[SimpleNamespace]:
    hits = _Hits()

    class Secret(BaseHTTPRequestHandler):
        def log_message(self, *_a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            hits.paths.append(self.path)
            body = b"<html><body>SECRET tailnet page</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    secret, secret_thread = _serve(Secret)
    secret_port = secret.server_address[1]

    class Public(BaseHTTPRequestHandler):
        def log_message(self, *_a) -> None:
            pass

        def _html(self, html: str) -> None:
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            if parts.path == "/redir":
                self.send_response(302)
                self.send_header("Location", query["to"][0])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if parts.path == "/subs":
                self._html(
                    "<html><body><main><p>Ana sayfa metni okunuyor.</p>"
                    '<img src="http://100.90.158.26:8001/v1/health">'
                    f'<img src="http://127.0.0.1:{secret_port}/img">'
                    '<iframe src="http://100.64.0.1/"></iframe>'
                    f'<iframe src="http://127.0.0.1:{secret_port}/frame"></iframe>'
                    f'<script>fetch("http://127.0.0.1:{secret_port}/fetch").catch(()=>0);'
                    'fetch("http://evil.test/x").catch(()=>0);</script>'
                    "</main></body></html>"
                )
                return
            if parts.path == "/long":
                text = _LONG_B if query.get("v") == ["b"] else _LONG_A
                self._html(f"<html><body><main><p>{text}</p></main></body></html>")
                return
            if parts.path == "/prices":
                self._html(
                    "<html><body><main><h1>Fiyatlar</h1>"
                    '<div class="price">  12 \n TL </div><p>arada</p>'
                    '<div class="price">34   TL</div><p>son</p></main></body></html>'
                )
                return
            self._html(f"<html><body><main><p>sayfa {parts.path}</p></main></body></html>")

    public, public_thread = _serve(Public)
    try:
        yield SimpleNamespace(
            port=public.server_address[1],
            base=f"http://public.test:{public.server_address[1]}",
            secret_port=secret_port,
            hits=hits,
        )
    finally:
        for server, thread in ((public, public_thread), (secret, secret_thread)):
            server.shutdown()
            thread.join(timeout=5)


@pytest.fixture()
async def guarded_worker(tmp_path, monkeypatch) -> Iterator[Worker]:
    """A real worker WITHOUT --allow-private-destinations; ``public.test`` is public to the
    policy (fake resolver) and lands on the loopback fixture in Chromium."""
    monkeypatch.setattr(
        media,
        "media_launch_args",
        lambda _kind: ["--host-resolver-rules=MAP public.test 127.0.0.1"],
    )
    worker = _worker(tmp_path)
    # The fixture is really served from 127.0.0.1; only the served-address (rebinding)
    # check is told so - the URL policy is the production one.
    worker._served_address_forbidden = lambda _ip: False
    await worker._print_hello()
    try:
        yield worker
    finally:
        await worker._close_all_sessions()


async def _open(worker: Worker) -> None:
    await worker._execute(
        "browser.session_open",
        {
            "session_id": "s1",
            "profile": "isolated",
            "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": False},
        },
    )


def _targets(secret_port: int) -> list[str]:
    return [
        "http://100.64.0.1/",
        "http://100.90.158.26:8001/v1/health",
        f"http://127.0.0.1:{secret_port}/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://evil.test/",
    ]


@pytest.mark.browser
@pytest.mark.parametrize("capability", ["browser.navigate", "browser.fetch_evidence"])
async def test_a_public_page_redirecting_into_a_forbidden_destination_is_refused(
    guarded_worker: Worker, sites, capability: str
) -> None:
    await _open(guarded_worker)
    for target in _targets(sites.secret_port):
        with pytest.raises(BrowserError) as info:
            await guarded_worker._execute(
                capability,
                {"session_id": "s1", "url": f"{sites.base}/redir?to={target}", "timeout_ms": 8000},
            )
        assert info.value.error_class is ErrorClass.SECURITY_SCOPE_ERROR, target
        assert info.value.retryable is False
        assert "SECRET" not in str(info.value.evidence)
    # Blocked BEFORE it was sent: the "tailnet" server never saw the hop.
    assert sites.hits.paths == []


@pytest.mark.browser
async def test_a_public_page_still_reads(guarded_worker: Worker, sites) -> None:
    await _open(guarded_worker)
    result = await guarded_worker._execute(
        "browser.fetch_evidence",
        {"session_id": "s1", "url": f"{sites.base}/redir?to={sites.base}/fine"},
    )
    assert result["final_url"] == f"{sites.base}/fine"
    assert "sayfa /fine" in result["excerpt"]


@pytest.mark.browser
async def test_sub_requests_to_the_tailnet_are_blocked_and_the_page_still_reads(
    guarded_worker: Worker, sites
) -> None:
    await _open(guarded_worker)
    result = await guarded_worker._execute(
        "browser.fetch_evidence", {"session_id": "s1", "url": f"{sites.base}/subs"}
    )
    assert "Ana sayfa metni" in result["excerpt"]
    assert sites.hits.paths == []  # no img, iframe or fetch reached the "tailnet" server
    state = guarded_worker._sessions["s1"]
    blocked = {v.resource_type for v in state.request_guard.violations}
    assert {"Image", "Document"} <= blocked


@pytest.mark.browser
async def test_the_trusted_report_view_opens_and_nothing_else_of_that_origin(
    guarded_worker: Worker, sites
) -> None:
    guarded_worker._trusted_origin = TrustedOrigin(scheme="http", host="127.0.0.1", port=sites.port)
    await _open(guarded_worker)
    origin = f"http://127.0.0.1:{sites.port}"
    opened = await guarded_worker._execute(
        "browser.navigate",
        {"session_id": "s1", "url": f"{origin}/v1/artifacts/renders/view?t=abc"},
    )
    assert opened["url"].startswith(f"{origin}/v1/artifacts/renders/view")
    for refused in (
        f"{origin}/v1/devices",
        f"{sites.base}/redir?to={origin}/v1/devices",
        f"http://127.0.0.1:{sites.secret_port}/v1/artifacts/renders/view?t=abc",
    ):
        with pytest.raises(BrowserError) as info:
            await guarded_worker._execute("browser.navigate", {"session_id": "s1", "url": refused})
        assert info.value.error_class is ErrorClass.SECURITY_SCOPE_ERROR, refused
    assert sites.hits.paths == []


@pytest.mark.browser
async def test_selector_hashes_only_the_matched_text(guarded_worker: Worker, sites) -> None:
    await _open(guarded_worker)
    result = await guarded_worker._execute(
        "browser.fetch_evidence",
        {"session_id": "s1", "url": f"{sites.base}/prices", "selector": "div.price"},
    )
    assert result["selector_matched"] is True
    assert result["text_sha256"] == hashlib.sha256(b"12 TL 34 TL").hexdigest()
    assert "arada" not in result["excerpt"] and "12" in result["excerpt"]


@pytest.mark.browser
async def test_selector_matching_nothing_is_not_a_failure(guarded_worker: Worker, sites) -> None:
    await _open(guarded_worker)
    result = await guarded_worker._execute(
        "browser.fetch_evidence",
        {"session_id": "s1", "url": f"{sites.base}/prices", "selector": "span.nothing"},
    )
    assert result["selector_matched"] is False
    assert result["text_sha256"] is None
    assert result["excerpt"] == ""


@pytest.mark.browser
async def test_without_selector_the_hash_covers_the_whole_text_past_the_excerpt(
    guarded_worker: Worker, sites
) -> None:
    await _open(guarded_worker)
    first = await guarded_worker._execute(
        "browser.fetch_evidence", {"session_id": "s1", "url": f"{sites.base}/long?v=a"}
    )
    second = await guarded_worker._execute(
        "browser.fetch_evidence", {"session_id": "s1", "url": f"{sites.base}/long?v=b"}
    )
    assert first["selector_matched"] is None
    assert first["excerpt"] == second["excerpt"]  # the change is past excerpt_chars
    assert first["text_sha256"] != second["text_sha256"]
    assert first["text_sha256"] == hashlib.sha256(_LONG_A.encode()).hexdigest()


@pytest.mark.browser
async def test_a_selector_over_200_characters_is_validation_error(
    guarded_worker: Worker, sites
) -> None:
    await _open(guarded_worker)
    with pytest.raises(BrowserError) as info:
        await guarded_worker._execute(
            "browser.fetch_evidence",
            {"session_id": "s1", "url": f"{sites.base}/prices", "selector": "d" * 201},
        )
    assert info.value.error_class is ErrorClass.VALIDATION_ERROR
