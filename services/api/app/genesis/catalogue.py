"""The spoken-name -> interface seam (M24_CAPABILITY_GENESIS_SPEC.md §6):
``GenesisInterfaceCatalogue`` resolves a target the owner NAMED ("sayaç
kutusu") to the interface the assistant already knows how to research
(``{name, url}``), and each entry's own operation aliases resolve a spoken
verb to the operation id the researched description will declare.

EMPTY in production until an owner registers a controllable local
application — there is no registration surface in M24 itself (a later
milestone's job); ``app.voice.intents`` imports :func:`get_catalogue` the
same way ``Intent.APP_OPEN`` imports ``app.operator.plans.resolve_app_alias``
for M19's OS-application allowlist, and the module-level singleton is reset
the same way ``set_holdoffs``/``set_engine``/``set_publisher`` are — by the
test harness, between cases, never by production code.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging import get_logger

logger = get_logger("app.genesis.catalogue")


@dataclass(frozen=True, slots=True)
class OperationAlias:
    """One spoken verb shape -> the operation id it names, for ONE interface."""

    operation_id: str
    #: Exact-token verb forms (module docstring's ``_has_exact`` shape) that name
    #: this operation when the owner's utterance also names the target.
    verbs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    name: str
    url: str
    #: Phrases (already casefolded, space-joined) that name this interface,
    #: longest first so "sayaç kutusunu" is preferred over a bare "sayaç" if
    #: both were ever registered.
    target_phrases: tuple[str, ...]
    operations: tuple[OperationAlias, ...] = ()

    def resolve_operation(self, tokens: tuple[str, ...]) -> str | None:
        for alias in self.operations:
            for tok in tokens:
                if tok in alias.verbs:
                    return alias.operation_id
        return None


class GenesisInterfaceCatalogue:
    """Longest-phrase-first spoken-name resolution, the same shape
    ``app.operator.plans.resolve_app_alias`` uses for M19's own allowlist."""

    def __init__(self) -> None:
        self._entries: list[CatalogueEntry] = []

    def register(self, entry: CatalogueEntry) -> None:
        self._entries.append(entry)
        self._entries.sort(key=lambda e: max((len(p) for p in e.target_phrases), default=0))
        self._entries.reverse()

    def resolve(self, tokens: tuple[str, ...]) -> CatalogueEntry | None:
        cleaned = " ".join(tok.split("'", 1)[0] for tok in tokens)
        for entry in self._entries:
            for phrase in entry.target_phrases:
                if phrase in cleaned:
                    return entry
        return None

    def clear(self) -> None:
        self._entries.clear()

    def entries(self) -> tuple[CatalogueEntry, ...]:
        return tuple(self._entries)


_catalogue = GenesisInterfaceCatalogue()


def get_catalogue() -> GenesisInterfaceCatalogue:
    return _catalogue


def set_catalogue(catalogue: GenesisInterfaceCatalogue) -> None:
    """Tests (and, later, an owner registration flow) swap the process-wide
    catalogue — mirrors ``app.uistate.publisher.set_publisher``."""
    global _catalogue
    _catalogue = catalogue


# ------------------------------------------------------------------ B36: persistence

#: A registered name is a manifest identifier token (the same shape the interface's own
#: ``name`` must have, ``app.genesis.interface.NAME_RE``).
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_PHRASES = 24
MAX_OPERATIONS = 16
MAX_VERBS = 24


def _clean_phrase(value: str) -> str:
    return " ".join(str(value).casefold().split())


def entry_to_dict(entry: CatalogueEntry) -> dict[str, Any]:
    return {
        "name": entry.name,
        "url": entry.url,
        "target_phrases": list(entry.target_phrases),
        "operations": [
            {"operation_id": op.operation_id, "verbs": list(op.verbs)} for op in entry.operations
        ],
    }


def entry_from_dict(raw: dict[str, Any]) -> CatalogueEntry:
    name = str(raw.get("name") or "")
    if not _NAME_RE.match(name):
        raise ValueError("catalogue entry name must be a lowercase identifier token")
    url = str(raw.get("url") or "").strip()
    if not url:
        raise ValueError("catalogue entry needs a url")
    phrases = tuple(
        dict.fromkeys(
            _clean_phrase(p) for p in (raw.get("target_phrases") or []) if _clean_phrase(p)
        )
    )[:MAX_PHRASES]
    if not phrases:
        raise ValueError("catalogue entry needs at least one spoken phrase")
    operations: list[OperationAlias] = []
    for op in (raw.get("operations") or [])[:MAX_OPERATIONS]:
        op_id = str((op or {}).get("operation_id") or "")
        if not _NAME_RE.match(op_id):
            raise ValueError("catalogue operation id must be a lowercase identifier token")
        verbs = tuple(
            dict.fromkeys(
                _clean_phrase(v) for v in ((op or {}).get("verbs") or []) if _clean_phrase(v)
            )
        )[:MAX_VERBS]
        operations.append(OperationAlias(op_id, verbs))
    return CatalogueEntry(
        name=name,
        url=url,
        target_phrases=tuple(sorted(phrases, key=len, reverse=True)),
        operations=tuple(operations),
    )


