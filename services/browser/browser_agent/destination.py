"""Destination policy: the worker only ever navigates to public Internet hosts.

Cloud Core is supposed to hand the device only URLs it has vetted, but the device
is the side that actually sits on the owner's LAN and tailnet, so it enforces the
same rule independently (defence in depth, review finding ADR-0050 addendum):
``http``/``https`` only, no userinfo, no loopback/private/link-local/CGNAT
(Tailscale 100.64.0.0/10)/multicast/reserved destinations — neither as an IP
literal nor via DNS (every resolved address is checked, so a rebinding name that
resolves to one public and one private address is refused too).

A refusal is ``security_scope_error`` (not retryable): the URL is not a browser
failure and not a website failure, it is a destination the policy forbids.
Fixture sites in the test suite live on loopback; they are reachable only when the
worker is started with ``--allow-private-destinations``, which the companion never
passes.

One narrow exception exists (owner decision 2026-09-29, ADR-0210 addendum): the office
PC's worker has to open the report Cloud Core serves, and Cloud Core is a tailnet address
that the rules above refuse. ``--trusted-origin <scheme://host:port>`` admits EXACTLY that
origin, and on it only the report-view route (``TRUSTED_VIEW_PATH``) - not the rest of what
the broker serves, not another port of the same host, not a neighbouring address. The origin
is device configuration (the installer writes it from the broker URL the device itself
dials); nothing Cloud Core sends can set or widen it. Without the option there is no
exception at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult, urlsplit

from .errors import ALLOWED_NAV_SCHEMES, BrowserError, ErrorClass, redact_url

Resolver = Callable[[str], Iterable[str]]

#: The one route the trusted origin is trusted for: what ``render_view_store.VIEW_PATH`` in
#: Cloud Core mints (``{origin}/v1/artifacts/renders/view?t=<token>``, the token is in the
#: QUERY, so the path is exact). A drift guard in tests reads the cloud constant.
TRUSTED_VIEW_PATH = "/v1/artifacts/renders/view"

_DEFAULT_PORTS = {"http": 80, "https": 443}
_HOST_NAME = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")
# A browser reads "2130706433" or "0x7f000001" as an IPv4 address; ipaddress does not, so
# such a "name" would sail past every check here and then be 127.0.0.1 in Chrome.
_NUMERIC_HOST = re.compile(r"(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+))*")

_FORBIDDEN_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".home.arpa")
_FORBIDDEN_HOSTS = frozenset({"localhost", "metadata.google.internal"})
# Tailscale/CGNAT is not in ipaddress's "private" set; the rest is covered by the
# is_private/is_loopback/... flags but is spelled out for readers and tests.
_EXTRA_FORBIDDEN_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),
)


def _default_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise BrowserError(
            ErrorClass.DEPENDENCY_UNAVAILABLE,
            f"destination: could not resolve host ({exc.errno})",
            retryable=True,
            evidence={"host": host},
        ) from exc
    return [info[4][0] for info in infos]


def address_is_forbidden(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast:
        return True
    if ip.is_reserved or ip.is_unspecified:
        return True
    return any(ip in net for net in _EXTRA_FORBIDDEN_NETWORKS)


def _canonical_host(host: str) -> str:
    """An IP literal in its one canonical spelling, anything else as it is (already
    lower-case, without the trailing dot)."""
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        return host


@dataclass(frozen=True, slots=True)
class TrustedOrigin:
    """The one origin (scheme, host, explicit port) whose report-view route is admitted."""

    scheme: str
    host: str
    port: int

    def admits(self, url: str, parts: SplitResult, host: str) -> bool:
        """True only for this exact origin AND the report-view route. Host compared as the
        parsed string: a name is a name, it is never resolved and trusted for what it
        resolves to."""
        # A backslash is a path separator to a browser and an ordinary character to urlsplit:
        # the two would disagree about where the host ends. Never trusted.
        if "\\" in url:
            return False
        if parts.scheme.lower() != self.scheme:
            return False
        if parts.path != TRUSTED_VIEW_PATH:
            return False
        try:
            port = parts.port
        except ValueError:
            return False
        if (_DEFAULT_PORTS[self.scheme] if port is None else port) != self.port:
            return False
        return _canonical_host(host) == self.host


def parse_trusted_origin(value: str | None) -> TrustedOrigin | None:
    """The configured ``--trusted-origin``: ``None`` for absent/empty (no exception at all),
    a ``TrustedOrigin`` for a usable one, ``ValueError`` (with the reason) otherwise.

    Private and tailnet addresses ARE acceptable - that is the point of the option. What is
    not: anything a browser would reach on THIS machine or on the cloud metadata service
    (loopback, link-local incl. 169.254.169.254 / fe80::/10), multicast, unspecified and
    reserved addresses, the local/internal name families, and anything that is more than
    an origin (userinfo, path, query, fragment).
    """
    if value is None or not value.strip():
        return None
    text = value.strip()
    if any(ch.isspace() or ord(ch) < 0x20 or ch == "\\" for ch in text):
        raise ValueError("must not contain whitespace, control characters or backslashes")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"is not a valid origin ({exc})") from exc
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_NAV_SCHEMES:
        raise ValueError("scheme must be http or https")
    if parts.username is not None or parts.password is not None:
        raise ValueError("must not carry credentials")
    if parts.path not in ("", "/") or "?" in text or "#" in text:
        raise ValueError("must be an origin only (no path, query or fragment)")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("has no host")
    if port == 0:
        raise ValueError("port 0 is not a port")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if isinstance(literal, ipaddress.IPv6Address) and literal.ipv4_mapped is not None:
            literal = literal.ipv4_mapped
        if (
            literal.is_loopback
            or literal.is_link_local
            or literal.is_multicast
            or literal.is_unspecified
            or literal.is_reserved
            or literal in ipaddress.ip_network("0.0.0.0/8")
        ):
            raise ValueError(f"{host} is a loopback, link-local, multicast or reserved address")
        canonical = str(ipaddress.ip_address(host))
    else:
        if host in _FORBIDDEN_HOSTS or host.endswith(_FORBIDDEN_HOST_SUFFIXES):
            raise ValueError(f"{host} is a local or internal host name")
        if _NUMERIC_HOST.fullmatch(host):
            raise ValueError(f"{host} is a numeric host a browser would read as an IP address")
        if _HOST_NAME.fullmatch(host) is None:
            raise ValueError(f"{host} is not a host name")
        canonical = host
    return TrustedOrigin(
        scheme=scheme,
        host=canonical,
        port=_DEFAULT_PORTS[scheme] if port is None else port,
    )


def require_public_destination(
    url: str,
    *,
    op: str,
    resolver: Resolver | None = None,
    trusted_origin: TrustedOrigin | None = None,
) -> None:
    """Raise ``security_scope_error`` unless ``url`` points at a public host (or is the
    report-view route of the one configured ``trusted_origin``)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = None
    if parts is None or parts.scheme.lower() not in ALLOWED_NAV_SCHEMES:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"{op}: URL scheme is not allowed (http/https only)",
            retryable=False,
            evidence={"url": redact_url(url)},
        )
    if parts.username is not None or parts.password is not None:
        _refuse(op, url, "credentials embedded in the URL")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        _refuse(op, url, "empty host")
    if trusted_origin is not None and trusted_origin.admits(url, parts, host):
        return
    if host in _FORBIDDEN_HOSTS or host.endswith(_FORBIDDEN_HOST_SUFFIXES):
        _refuse(op, url, "local or internal host name")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if address_is_forbidden(str(literal)):
            _refuse(op, url, "non-public IP address")
        return
    resolve = resolver or _default_resolver
    addresses = list(resolve(host))
    if not addresses:
        _refuse(op, url, "host resolved to no address")
    bad = [a for a in addresses if address_is_forbidden(a)]
    if bad:
        _refuse(op, url, "host resolves to a non-public address")


