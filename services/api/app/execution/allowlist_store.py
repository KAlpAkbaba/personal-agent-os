"""The owner's cloud allow-list: the seed file plus the rows the editor writes (ADR-0218).

``app.execution.allowlist`` reads the shared JSON, which starts empty and stays the SEED. The
owner's own sites are ``team_state`` rows of kind ``allowlist`` - one row per registrable
domain, ``doc`` = ``{added_at, added_by}`` - so no table is added. :func:`acting_allowed` is
the same contract as the seed module's (``(True, "")`` or ``(False, reason)``, the deny-list
asked first and always winning), over seed + rows, read at every call so an add or a removal
applies at once. A process that never called :func:`bind` answers from the seed only: the cloud
never acts by default.

:func:`validate_site` is the editor's gate; it is stricter than the seed loader because the
small suffix table of ``app.webtask.sites`` does not know every public suffix.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.execution import allowlist
from app.team.models import TeamStateRow
from app.webtask.sites import denied_host, host_of, site_of

KIND_ALLOWLIST: Final = "allowlist"
SUBSYSTEM_TEAM: Final = "team"
EVENT_SITE_ADDED: Final = "allowlist.site_added"
EVENT_SITE_REMOVED: Final = "allowlist.site_removed"

NOT_ON_ALLOW_LIST: Final = allowlist.NOT_ON_ALLOW_LIST
DENY_LISTED: Final = allowlist.DENY_LISTED

SOURCE_SEED: Final = "seed"
SOURCE_OWNER: Final = "owner"
ADDED_BY_SHELL: Final = "shell"

#: ``team_state.key`` is String(80).
SITE_MAX_CHARS: Final = 80

#: First labels that name a category under a country suffix (``co.uk``, ``com.tr``).
_SECOND_LEVEL: Final = frozenset(
    {"com", "org", "net", "gov", "edu", "co", "ac", "gen", "bel", "pol", "mil", "k12", "av", "web"}
)
#: Top-level domains that are real registrable endings for a name such as ``co.com``.
_OPEN_TLDS: Final = frozenset(
    {
        "com",
        "org",
        "net",
        "edu",
        "gov",
        "io",
        "app",
        "dev",
        "info",
        "biz",
        "xyz",
        "online",
        "site",
        "shop",
        "store",
        "tech",
        "cloud",
        "ai",
        "me",
        "tv",
    }
)
_LABEL: Final = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True, slots=True)
class Refusal(Exception):
    """A site the editor will not list. ``code`` is machine-readable, ``message`` is Turkish."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class Entry:
    site: str
    source: str
    added_at: str = ""
    added_by: str = ""


def _normalise(raw: str) -> str:
    text = str(raw).strip()
    if "://" in text:
        text = host_of(text)
    text = text.strip().strip(".").lower()
    try:
        return text.encode("idna").decode("ascii") if text else text
    except UnicodeError:
        return text


def validate_site(raw: str) -> str:
    """The registrable domain ``raw`` names, or a :class:`Refusal` saying why not."""
    site = _normalise(raw)
    if not site:
        raise Refusal("empty_site", "Bir site adı gerekli.")
    if len(site) > SITE_MAX_CHARS:
        raise Refusal("not_a_registrable_domain", "Site adı çok uzun.")
    labels = site.split(".")
    if not all(_LABEL.match(label) for label in labels) or all(part.isdigit() for part in labels):
        raise Refusal("not_a_registrable_domain", f"{raw!r} bir alan adı değil.")
    if len(labels) == 1:
        raise Refusal("bare_public_suffix", f"{site} tek başına bir uzantıdır, bir site değil.")
    if (
        len(labels) == 2
        and labels[0] in _SECOND_LEVEL
        and (len(labels[1]) == 2 or labels[1] not in _OPEN_TLDS)
    ):
        raise Refusal(
            "bare_public_suffix", f"{site} bir genel uzantıdır (kayıt yapılabilir bir site değil)."
        )
    if site_of(f"https://{site}/") != site:
        raise Refusal(
            "not_a_registrable_domain",
            f"{site} kayıt yapılabilir alan adı değil; ana alan adını yazın ({site_of(f'https://{site}/')}).",
        )
    if denied_host(site) is not None:
        raise Refusal(
            "deny_listed_site", f"{site} yasaklı listede; bu sitede bulutta işlem yapılmaz."
        )
    return site


