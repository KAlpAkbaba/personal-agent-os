"""The sites a cloud browser job may ACT on (ADR-0213 addendum, option 4) - the worker's half.

``SITES`` is written verbatim from ``packages/protocol/browser-cloud-allowlist.json`` - the
ONE source both sides read - and ``tests/unit/test_cloud_allowlist.py`` holds the two equal.
The worker carries the list in its own source because an installed worker has no repository
beside it. A cloud job acts only on a listed site (or a subdomain of one) and never on a
deny-listed site, listed or not; the empty list allows nothing.

Contract v1.9: a cloud task's ``session_open`` brings the owner's CURRENT list
(``owner_allow_list``). It is added to ``SITES``, never put in its place: the seed is the
floor both sides start from, the session's list what the owner added since.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

from browser_agent.task_denylist import denied, host_of

#: registrable domains the owner allowed; empty at first.
SITES: Final[tuple[str, ...]] = ()

NOT_ON_ALLOW_LIST: Final = "not_on_owner_allow_list"
DENY_LISTED: Final = "deny_listed_site"

# The registrable-domain rule of ``services/api/app/webtask/sites.py::site_of`` and the
# editor's gate ``app/execution/allowlist_store.py::validate_site`` (the list's only writer).
_SECOND_LEVEL: Final = frozenset(
    {"com", "org", "net", "gov", "edu", "co", "ac", "gen", "bel", "pol", "mil", "k12", "av", "web"}
)
_COUNTRY_WITH_SECOND_LEVEL: Final = frozenset(
    {"tr", "uk", "au", "br", "jp", "in", "za", "nz", "mx", "ar", "cn", "kr", "il", "sg", "hk"}
)
_OPEN_TLDS: Final = frozenset(
    {
        "com", "org", "net", "edu", "gov", "io", "app", "dev", "info", "biz",
        "xyz", "online", "site", "shop", "store", "tech", "cloud", "ai", "me", "tv",
    }
)  # fmt: skip
_LABEL: Final = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def site_of(url: str) -> str:
    """The registrable domain of ``url``'s host (``odeme.magaza.com.tr`` ->
    ``magaza.com.tr``); an IP address is its own site; ``""`` when there is no host."""
    host = host_of(url)
    labels = [label for label in host.split(".") if label]
    if len(labels) <= 2 or all(label.isdigit() for label in labels):
        return host
    if labels[-1] in _COUNTRY_WITH_SECOND_LEVEL and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def valid_site(name: object) -> bool:
    """True when ``name`` is written as the editor writes a site: lower case, a
    registrable domain, not a bare public suffix. Deny-listed names are not refused here:
    the deny-list is asked first at every write and always wins."""
    if not isinstance(name, str) or not name or len(name) > 80:
        return False
    labels = name.split(".")
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        return False
    if (
        len(labels) == 2
        and labels[0] in _SECOND_LEVEL
        and (len(labels[1]) == 2 or labels[1] not in _OPEN_TLDS)
    ):
        return False
    return site_of(f"https://{name}/") == name


def acting_allowed(url: str, extra_sites: Iterable[str] = ()) -> tuple[bool, str]:
    """``(True, "")`` or ``(False, reason)``; the deny-list is asked first and always wins.
    ``extra_sites`` is a cloud task's own list, added to ``SITES``."""
    if denied(url) is not None:
        return False, DENY_LISTED
    host = host_of(url)
    listed = (*SITES, *extra_sites)
    if host and any(host == site or host.endswith("." + site) for site in listed):
        return True, ""
    return False, NOT_ON_ALLOW_LIST


def allowed(url: str) -> bool:
    return acting_allowed(url)[0]


__all__ = [
    "DENY_LISTED",
    "NOT_ON_ALLOW_LIST",
    "SITES",
    "acting_allowed",
    "allowed",
    "site_of",
    "valid_site",
]