def _refuse(op: str, url: str, why: str) -> None:
    raise BrowserError(
        ErrorClass.SECURITY_SCOPE_ERROR,
        f"{op}: destination refused ({why})",
        retryable=False,
        evidence={"url": redact_url(url)},
    )


def trusted_origin_admits(url: str, trusted_origin: TrustedOrigin | None) -> bool:
    """True when ``url`` is the report-view route of the configured origin (and only then)."""
    if trusted_origin is None:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").strip().lower().rstrip(".")
    return bool(host) and trusted_origin.admits(url, parts, host)


# --------------------------------------------------------------------------- #
# every request the browser sends (browser-redirect-guard, cycle d20261003)
# --------------------------------------------------------------------------- #
#
# The checks above ran on the REQUESTED url only. A public page that redirects - or a
# sub-request (img, fetch, iframe) - into the tailnet, loopback or link-local went through,
# and the cloud worker sits on the Cloud Core host (changedetection.io GHSA-3c45-4pj5-ch7m
# and GHSA-gwph-fp79-379w broke the same way). Playwright's ``page.route`` is not called for
# redirect hops, so the guard is CDP ``Fetch`` interception at the request stage: Chromium
# pauses EVERY request of the page - each redirect hop included - before it is sent, and
# nothing continues unless the same policy admits it.

