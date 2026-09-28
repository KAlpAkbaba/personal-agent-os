"""The site a page belongs to, and the sites no task acts on (ADR-0207 c, decision 5).

Two questions, both answered from the URL and never from the page's text - a page can
write anything about itself:

* ``site_of(url)`` - the registrable domain, for the read-back ("hangi sitede") and the
  trail. ``www.magaza.com.tr`` and ``odeme.magaza.com.tr`` are both ``magaza.com.tr``.
* ``denied(url)`` - the deny-list category the host falls in, or ``None``. The list is
  ``packages/protocol/browser-task-denylist.json``: banks, e-Devlet, payment providers,
  password managers, the employer's systems, Kolay Monitor, and this system's own pages.
  On a denied site a task may observe and read, and may leave. It does nothing else.

The registrable domain is found with a SMALL table of two-level public suffixes rather
than the full Public Suffix List: the read-back names a site for a person to recognise,
and the deny-list matches on the whole host anyway, so a suffix this table does not know
costs a slightly longer name in a sentence and nothing in safety.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from app.protocol_files import protocol_file

#: The run-time copy of ``packages/protocol/browser-task-denylist.json``
#: (app/protocol_files.py): the image holds no repository.
DENYLIST_PATH: Final[Path] = protocol_file("browser-task-denylist.json")

_SECOND_LEVEL: Final = frozenset(
    {"com", "org", "net", "gov", "edu", "co", "ac", "gen", "bel", "pol", "mil", "k12", "av", "web"}
)
_COUNTRY_WITH_SECOND_LEVEL: Final = frozenset(
    {"tr", "uk", "au", "br", "jp", "in", "za", "nz", "mx", "ar", "cn", "kr", "il", "sg", "hk"}
)


def host_of(url: str) -> str:
    """The host, lower-cased, without port or userinfo; ``""`` for anything that is not
    an http(s) URL with a host."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https"):
        return ""
    return (parts.hostname or "").rstrip(".").lower()


def site_of(url: str) -> str:
    host = host_of(url)
    labels = [label for label in host.split(".") if label]
    if len(labels) <= 2:
        return host
    if labels[-1] in _COUNTRY_WITH_SECOND_LEVEL and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


@dataclass(frozen=True, slots=True)
class Category:
    name: str
    domains: tuple[str, ...]
    host_label_parts: tuple[str, ...]


@lru_cache(maxsize=1)
def categories() -> tuple[Category, ...]:
    """The shared file, read once. No fallback when it is missing."""
    shared = json.loads(DENYLIST_PATH.read_text(encoding="utf-8"))
    return tuple(
        Category(
            name=str(name),
            domains=tuple(str(d).lower().strip(".") for d in body.get("domains") or []),
            host_label_parts=tuple(str(p).lower() for p in body.get("host_label_parts") or []),
        )
        for name, body in shared["categories"].items()
    )


def denied_host(host: str) -> str | None:
    host = host.rstrip(".").lower()
    if not host:
        return None
    labels = host.split(".")
    for category in categories():
        for domain in category.domains:
            if host == domain or host.endswith("." + domain):
                return category.name
        for part in category.host_label_parts:
            if part and any(part in label for label in labels):
                return category.name
    return None


def denied(url: str) -> str | None:
    """The category that puts this URL's host on the deny-list, or ``None``."""
    return denied_host(host_of(url))


__all__ = ["DENYLIST_PATH", "Category", "categories", "denied", "denied_host", "host_of", "site_of"]