# ------------------------------------------------------------------ the rows

_SessionFactory = Callable[[], AbstractContextManager[Session]]
_FACTORY: _SessionFactory | None = None


def bind(factory: _SessionFactory) -> None:
    """Give the store its database (``ArtifactRuntime.session`` or a ``sessionmaker``)."""
    global _FACTORY
    _FACTORY = factory


def unbind() -> None:
    global _FACTORY
    _FACTORY = None


def _stamp(at: datetime | None = None) -> str:
    return (at or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def owner_entries() -> list[Entry]:
    if _FACTORY is None:
        return []
    with _FACTORY() as session:
        rows = session.execute(
            select(TeamStateRow)
            .where(TeamStateRow.kind == KIND_ALLOWLIST)
            .order_by(TeamStateRow.key)
        ).scalars()
        entries = [
            Entry(
                site=row.key,
                source=SOURCE_OWNER,
                added_at=str(row.doc.get("added_at", "")),
                added_by=str(row.doc.get("added_by", "")),
            )
            for row in rows
        ]
    entries.sort(key=lambda e: (e.added_at, e.site))
    return entries


def entries() -> list[Entry]:
    """Seed sites first, then the owner's in the order added. A row that is deny-listed
    (written behind the editor) is left out: the deny-list wins over every list."""
    seed = [Entry(site=s, source=SOURCE_SEED) for s in allowlist.sites()]
    seen = {e.site for e in seed}
    rows = [e for e in owner_entries() if e.site not in seen and denied_host(e.site) is None]
    return seed + rows


def effective_sites() -> tuple[str, ...]:
    """What the worker gets with a job: the merged list, deny-listed rows excluded."""
    return tuple(e.site for e in entries())


def acting_allowed(url: str) -> tuple[bool, str]:
    """``(True, "")`` or ``(False, reason)``; the deny-list is asked first and always wins."""
    if allowlist.denied(url) is not None:
        return False, DENY_LISTED
    host = host_of(url)
    if host and any(host == site or host.endswith("." + site) for site in effective_sites()):
        return True, ""
    return False, NOT_ON_ALLOW_LIST


def allowed(url: str) -> bool:
    return acting_allowed(url)[0]


def add(
    session: Session, site: str, *, added_by: str = ADDED_BY_SHELL, now: datetime | None = None
) -> bool:
    """Write the row; ``False`` when it was already there (nothing changed). Commits."""
    if session.get(TeamStateRow, (KIND_ALLOWLIST, site)) is not None:
        return False
    at = _stamp(now)
    session.add(
        TeamStateRow(
            kind=KIND_ALLOWLIST, key=site, doc={"added_at": at, "added_by": added_by}, updated_at=at
        )
    )
    session.commit()
    return True


def remove(session: Session, site: str) -> bool:
    row = session.get(TeamStateRow, (KIND_ALLOWLIST, site))
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def exists(session: Session, site: str) -> bool:
    return session.get(TeamStateRow, (KIND_ALLOWLIST, site)) is not None


def event_detail(site: str, *, channel: str) -> dict[str, Any]:
    return {"site": site, "actor": "owner", "channel": channel}


__all__ = [
    "ADDED_BY_SHELL",
    "DENY_LISTED",
    "EVENT_SITE_ADDED",
    "EVENT_SITE_REMOVED",
    "KIND_ALLOWLIST",
    "NOT_ON_ALLOW_LIST",
    "Entry",
    "Refusal",
    "acting_allowed",
    "add",
    "allowed",
    "bind",
    "effective_sites",
    "entries",
    "event_detail",
    "exists",
    "remove",
    "unbind",
    "validate_site",
]