_REQUEST_STAGE_PATTERNS = [{"urlPattern": "*", "requestStage": "Request"}]


@dataclass(frozen=True, slots=True)
class BlockedRequest:
    """One request the guard failed because the policy forbids its destination."""

    url: str  # scheme://host/path only - never the query (tokens live there)
    resource_type: str
    frame_id: str
    main_frame: bool
    redirect_hop: bool
    reason: str

    @property
    def is_page_navigation(self) -> bool:
        return self.main_frame and self.resource_type == "Document"


def _url_without_query(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable>"
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme}://{host}{port}{parts.path}"


class RequestGuard:
    """Holds every request of the pages it is attached to to ``check`` (a callable that
    raises ``BrowserError`` for a forbidden url). Fails closed: a check that cannot decide
    (DNS down) fails the request too, it is just not recorded as a policy refusal."""

    def __init__(self, check: Callable[[str], None]) -> None:
        self._check = check
        self.violations: list[BlockedRequest] = []
        self._tasks: set[asyncio.Task[None]] = set()

    async def attach(self, cdp: Any) -> None:
        """Start intercepting on one page's CDP session (``context.new_cdp_session(page)``)."""
        tree = await cdp.send("Page.getFrameTree")
        main_frame_id = str(tree["frameTree"]["frame"]["id"])

        def on_paused(event: dict[str, Any]) -> None:
            task = asyncio.ensure_future(self.on_request_paused(cdp, main_frame_id, event))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        cdp.on("Fetch.requestPaused", on_paused)
        await cdp.send("Fetch.enable", {"patterns": _REQUEST_STAGE_PATTERNS})

    async def on_request_paused(self, cdp: Any, main_frame_id: str, event: dict[str, Any]) -> None:
        request_id = event["requestId"]
        url = str((event.get("request") or {}).get("url") or "")
        try:
            await asyncio.to_thread(self._check, url)
        except BrowserError as exc:
            if exc.error_class in (ErrorClass.SECURITY_SCOPE_ERROR, ErrorClass.VALIDATION_ERROR):
                frame_id = str(event.get("frameId") or "")
                self.violations.append(
                    BlockedRequest(
                        url=_url_without_query(url),
                        resource_type=str(event.get("resourceType") or ""),
                        frame_id=frame_id,
                        main_frame=frame_id == main_frame_id,
                        redirect_hop="redirectedRequestId" in event,
                        reason=str(exc)[:200],
                    )
                )
            await self._fail(cdp, request_id)
            return
        except Exception:  # never let an unchecked request through
            await self._fail(cdp, request_id)
            return
        with contextlib.suppress(Exception):  # the page may be gone by now
            await cdp.send("Fetch.continueRequest", {"requestId": request_id})

    @staticmethod
    async def _fail(cdp: Any, request_id: str) -> None:
        with contextlib.suppress(Exception):
            await cdp.send(
                "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}
            )

    def mark(self) -> int:
        return len(self.violations)

    def main_frame_violation_since(self, mark: int) -> BlockedRequest | None:
        """The first refused page navigation (requested url or a redirect hop of a tab's
        main frame) recorded after ``mark``; sub-requests and iframes are not one."""
        return next((v for v in self.violations[mark:] if v.is_page_navigation), None)


# --------------------------------------------------------------------------- #
# the network layer: Chromium's only way out (browser-redirect-guard, return 1)
# --------------------------------------------------------------------------- #
#
# CDP ``Fetch`` on the page's own session does not see a cross-site iframe's requests (an
# out-of-process frame is another target), a popup's first request (sent before anything can
# attach to the new target) or a WebSocket (CDP Fetch does not intercept it); the inspector
# reached the "tailnet" all three ways on 2026-10-04. So every managed browser the worker
# launches gets ``--proxy-server`` pointing at this in-process egress proxy and
# ``--proxy-bypass-list=<-loopback>`` (Chromium otherwise sends loopback around a proxy).
# Every connection Chromium makes - any target, any frame, redirect hops, WebSockets,
# workers, beacons - is a CONNECT or an absolute-form request here, held to the same
# policy, and the proxy dials the ADDRESS IT VETTED (no second resolution: the DNS
# rebinding gap between our resolver and Chromium's is closed, not just detected). The idea
# is Stripe's smokescreen; it is in-process Python so the office PC's Windows worker and the
# cloud container get the same guard without another binary.

