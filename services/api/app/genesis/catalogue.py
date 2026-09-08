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

from dataclasses import dataclass


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


__all__ = [
    "CatalogueEntry",
    "GenesisInterfaceCatalogue",
    "OperationAlias",
    "get_catalogue",
    "set_catalogue",
]
