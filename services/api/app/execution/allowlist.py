"""The sites a cloud browser job may ACT on (ADR-0213 addendum, option 4).

A cloud job acts only on a site of the owner's allow-list and reads everywhere else. The
list is ``packages/protocol/browser-cloud-allowlist.json`` (empty at first, so a cloud job
acts nowhere until the owner adds a site); the worker carries a verbatim copy in
``browser_agent/cloud_allowlist.py``. A deny-listed site is never allowed, even when listed.
The caller (task execution-target-wiring) asks ``acting_allowed``; this module decides
nothing about targets.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Final

from app.protocol_files import protocol_file
from app.webtask.sites import denied, host_of, site_of

#: The run-time copy of ``packages/protocol/browser-cloud-allowlist.json``.
ALLOWLIST_PATH: Final[Path] = protocol_file("browser-cloud-allowlist.json")

NOT_ON_ALLOW_LIST: Final = "not_on_owner_allow_list"
DENY_LISTED: Final = "deny_listed_site"


@lru_cache(maxsize=1)
def sites() -> tuple[str, ...]:
    """The listed registrable domains, read once. A malformed entry is an error, never skipped."""
    shared = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    out: list[str] = []
    for raw in shared["sites"]:
        site = str(raw).strip().strip(".").lower()
        if not site or site_of(f"https://{site}/") != site or "." not in site:
            raise ValueError(f"allow-list entry {raw!r} is not a registrable domain")
        out.append(site)
    return tuple(out)


def allowed(url: str) -> bool:
    """True when the URL's host is a listed site or a subdomain of one and is not deny-listed."""
    return acting_allowed(url)[0]


def acting_allowed(url: str) -> tuple[bool, str]:
    """``(True, "")`` or ``(False, reason)``; the deny-list is asked first and always wins."""
    host = host_of(url)
    if denied(url) is not None:
        return False, DENY_LISTED
    if host and any(host == site or host.endswith("." + site) for site in sites()):
        return True, ""
    return False, NOT_ON_ALLOW_LIST


__all__ = [
    "ALLOWLIST_PATH",
    "DENY_LISTED",
    "NOT_ON_ALLOW_LIST",
    "acting_allowed",
    "allowed",
    "sites",
]