class CatalogueStore:
    """The rows behind the process-wide catalogue (B36 req 562/563/564): every write goes
    to the table AND rebuilds the in-memory catalogue the router reads, so a registered
    interface is spoken to in the same process at once and after a restart.

    ``fetch`` is the interface fetch (``app.genesis.interface.fetch_interface``), injected
    so discovery can be proven without a network; ``validate_url`` is the host rule the
    fetch applies (loopback, or an owner-authorized host)."""

    def __init__(
        self,
        session_factory: Callable[[], AbstractContextManager[Session]],
        *,
        catalogue: GenesisInterfaceCatalogue | None = None,
        fetch: Callable[[str], Any] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._catalogue = catalogue
        self._fetch = fetch
        self.loaded = False

    @property
    def catalogue(self) -> GenesisInterfaceCatalogue:
        return self._catalogue if self._catalogue is not None else get_catalogue()

    # ------------------------------------------------------------- reads

    def list(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        from app.genesis.models import GenesisCatalogueRow

        with self._session_factory() as session:
            rows = session.execute(
                select(GenesisCatalogueRow).order_by(GenesisCatalogueRow.name)
            ).scalars()
            return [self._row_dict(row) for row in rows if include_disabled or row.enabled]

    def load(self) -> int:
        """Rebuild the in-memory catalogue from the enabled rows. Returns the count."""
        from app.genesis.models import GenesisCatalogueRow

        catalogue = self.catalogue
        catalogue.clear()
        count = 0
        with self._session_factory() as session:
            rows = session.execute(
                select(GenesisCatalogueRow).where(GenesisCatalogueRow.enabled.is_(True))
            ).scalars()
            for row in rows:
                try:
                    catalogue.register(entry_from_dict(self._row_dict(row)))
                    count += 1
                except ValueError:
                    logger.warning("genesis_catalogue_row_invalid", name=row.name)
        self.loaded = True
        return count

    # ------------------------------------------------------------ writes

    def register(
        self,
        *,
        name: str,
        url: str,
        target_phrases: list[str],
        operations: list[dict[str, Any]] | None = None,
        source: str = "owner_rest",
        spec_digest: str | None = None,
    ) -> dict[str, Any]:
        from app.genesis.models import CATALOGUE_SOURCES, GenesisCatalogueRow

        entry = entry_from_dict(
            {
                "name": name,
                "url": url,
                "target_phrases": target_phrases,
                "operations": operations or [],
            }
        )
        if source not in CATALOGUE_SOURCES:
            raise ValueError("unknown catalogue source")
        now = datetime.now(UTC)
        with self._session_factory() as session:
            row = session.execute(
                select(GenesisCatalogueRow).where(GenesisCatalogueRow.name == entry.name)
            ).scalar_one_or_none()
            if row is None:
                row = GenesisCatalogueRow(name=entry.name, created_at=now)
                session.add(row)
            row.url = entry.url
            row.target_phrases_json = list(entry.target_phrases)
            row.operations_json = entry_to_dict(entry)["operations"]
            row.source = source
            row.enabled = True
            row.spec_digest = spec_digest
            row.last_seen_at = now if spec_digest else row.last_seen_at
            row.updated_at = now
            session.commit()
            session.refresh(row)
            out = self._row_dict(row)
        self.load()
        logger.info("genesis_catalogue_registered", name=entry.name, source=source)
        return out

    def disable(self, name: str) -> dict[str, Any]:
        from app.genesis.models import GenesisCatalogueRow

        with self._session_factory() as session:
            row = session.execute(
                select(GenesisCatalogueRow).where(GenesisCatalogueRow.name == name)
            ).scalar_one_or_none()
            if row is None:
                raise LookupError(name)
            row.enabled = False
            row.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            out = self._row_dict(row)
        self.load()
        return out

    def discover(self, url: str) -> dict[str, Any]:
        """Req 564: fetch the description at ``url`` and PROPOSE an entry from it - the
        interface's own name, its operations as verb aliases of their own ids - without
        registering anything. The owner adds the spoken phrases and registers."""
        if self._fetch is None:
            from app.genesis.interface import fetch_interface

            fetch = fetch_interface
        else:
            fetch = self._fetch
        interface = fetch(url)
        described = interface.to_dict()
        digest = hashlib.sha256(
            json.dumps(described, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return {
            "name": interface.name,
            "url": url,
            "base_url": interface.base_url,
            "target_phrases": [],
            "operations": [
                {"operation_id": op.id, "verbs": [op.id], "side_effect": op.side_effect}
                for op in interface.operations
            ],
            "read_back": interface.evidence.get("read_back"),
            "spec_digest": digest,
        }

    @staticmethod
    def _row_dict(row: Any) -> dict[str, Any]:
        return {
            "name": row.name,
            "url": row.url,
            "target_phrases": list(row.target_phrases_json or []),
            "operations": list(row.operations_json or []),
            "source": row.source,
            "enabled": bool(row.enabled),
            "spec_digest": row.spec_digest,
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


__all__ = [
    "CatalogueEntry",
    "CatalogueStore",
    "GenesisInterfaceCatalogue",
    "OperationAlias",
    "entry_from_dict",
    "entry_to_dict",
    "get_catalogue",
    "set_catalogue",
]