_PROXY_HEAD_LIMIT = 64 * 1024
_PROXY_IO_TIMEOUT_S = 30.0
_PROXY_DIAL_TIMEOUT_S = 15.0
_HOP_BY_HOP = frozenset(
    {"connection", "keep-alive", "proxy-connection", "proxy-authorization", "te", "trailer"}
)
_REFUSED = (
    b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nContent-Length: 0\r\n"
    b"Connection: close\r\nProxy-Connection: close\r\n\r\n"
)
_BAD_REQUEST = (
    b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n"
    b"Proxy-Connection: close\r\n\r\n"
)
_BAD_GATEWAY = (
    b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n"
    b"Proxy-Connection: close\r\n\r\n"
)


@dataclass(frozen=True, slots=True)
class ProxyRefusal:
    """One connection the egress proxy refused (host, port and - for http - the path)."""

    method: str
    target: str  # scheme://host:port/path or host:port - never the query
    reason: str


class EgressProxy:
    """A forward proxy on 127.0.0.1 that admits only what ``require_public_destination``
    admits (the trusted-origin exception included) and dials the address it checked.

    ``dial`` maps a vetted address to the address actually connected to; production leaves
    it the identity, the browser tests map their "public" addresses to the loopback fixture.
    """

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        trusted_origin: TrustedOrigin | None = None,
        dial: Callable[[str], str] | None = None,
    ) -> None:
        self._resolver = resolver
        self._trusted_origin = trusted_origin
        self._dial = dial or (lambda address: address)
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[asyncio.Task[None]] = set()
        self.refusals: list[ProxyRefusal] = []
        self.port: int | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._on_client, "127.0.0.1", 0)
        self.port = int(self._server.sockets[0].getsockname()[1])

    def chromium_args(self) -> list[str]:
        """Launch arguments that leave the browser no other way out. WebRTC's UDP would go
        around an HTTP proxy, so non-proxied UDP is switched off."""
        if self.port is None:
            raise RuntimeError("egress proxy is not started")
        return [
            f"--proxy-server=http://127.0.0.1:{self.port}",
            "--proxy-bypass-list=<-loopback>",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        ]

    def is_proxy_address(self, address: str | None, port: Any) -> bool:
        return self.port is not None and address in ("127.0.0.1", "::1") and port == self.port

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
        for task in list(self._connections):
            task.cancel()
        if server is not None:
            with contextlib.suppress(Exception):
                await server.wait_closed()
        self.port = None

    # -- policy --------------------------------------------------------------- #

    def vet(self, *, url: str, host: str, port: int, connect: bool) -> str:
        """The address to dial for ``host``, or ``BrowserError`` when the policy forbids it.
        ``url`` is the full url (absolute-form) or ``https://host:port/`` for a CONNECT."""
        host = host.strip("[]").strip().lower().rstrip(".")
        # A tunnel carries no path, so it never gets the trusted-origin exception (which is
        # for the report-view route only): Chromium tunnels ws:// as a CONNECT, and admitting
        # the origin let a page reach any path of it (inspector, 2026-10-04). The trusted
        # report view is http and comes absolute-form, where the path IS checked.
        trusted = None if connect else self._trusted_origin
        resolved: list[str] = []

        def recording(name: str) -> list[str]:
            addresses = list((self._resolver or _default_resolver)(name))
            resolved.extend(addresses)
            return addresses

        require_public_destination(url, op="request", resolver=recording, trusted_origin=trusted)
        if resolved:
            return resolved[0]  # every one of them was checked; dial what was checked
        return self._resolve_one(host)  # an IP literal, or the trusted origin's own name

    def _resolve_one(self, host: str) -> str:
        try:
            return str(ipaddress.ip_address(host))
        except ValueError:
            addresses = list((self._resolver or _default_resolver)(host))
        if not addresses:
            raise BrowserError(
                ErrorClass.DEPENDENCY_UNAVAILABLE, "egress proxy: no address", retryable=True
            )
        return addresses[0]

    # -- connections ---------------------------------------------------------- #

    def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.ensure_future(self._serve(reader, writer))
        self._connections.add(task)
        task.add_done_callback(self._connections.discard)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        try:
            head = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=_PROXY_IO_TIMEOUT_S
            )
            if len(head) > _PROXY_HEAD_LIMIT:
                writer.write(_BAD_REQUEST)
                return
            request_line, _, header_block = head.decode("latin-1").partition("\r\n")
            method, target, version = (request_line.split(" ") + ["", "", ""])[:3]
            if not version.startswith("HTTP/"):
                writer.write(_BAD_REQUEST)
                return
            connect = method.upper() == "CONNECT"
            parsed = _proxy_target(target, connect=connect)
            if parsed is None:
                writer.write(_BAD_REQUEST)
                return
            url, host, port, origin_form = parsed
            try:
                address = await asyncio.to_thread(
                    self.vet, url=url, host=host, port=port, connect=connect
                )
            except BrowserError as exc:
                self.refusals.append(
                    ProxyRefusal(
                        method=method.upper(), target=_url_without_query(url), reason=str(exc)[:200]
                    )
                )
                writer.write(_REFUSED)
                return
            try:
                up_reader, upstream = await asyncio.wait_for(
                    asyncio.open_connection(self._dial(address), port),
                    timeout=_PROXY_DIAL_TIMEOUT_S,
                )
            except (OSError, TimeoutError):
                writer.write(_BAD_GATEWAY)
                return
            if connect:
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            else:
                # One request per proxy connection: the next request on a kept-alive
                # connection could be for another host, and it would land on this upstream.
                upstream.write(_origin_request(method, origin_form, version, header_block))
                await upstream.drain()
                response_head = await asyncio.wait_for(
                    up_reader.readuntil(b"\r\n\r\n"), timeout=_PROXY_IO_TIMEOUT_S
                )
                writer.write(_closing_response(response_head))
                await writer.drain()
            await _pipe_both(reader, writer, up_reader, upstream)
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ConnectionError,
            TimeoutError,
            ValueError,
        ):
            pass
        finally:
            for w in (upstream, writer):
                if w is not None:
                    with contextlib.suppress(Exception):
                        w.close()


