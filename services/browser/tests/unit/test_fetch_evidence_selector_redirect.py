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

import asyncio
import hashlib
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from browser_agent import media, policy
from browser_agent.destination import (
    EgressProxy,
    RequestGuard,
    TrustedOrigin,
    require_public_destination,
)
from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.evidence import validate_selector, whitespace_digest
from browser_agent.worker import SessionState, Worker, build_arg_parser

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
    pages: dict[str, str] = {}  # extra pages a test adds (path -> body html)
    reached: list[str] = []  # every path the PUBLIC fixture served

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
            reached.append(parts.path)
            if parts.path in pages:
                self._html(f"<html><body><main>{pages[parts.path]}</main></body></html>")
                return
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
            pages=pages,
            reached=reached,
        )
    finally:
        for server, thread in ((public, public_thread), (secret, secret_thread)):
            server.shutdown()
            thread.join(timeout=5)


def _dial_fixture(address: str) -> str:
    """The browser tests' "public" addresses (93.184.216.x) are the loopback fixture."""
    return "127.0.0.1" if address.startswith("93.184.216.") else address


@pytest.fixture()
async def guarded_worker(tmp_path) -> Iterator[Worker]:
    """A real worker WITHOUT --allow-private-destinations, behind its egress proxy:
    ``public.test`` / ``other.test`` are public to the policy (fake resolver) and the proxy
    dials the loopback fixture for them. Chromium resolves nothing itself - without the proxy
    ``public.test`` would not load at all, so every passing read proves the proxy path."""
    worker = _worker(tmp_path)
    worker._egress_dial = _dial_fixture
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


# --------------------------------------------------------------------------- #
# no browser: the egress proxy (return 1-3) against raw sockets
# --------------------------------------------------------------------------- #


