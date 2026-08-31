"""Memory backend abstraction (MEMORY_SPEC §3).

`MemoryBackend` captures the FULL behavioral contract — write / edit /
supersede / forget / retrieve / inspect plus lifecycle operations — so an
external framework (e.g. Mem0) can later replace or augment the native
implementation WITHOUT changing the core data model. PostgreSQL stays the
canonical source of truth; any external framework is an adapter behind this
Protocol and must never become an opaque store that prevents export or
migration.

`NativeMemoryBackend` is the (only) real implementation, delegating to the
policy/service/lifecycle/retrieval modules. `Mem0Backend` is a stub that
raises a typed BACKEND_NOT_IMPLEMENTED error on every call, documenting the
seam and its constraints for a future integration.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.memory import lifecycle, retrieval, service
from app.memory.embedding import Embedder
from app.memory.errors import MemoryErrorClass, MemorySubsystemError
from app.memory.policy import Observation
from app.memory.retrieval import RetrievalFilters
from app.memory.service import MemoryLinks, ObserveResult
from app.memory.types import Actor

SessionScope = Callable[[], AbstractContextManager[Session]]


@runtime_checkable
class MemoryBackend(Protocol):
    """Full memory contract. All methods are synchronous (callers use
    asyncio.to_thread) and return JSON-safe payloads."""

    # ------------------------------------------------------------- writes
    def observe(self, obs: Observation, links: MemoryLinks | None = None) -> ObserveResult:
        """Run the write policy on one observation (may refuse/ignore)."""
        ...

    def remember(
        self,
        *,
        text: str,
        memory_class: Any,
        key: str | None = None,
        value: dict[str, Any] | None = None,
        links: MemoryLinks | None = None,
        source: dict[str, Any] | None = None,
    ) -> ObserveResult:
        """Explicit owner teach: durable immediately."""
        ...

    # -------------------------------------------------------------- edits
    def edit(
        self,
        memory_id: uuid.UUID,
        *,
        actor: Actor,
        text: str | None = None,
        value: dict[str, Any] | None = None,
        change_reason: str = "",
    ) -> dict[str, Any]: ...

    def supersede(
        self,
        memory_id: uuid.UUID,
        *,
        actor: Actor,
        text: str,
        value: dict[str, Any] | None = None,
        reason: str = "",
    ) -> dict[str, Any]: ...

    def pin(self, memory_id: uuid.UUID, *, actor: Actor) -> dict[str, Any]: ...

    def forget(
        self, memory_id: uuid.UUID, *, actor: Actor, reason: str = "owner_request"
    ) -> dict[str, Any]: ...

    # ----------------------------------------------------------- retrieval
    def search(
        self,
        *,
        query: str | None,
        filters: RetrievalFilters,
        k: int = retrieval.DEFAULT_K,
    ) -> list[dict[str, Any]]: ...

    def inspect(self, memory_id: uuid.UUID) -> dict[str, Any]: ...

    def audit_events(self, *, limit: int = 50) -> list[dict[str, Any]]: ...

    # ----------------------------------------------------------- lifecycle
    def sweep_expired(self) -> int: ...

    def reindex(self, new_embedder: Embedder) -> int: ...

    def detect_procedures(self) -> list[dict[str, Any]]: ...


class NativeMemoryBackend:
    """PostgreSQL-canonical implementation on top of the frozen data model."""

    name = "native"

    def __init__(self, session_scope: SessionScope, embedder: Embedder) -> None:
        self._session_scope = session_scope
        self.embedder = embedder

    # ------------------------------------------------------------- writes
    def observe(self, obs: Observation, links: MemoryLinks | None = None) -> ObserveResult:
        with self._session_scope() as session:
            return service.record_observation(session, self.embedder, obs, links)

    def remember(
        self,
        *,
        text: str,
        memory_class: Any,
        key: str | None = None,
        value: dict[str, Any] | None = None,
        links: MemoryLinks | None = None,
        source: dict[str, Any] | None = None,
    ) -> ObserveResult:
        with self._session_scope() as session:
            return service.remember_explicit(
                session,
                self.embedder,
                text=text,
                memory_class=memory_class,
                key=key,
                value=value,
                links=links,
                source=source,
            )

    # -------------------------------------------------------------- edits
    def edit(
        self,
        memory_id: uuid.UUID,
        *,
        actor: Actor,
        text: str | None = None,
        value: dict[str, Any] | None = None,
        change_reason: str = "",
    ) -> dict[str, Any]:
        with self._session_scope() as session:
            memory = service.edit_memory(
                session,
                self.embedder,
                memory_id,
                actor=actor,
                text=text,
                value=value,
                change_reason=change_reason,
            )
            return retrieval.to_payload(memory)

    def supersede(
        self,
        memory_id: uuid.UUID,
        *,
        actor: Actor,
        text: str,
        value: dict[str, Any] | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        with self._session_scope() as session:
            new = service.supersede_memory(
                session,
                self.embedder,
                memory_id,
                actor=actor,
                text=text,
                value=value,
                reason=reason,
            )
            return retrieval.to_payload(new)

    def pin(self, memory_id: uuid.UUID, *, actor: Actor) -> dict[str, Any]:
        with self._session_scope() as session:
            return retrieval.to_payload(service.pin_memory(session, memory_id, actor=actor))

    def forget(
        self, memory_id: uuid.UUID, *, actor: Actor, reason: str = "owner_request"
    ) -> dict[str, Any]:
        with self._session_scope() as session:
            counts = service.forget_memory(session, memory_id, actor=actor, reason=reason)
            return {"forgotten": True, "memory_id": str(memory_id), "deleted": counts}

    # ----------------------------------------------------------- retrieval
    def search(
        self,
        *,
        query: str | None,
        filters: RetrievalFilters,
        k: int = retrieval.DEFAULT_K,
    ) -> list[dict[str, Any]]:
        with self._session_scope() as session:
            scored = retrieval.hybrid_search(session, self.embedder, query, filters, k=k)
            return [
                {
                    **retrieval.to_payload(item.memory),
                    "score": round(item.score, 6),
                    "score_components": {
                        name: round(v, 6) for name, v in item.components.items()
                    },
                }
                for item in scored
            ]

    def inspect(self, memory_id: uuid.UUID) -> dict[str, Any]:
        with self._session_scope() as session:
            return service.inspect_memory(session, memory_id)

    def audit_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._session_scope() as session:
            return service.list_audit_events(session, limit=limit)

    # ----------------------------------------------------------- lifecycle
    def sweep_expired(self) -> int:
        with self._session_scope() as session:
            return lifecycle.sweep_expired(session)

    def reindex(self, new_embedder: Embedder) -> int:
        with self._session_scope() as session:
            return lifecycle.reindex(session, new_embedder)

    def detect_procedures(self) -> list[dict[str, Any]]:
        with self._session_scope() as session:
            proposals = lifecycle.detect_procedures(session, self.embedder)
            return [retrieval.to_payload(p) for p in proposals]


def _not_implemented(operation: str) -> MemorySubsystemError:
    return MemorySubsystemError(
        MemoryErrorClass.BACKEND_NOT_IMPLEMENTED,
        f"Mem0Backend.{operation} is a documented seam, not an implementation. "
        "PostgreSQL remains canonical; a Mem0 adapter may accelerate "
        "extraction/retrieval but must sync into the canonical schema "
        "(MEMORY_SPEC §3).",
        details={"backend": "mem0", "operation": operation},
    )


class Mem0Backend:
    """Documented adapter seam for a future Mem0 (or similar) integration.

    Constraints for whoever implements this:
    - PostgreSQL rows stay canonical: every write must land in (or sync back
      to) the frozen schema; Mem0 may only add acceleration/extraction.
    - Forget must propagate: a hard delete here must delete the Mem0-side
      representation too, in the same operation.
    - The write policy (policy.py) and secrets guard run BEFORE any Mem0 call.
    """

    name = "mem0"

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:  # pragma: no cover - seam
        pass

    def observe(self, obs: Observation, links: MemoryLinks | None = None) -> ObserveResult:
        raise _not_implemented("observe")

    def remember(self, **_kwargs: Any) -> ObserveResult:
        raise _not_implemented("remember")

    def edit(self, memory_id: uuid.UUID, **_kwargs: Any) -> dict[str, Any]:
        raise _not_implemented("edit")

    def supersede(self, memory_id: uuid.UUID, **_kwargs: Any) -> dict[str, Any]:
        raise _not_implemented("supersede")

    def pin(self, memory_id: uuid.UUID, **_kwargs: Any) -> dict[str, Any]:
        raise _not_implemented("pin")

    def forget(self, memory_id: uuid.UUID, **_kwargs: Any) -> dict[str, Any]:
        raise _not_implemented("forget")

    def search(self, **_kwargs: Any) -> list[dict[str, Any]]:
        raise _not_implemented("search")

    def inspect(self, memory_id: uuid.UUID) -> dict[str, Any]:
        raise _not_implemented("inspect")

    def audit_events(self, **_kwargs: Any) -> list[dict[str, Any]]:
        raise _not_implemented("audit_events")

    def sweep_expired(self) -> int:
        raise _not_implemented("sweep_expired")

    def reindex(self, new_embedder: Embedder) -> int:
        raise _not_implemented("reindex")

    def detect_procedures(self) -> list[dict[str, Any]]:
        raise _not_implemented("detect_procedures")


__all__ = ["Mem0Backend", "MemoryBackend", "NativeMemoryBackend", "SessionScope"]