def _proxy_target(target: str, *, connect: bool) -> tuple[str, str, int, str] | None:
    """(url for the policy, host, port, origin-form path) of a proxy request target."""
    if connect:
        host, sep, port_text = target.rpartition(":")
        if not sep or not port_text.isdigit() or not host:
            return None
        port = int(port_text)
        if not 0 < port < 65536:
            return None
        bare = host.strip("[]")
        shown = f"[{bare}]" if ":" in bare else bare
        return f"https://{shown}:{port}/", bare, port, ""
    try:
        parts = urlsplit(target)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "ws") or not parts.hostname:
        return None  # https and wss come as CONNECT; anything else is not ours to carry
    policy_url = target if scheme == "http" else "http" + target[len(scheme) :]
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return policy_url, parts.hostname, port or 80, path


def _origin_request(method: str, path: str, version: str, header_block: str) -> bytes:
    headers = [line for line in header_block.split("\r\n") if line]
    upgrade = any(line.lower().startswith("upgrade:") for line in headers)
    kept = [line for line in headers if line.split(":", 1)[0].strip().lower() not in _HOP_BY_HOP]
    kept.append("Connection: Upgrade" if upgrade else "Connection: close")
    return (f"{method} {path} {version}\r\n" + "\r\n".join(kept) + "\r\n\r\n").encode("latin-1")


def _closing_response(head: bytes) -> bytes:
    status_line, _, header_block = head.decode("latin-1").partition("\r\n")
    headers = [line for line in header_block.split("\r\n") if line]
    if " 101 " in f"{status_line} ":
        return head  # a WebSocket upgrade stays the connection it is
    kept = [line for line in headers if line.split(":", 1)[0].strip().lower() not in _HOP_BY_HOP]
    kept += ["Connection: close", "Proxy-Connection: close"]
    return (status_line + "\r\n" + "\r\n".join(kept) + "\r\n\r\n").encode("latin-1")


async def _pipe_both(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    up_reader: asyncio.StreamReader,
    up_writer: asyncio.StreamWriter,
) -> None:
    async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            while chunk := await src.read(65536):
                dst.write(chunk)
                await dst.drain()
        with contextlib.suppress(Exception):
            dst.close()

    await asyncio.gather(pump(client_reader, up_writer), pump(up_reader, client_writer))


__all__ = [
    "TRUSTED_VIEW_PATH",
    "BlockedRequest",
    "EgressProxy",
    "ProxyRefusal",
    "RequestGuard",
    "TrustedOrigin",
    "address_is_forbidden",
    "parse_trusted_origin",
    "require_public_destination",
    "trusted_origin_admits",
]