@pytest.fixture()
def upstream() -> Iterator[SimpleNamespace]:
    seen: list[tuple[str, str]] = []

    class Echo(BaseHTTPRequestHandler):
        def log_message(self, *_a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            seen.append((self.path, self.headers.get("Host", "")))
            body = b"upstream says hi"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(body)

    server, thread = _serve(Echo)
    try:
        yield SimpleNamespace(port=server.server_address[1], seen=seen)
    finally:
        server.shutdown()
        thread.join(timeout=5)


async def _through(proxy: EgressProxy, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(request)
    await writer.drain()
    try:
        return await asyncio.wait_for(reader.read(65536), timeout=5)
    finally:
        writer.close()


class TestEgressProxyUnit:
    @pytest.mark.parametrize(
        "target",
        [
            "100.64.0.1:443",
            "100.90.158.26:8001",
            "127.0.0.1:{port}",
            "169.254.169.254:80",
            "evil.test:443",
            "[::1]:{port}",
        ],
    )
    async def test_a_tunnel_to_a_forbidden_destination_is_refused_and_never_dialled(
        self, upstream, target: str
    ) -> None:
        proxy = EgressProxy(resolver=_fake_resolver, dial=_dial_fixture)
        await proxy.start()
        try:
            host_port = target.format(port=upstream.port)
            answer = await _through(proxy, f"CONNECT {host_port} HTTP/1.1\r\n\r\n".encode())
        finally:
            await proxy.close()
        assert answer.startswith(b"HTTP/1.1 403")
        assert len(proxy.refusals) == 1
        assert upstream.seen == []

    async def test_a_plain_request_to_a_forbidden_destination_is_refused(self, upstream) -> None:
        proxy = EgressProxy(resolver=_fake_resolver, dial=_dial_fixture)
        await proxy.start()
        try:
            answer = await _through(
                proxy,
                f"GET http://127.0.0.1:{upstream.port}/x?t=secret HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{upstream.port}\r\n\r\n".encode(),
            )
        finally:
            await proxy.close()
        assert answer.startswith(b"HTTP/1.1 403")
        assert "secret" not in proxy.refusals[0].target
        assert upstream.seen == []

    async def test_a_public_request_is_forwarded_once_and_the_connection_closed(
        self, upstream
    ) -> None:
        proxy = EgressProxy(resolver=_fake_resolver, dial=_dial_fixture)
        await proxy.start()
        try:
            answer = await _through(
                proxy,
                (
                    f"GET http://public.test:{upstream.port}/a?b=1 HTTP/1.1\r\n"
                    f"Host: public.test:{upstream.port}\r\n"
                    "Proxy-Connection: keep-alive\r\n\r\n"
                ).encode(),
            )
        finally:
            await proxy.close()
        assert answer.split(b" ", 2)[1] == b"200"
        assert b"Connection: close" in answer and b"keep-alive" not in answer
        assert upstream.seen == [("/a?b=1", f"public.test:{upstream.port}")]

    def test_the_proxy_dials_the_address_it_vetted(self) -> None:
        # DNS rebinding: the name is resolved ONCE and the checked answer is what is dialled;
        # a second (tailnet) answer is never asked for.
        answers = iter([["93.184.216.34"], ["100.90.158.26"]])
        proxy = EgressProxy(resolver=lambda _h: next(answers))
        address = proxy.vet(
            url="https://rebind.test:443/", host="rebind.test", port=443, connect=True
        )
        assert address == "93.184.216.34"

    def test_the_trusted_origin_view_is_admitted_and_nothing_else(self) -> None:
        trusted = TrustedOrigin(scheme="http", host="100.90.158.26", port=8001)
        proxy = EgressProxy(resolver=_fake_resolver, trusted_origin=trusted)
        view = "http://100.90.158.26:8001/v1/artifacts/renders/view?t=abc"
        assert proxy.vet(url=view, host="100.90.158.26", port=8001, connect=False) == (
            "100.90.158.26"
        )
        for url, port, connect in (
            ("http://100.90.158.26:8001/v1/devices", 8001, False),
            ("http://100.90.158.26:8002/v1/artifacts/renders/view", 8002, False),
            ("https://100.90.158.26:8002/", 8002, True),
            ("https://100.90.158.27:8001/", 8001, True),
        ):
            with pytest.raises(BrowserError) as info:
                proxy.vet(url=url, host=urlsplit(url).hostname, port=port, connect=connect)
            assert info.value.error_class is ErrorClass.SECURITY_SCOPE_ERROR, url

    async def test_a_tunnel_to_the_trusted_origin_itself_is_refused(self, upstream) -> None:
        # Return of 2026-10-04: a tunnel carries no path, and Chromium tunnels ws:// through
        # the proxy as a CONNECT - admitting the trusted host:port let a hostile page reach
        # ANY path of the Cloud Core API (/v1/devices/ws-probe). The exception is for the
        # report-view route only, so a CONNECT never gets it.
        trusted = TrustedOrigin(scheme="http", host="127.0.0.1", port=upstream.port)
        proxy = EgressProxy(resolver=_fake_resolver, trusted_origin=trusted)
        with pytest.raises(BrowserError) as info:
            proxy.vet(
                url=f"https://127.0.0.1:{upstream.port}/",
                host="127.0.0.1",
                port=upstream.port,
                connect=True,
            )
        assert info.value.error_class is ErrorClass.SECURITY_SCOPE_ERROR
        await proxy.start()
        try:
            answer = await _through(
                proxy, f"CONNECT 127.0.0.1:{upstream.port} HTTP/1.1\r\n\r\n".encode()
            )
        finally:
            await proxy.close()
        assert answer.startswith(b"HTTP/1.1 403")
        assert upstream.seen == []

    async def test_chromium_is_left_no_way_around_it(self) -> None:
        proxy = EgressProxy()
        await proxy.start()
        try:
            args = proxy.chromium_args()
            assert f"--proxy-server=http://127.0.0.1:{proxy.port}" in args
        finally:
            await proxy.close()
        # Chromium sends loopback around a proxy unless told not to.
        assert "--proxy-bypass-list=<-loopback>" in args
        assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args

    async def test_the_worker_starts_one_proxy_and_stops_it_at_shutdown(self, tmp_path) -> None:
        worker = _worker(tmp_path)
        try:
            args = await worker._egress_args()
            assert args == await worker._egress_args()
            assert worker._egress_proxy is not None
        finally:
            await worker._close_all_sessions()
        assert worker._egress_proxy is None


# --------------------------------------------------------------------------- #
# no browser: the owner's own Chrome is not left intercepted (return 5)
# --------------------------------------------------------------------------- #


class _ReleasableCdp(_FakeCdp):
    def __init__(self) -> None:
        super().__init__()
        self.detached = False

    async def detach(self) -> None:
        self.detached = True


class _OwnerPage:
    url = "https://ornek.com/"

    def __init__(self) -> None:
        self.cdp = _ReleasableCdp()
        cdp = self.cdp

        class _Context:
            async def new_cdp_session(self, _page) -> _ReleasableCdp:
                return cdp

        self.context = _Context()


class _OwnerTab:
    index = 0
    is_current = True
    url = "https://ornek.com/"
    title = "Sahibin sekmesi"


class _OwnerBrowserSession:
    def __init__(self) -> None:
        self.backend = SimpleNamespace(
            current_page=_OwnerPage(),
            main_pid=None,
            last_launch_kind=None,
            launch_lock_name=None,
            job_object_assigned=False,
        )

    async def list_tabs(self) -> list[_OwnerTab]:
        return [_OwnerTab()]


def _session_state(profile: str) -> SessionState:
    browser_session = _OwnerBrowserSession()
    return SessionState(
        session_id="o",
        browser_session=browser_session,  # type: ignore[arg-type]
        backend=browser_session.backend,  # type: ignore[arg-type]
        policy_allowed=frozenset(policy.RiskClass),
        visible=True,
        channel="chrome",
        browser_version=None,
        profile=profile,
        last_used=0.0,
        request_guard=RequestGuard(_policy_check),
    )


class TestOwnerSessionGuardUnit:
    async def test_the_owners_tab_is_released_when_the_op_ends(self, tmp_path) -> None:
        worker = _worker(tmp_path)
        state = _session_state(media.OWNER_PROFILE)
        worker._sessions["o"] = state
        await worker._execute("browser.tab_list", {"session_id": "o"})
        cdp = state.browser_session.backend.current_page.cdp
        methods = [m for m, _ in cdp.sent]
        assert "Fetch.enable" in methods  # guarded WHILE the worker drove the tab
        assert methods[-1] == "Fetch.disable" and cdp.detached  # and let go after
        assert state.guard_cdp == {} and state.guarded_pages == set()

    async def test_the_owners_tab_is_released_when_the_op_fails(self, tmp_path) -> None:
        worker = _worker(tmp_path)
        state = _session_state(media.OWNER_PROFILE)
        worker._sessions["o"] = state
        with pytest.raises(BrowserError):
            await worker._execute("browser.tab_select", {"session_id": "o", "index": "x"})
        cdp = state.browser_session.backend.current_page.cdp
        assert cdp.detached and state.guard_cdp == {}

    async def test_a_worker_profile_keeps_its_guard_between_ops(self, tmp_path) -> None:
        worker = _worker(tmp_path)
        state = _session_state("isolated")
        worker._sessions["o"] = state
        await worker._execute("browser.tab_list", {"session_id": "o"})
        cdp = state.browser_session.backend.current_page.cdp
        assert "Fetch.disable" not in [m for m, _ in cdp.sent] and not cdp.detached
        assert state.guard_cdp  # still intercepting: it is the worker's own browser


# --------------------------------------------------------------------------- #
# -m browser: what the page-level guard could not see (return 1-4)
# --------------------------------------------------------------------------- #


def _hostile(sites) -> None:
    """Pages that try the ways around a page-level guard: a cross-site iframe whose own
    img/fetch/WebSocket go to the "tailnet", popups and WebSockets."""
    secret = sites.secret_port
    sites.pages.update(
        {
            "/oopif": (
                f'<p>Dis sayfa</p><iframe src="http://other.test:{sites.port}/inner"></iframe>'
            ),
            "/inner": (
                f'<p>Ic cerceve</p><img src="http://127.0.0.1:{secret}/inner-img">'
                f'<script>fetch("http://127.0.0.1:{secret}/inner-fetch").catch(()=>0);'
                f'try{{new WebSocket("ws://127.0.0.1:{secret}/inner-ws")}}catch(e){{}}</script>'
            ),
            "/popup": (
                "<p>Acilir pencere sayfasi</p>"
                f'<script>window.open("http://127.0.0.1:{secret}/popup");'
                f'window.open("{sites.base}/redir?to=http://127.0.0.1:{secret}/popup-hop");'
                "</script>"
            ),
            "/ws": (
                "<p>Soket sayfasi</p>"
                f'<script>try{{new WebSocket("ws://127.0.0.1:{secret}/ws")}}catch(e){{}}'
                'try{new WebSocket("ws://evil.test/ws")}catch(e){}</script>'
            ),
        }
    )


async def _read(worker: Worker, url: str) -> dict:
    result = await worker._execute("browser.fetch_evidence", {"session_id": "s1", "url": url})
    await asyncio.sleep(2.0)  # what the page starts on its own has time to try
    return result


@pytest.mark.browser
async def test_a_cross_site_iframe_cannot_reach_the_tailnet(guarded_worker: Worker, sites) -> None:
    _hostile(sites)
    await _open(guarded_worker)
    result = await _read(guarded_worker, f"{sites.base}/oopif")
    assert "Dis sayfa" in result["excerpt"]
    assert "/inner" in sites.reached  # the cross-site frame itself DID load
    assert sites.hits.paths == []  # ...and nothing it asked for reached the "tailnet"
    assert any("127.0.0.1" in r.target for r in guarded_worker._egress_proxy.refusals)


@pytest.mark.browser
async def test_a_popup_cannot_reach_the_tailnet(guarded_worker: Worker, sites) -> None:
    _hostile(sites)
    await _open(guarded_worker)
    result = await _read(guarded_worker, f"{sites.base}/popup")
    assert "Acilir pencere" in result["excerpt"]
    assert sites.hits.paths == []  # neither the popup's first request nor its redirect hop


@pytest.mark.browser
async def test_a_websocket_cannot_reach_the_tailnet(guarded_worker: Worker, sites) -> None:
    _hostile(sites)
    await _open(guarded_worker)
    result = await _read(guarded_worker, f"{sites.base}/ws")
    assert "Soket sayfasi" in result["excerpt"]
    assert sites.hits.paths == []
    assert any("127.0.0.1" in r.target for r in guarded_worker._egress_proxy.refusals)


@pytest.mark.browser
async def test_a_websocket_cannot_reach_another_path_of_the_trusted_origin(
    guarded_worker: Worker, sites
) -> None:
    # The inspector's probe of 2026-10-04: the trusted origin is the "Cloud Core API"
    # (here the secret fixture) and a public page opens a WebSocket to another path of it.
    secret = sites.secret_port
    guarded_worker._trusted_origin = TrustedOrigin(scheme="http", host="127.0.0.1", port=secret)
    sites.pages["/ws-trusted"] = (
        "<p>Soket sayfasi</p>"
        f'<script>try{{new WebSocket("ws://127.0.0.1:{secret}/v1/devices/ws-probe")}}'
        "catch(e){}</script>"
    )
    await _open(guarded_worker)
    result = await _read(guarded_worker, f"{sites.base}/ws-trusted")
    assert "Soket sayfasi" in result["excerpt"]
    assert sites.hits.paths == []
    assert any(r.method == "CONNECT" for r in guarded_worker._egress_proxy.refusals)


@pytest.mark.browser
async def test_tab_new_with_a_redirecting_url_is_refused(guarded_worker: Worker, sites) -> None:
    await _open(guarded_worker)
    for target in _targets(sites.secret_port):
        with pytest.raises(BrowserError) as info:
            await guarded_worker._execute(
                "browser.tab_new",
                {"session_id": "s1", "url": f"{sites.base}/redir?to={target}"},
            )
        assert info.value.error_class is ErrorClass.SECURITY_SCOPE_ERROR, target
        assert info.value.retryable is False
    assert sites.hits.paths == []
