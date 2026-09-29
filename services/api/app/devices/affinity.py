"""Where is this session? The inputs to session device affinity (ADR-0208).

``app.devices.selection.select_device`` takes a list of device ids and decides what they are
worth. This module is where the list comes from, in the order the caller trusts them:

1. a device id the CLIENT DECLARED for the session (``declared_device_id`` on the realtime
   session, sent by a client that knows which enrolled machine it is running on);
2. the device an authenticated identity session is bound to (``OwnerSession.device_id`` -
   the native clients set it);
3. the enrolled device that is connected from the SAME network address as this request
   (:func:`source_device_id`), when ``device_affinity_by_source_ip`` is on.

None of the three is authority. The owner session is what authorises a command; these only
choose among devices the owner could have chosen by name, and ``select_device`` skips an id
that names no live enrolled device without saying so.

The address is where the sharp edges are. Cloud Core runs behind an nginx edge in Docker, so
the transport peer of a request is the EDGE, and the caller's address is in ``X-Real-IP`` -
which the edge sets from its own ``$remote_addr`` on every request and every device
connection (``infra/docker/edge/nginx.conf``). A header is only as good as whoever sent it,
so it is believed from a peer inside ``trusted_proxy_cidrs`` and from no one else; from any
other peer the header is the caller's own claim and is ignored. ``X-Forwarded-For`` is never
read: the edge APPENDS to whatever the client sent, so its left end is the caller's to write.
"""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

#: The one header believed, and only from a trusted peer. Set by the edge from ``$remote_addr``,
#: replacing anything the client sent (nginx ``proxy_set_header`` overwrites, it does not append).
REAL_IP_HEADER = "x-real-ip"


def parse_ip(value: object) -> IPAddress | None:
    """A textual address as an address object, or ``None`` for anything else (a hostname,
    ``"testclient"``, junk, ``None``). An IPv4-mapped IPv6 address is the IPv4 address."""
    if not isinstance(value, str):
        return None
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def trusted_networks(cidrs: Iterable[str]) -> tuple[IPNetwork, ...]:
    """The configured proxy networks. A malformed entry is skipped here (settings validation
    is what refuses it at start-up); trusting less is the safe direction."""
    networks: list[IPNetwork] = []
    for cidr in cidrs:
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def client_ip(
    peer_host: str | None,
    headers: Mapping[str, str] | Any,
    trusted: tuple[IPNetwork, ...],
) -> str | None:
    """The address of whoever is really calling, or ``None`` when that cannot be told.

    * peer is not an address (``"testclient"``, a unix socket, missing): ``None``.
    * peer is inside ``trusted``: the address in ``X-Real-IP``. If the header is missing or
      is not a single address the answer is ``None`` - the peer is the proxy, and the proxy
      is not the caller.
    * anyone else: the peer address itself; whatever headers came with it are the caller's
      own words and are not read.
    """
    peer = parse_ip(peer_host)
    if peer is None:
        return None
    if any(peer in network for network in trusted):
        forwarded = parse_ip(headers.get(REAL_IP_HEADER))
        return str(forwarded) if forwarded is not None else None
    return str(peer)


def parse_device_id(value: object) -> uuid.UUID | None:
    """A device id as a UUID; ``None`` for anything that is not one (never an error)."""
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value.strip())
    except ValueError:
        return None


def session_device_ids(
    *, declared: object = None, bound: object = None, source: object = None
) -> tuple[uuid.UUID, ...]:
    """The candidates, best first, without repeats and without what is not an id."""
    out: list[uuid.UUID] = []
    for raw in (declared, bound, source):
        parsed = parse_device_id(raw)
        if parsed is not None and parsed not in out:
            out.append(parsed)
    return tuple(out)


def source_device_id(
    *,
    peer_host: str | None,
    headers: Mapping[str, str] | Any,
    broker: Any,
    settings: Any,
) -> uuid.UUID | None:
    """The enrolled device connected from this request's address, when the setting allows it.

    ``None`` when the setting is off (the default), when the address cannot be told, when no
    connected device has it, and when MORE THAN ONE has it - two machines behind one address
    cannot be told apart by it, and guessing is exactly what this must not do.
    """
    if not getattr(settings, "device_affinity_by_source_ip", False) or broker is None:
        return None
    ip = client_ip(peer_host, headers, trusted_networks(settings.trusted_proxy_cidrs))
    if ip is None:
        return None
    return broker.device_id_for_peer_ip(ip)


__all__ = [
    "REAL_IP_HEADER",
    "client_ip",
    "parse_device_id",
    "parse_ip",
    "session_device_ids",
    "source_device_id",
    "trusted_networks",
]
