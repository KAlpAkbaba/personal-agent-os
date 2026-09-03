"""Fetch-destination policy (finding HIGH-2, ADR-0050 §6 boundary hardening).

The browser-research pipeline asks a device to fetch arbitrary owner-supplied
or discovery-supplied URLs through its real Chrome. Without a destination
check, "research this topic" is a path for the pipeline itself to be turned
into a probe against the device's OWN network: internal admin panels, the
Tailscale control surface, the cloud metadata endpoint
(``169.254.169.254``), or any other address that is not the public web the
owner asked to be researched.

``validate_fetch_target`` is the one gate every URL the pipeline is about to
hand to a device must pass. It is called from two places (defence in depth):

- ``app.research.browser_activities.fetch_targets_activity``, when a
  discovered candidate becomes a fetch target — the earliest point a bad URL
  can be filtered out, before it even costs a Temporal activity/device
  command;
- ``app.research.browser_gateway.DeviceBrowserGateway.fetch_url``,
  immediately before the ``browser.fetch_evidence`` command is built — the
  point closest to the actual connection the device's Chrome will make.

Both calls matter: a hostname can resolve to a public address at discovery
time and be repointed at a private one by the time the fetch actually runs
(DNS rebinding), so the check re-resolves and re-validates at each call
rather than trusting an earlier verdict. A syntactic host check alone (deny
``localhost`` by string match, say) does not defend against rebinding or
against a hostname whose DNS record is simply private from the start; this
module always resolves the hostname and inspects every address it returns.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})

_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost")
_BLOCKED_HOSTNAMES = frozenset({"localhost"})

#: CGNAT (RFC 6598), used by Tailscale for the tailnet's own 100.x addresses.
#: Not covered by ``ipaddress.IPv4Address.is_private``, so it needs its own
#: check.
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


class DestinationPolicyError(ValueError):
    """A fetch target is refused by destination policy.

    ``error_class`` matches the device taxonomy (BROWSER_CAPABILITIES.md §5)
    so callers can surface it as the same typed, non-retryable
    ``security_scope_error`` the rest of the pipeline uses for policy
    refusals.
    """

    def __init__(self, message: str, *, error_class: str = "security_scope_error") -> None:
        super().__init__(message)
        self.error_class = error_class


def _is_blocked_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for loopback, RFC1918, link-local (incl. 169.254.169.254), CGNAT
    (the tailnet), multicast, reserved, or IPv6 ULA/link-local/loopback.
    ``is_private`` already covers RFC1918 + loopback + link-local for IPv4
    and ULA + link-local + loopback for IPv6; the rest are explicit."""
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return True
    if addr.is_multicast or addr.is_reserved or addr.is_unspecified:
        return True
    if isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT_NETWORK:
        return True
    return False


def _validate_scheme(scheme: str) -> None:
    if scheme.lower() not in ALLOWED_SCHEMES:
        raise DestinationPolicyError(
            f"scheme {scheme!r} is not a fetchable destination (http/https only)"
        )


def _validate_no_userinfo(netloc: str) -> None:
    if "@" in netloc:
        raise DestinationPolicyError("URL must not carry userinfo (user:pass@host)")


def _validate_hostname_shape(hostname: str) -> None:
    lowered = hostname.lower()
    if lowered in _BLOCKED_HOSTNAMES or any(
        lowered.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES
    ):
        raise DestinationPolicyError(f"host {hostname!r} is not a fetchable destination")


def _validate_ip_literal(hostname: str) -> None:
    """If the host is itself an IP literal, check it directly — resolving a
    literal is a no-op that would otherwise let the syntactic checks below be
    skipped entirely."""
    try:
        addr = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return  # not an IP literal; resolved separately below
    if _is_blocked_address(addr):
        raise DestinationPolicyError(f"address {hostname!r} is not a fetchable destination")


def resolve_hostname(hostname: str) -> list[str]:
    """Default resolver: ``socket.getaddrinfo``, every returned address.
    Tests inject a fake via the ``resolver`` parameter of
    :func:`validate_fetch_target`, or monkeypatch this function directly —
    both work, since ``validate_fetch_target`` looks this name up at call
    time rather than binding it as a default argument."""
    infos = socket.getaddrinfo(hostname, None)
    return [info[4][0] for info in infos]


def validate_fetch_target(
    url: str, *, resolver: Callable[[str], list[str]] | None = None
) -> None:
    """Raise :class:`DestinationPolicyError` unless ``url`` is a fetchable
    public-web destination. Checks, in order: scheme, userinfo, hostname
    shape (localhost/.local/.internal/.localhost), IP-literal host, then
    resolves the hostname and rejects if ANY resolved address is blocked
    (the DNS-rebinding defence — validated at call time, not cached from an
    earlier check)."""
    parts = urlsplit(url.strip())
    _validate_scheme(parts.scheme)
    _validate_no_userinfo(parts.netloc)
    hostname = parts.hostname
    if not hostname:
        raise DestinationPolicyError("URL has no host")
    _validate_hostname_shape(hostname)
    _validate_ip_literal(hostname)

    resolve = resolver if resolver is not None else resolve_hostname
    try:
        resolved = resolve(hostname)
    except DestinationPolicyError:
        raise
    except OSError as exc:
        raise DestinationPolicyError(f"could not resolve host {hostname!r}: {exc}") from exc

    for raw in resolved:
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            continue  # not a literal address (defensive; getaddrinfo always returns one)
        if _is_blocked_address(addr):
            raise DestinationPolicyError(
                f"host {hostname!r} resolves to a non-public address {raw!r}"
            )


__all__ = [
    "ALLOWED_SCHEMES",
    "DestinationPolicyError",
    "resolve_hostname",
    "validate_fetch_target",
]
