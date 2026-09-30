"""The sites a cloud browser job may ACT on (ADR-0213 addendum, option 4) - the worker's half.

``SITES`` is written verbatim from ``packages/protocol/browser-cloud-allowlist.json`` - the
ONE source both sides read - and ``tests/unit/test_cloud_allowlist.py`` holds the two equal.
The worker carries the list in its own source because an installed worker has no repository
beside it. A cloud job acts only on a listed site (or a subdomain of one) and never on a
deny-listed site, listed or not; the empty list allows nothing.
"""

from __future__ import annotations

from typing import Final

from browser_agent.task_denylist import denied, host_of

#: registrable domains the owner allowed; empty at first.
SITES: Final[tuple[str, ...]] = ()

NOT_ON_ALLOW_LIST: Final = "not_on_owner_allow_list"
DENY_LISTED: Final = "deny_listed_site"


def acting_allowed(url: str) -> tuple[bool, str]:
    """``(True, "")`` or ``(False, reason)``; the deny-list is asked first and always wins."""
    if denied(url) is not None:
        return False, DENY_LISTED
    host = host_of(url)
    if host and any(host == site or host.endswith("." + site) for site in SITES):
        return True, ""
    return False, NOT_ON_ALLOW_LIST


def allowed(url: str) -> bool:
    return acting_allowed(url)[0]


__all__ = ["DENY_LISTED", "NOT_ON_ALLOW_LIST", "SITES", "acting_allowed", "allowed"]
