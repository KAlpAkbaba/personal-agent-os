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
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

from .errors import ALLOWED_NAV_SCHEMES, BrowserError, ErrorClass, redact_url

Resolver = Callable[[str], Iterable[str]]

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


def require_public_destination(url: str, *, op: str, resolver: Resolver | None = None) -> None:
    """Raise ``security_scope_error`` unless ``url`` points at a public host."""
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


__all__ = ["address_is_forbidden", "require_public_destination"]
