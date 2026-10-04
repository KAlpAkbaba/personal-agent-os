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


__all__ = [
    "TRUSTED_VIEW_PATH",
    "BlockedRequest",
    "RequestGuard",
    "TrustedOrigin",
    "address_is_forbidden",
    "parse_trusted_origin",
    "require_public_destination",
    "trusted_origin_admits",
]
