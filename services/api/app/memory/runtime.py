"""Memory runtime: DB session factory + embedder + backend selection.

Lives on app.state.memory (mirrors ArtifactRuntime/VoiceRuntime). The default
embedder is the frozen DeterministicEmbedder (offline, seeded); production
wires a real embedding provider behind the same Embedder protocol and runs
`lifecycle.reindex` to rebuild memory_embeddings for the new model. Tests may
inject an `engine` (e.g. SQLite StaticPool) and/or `embedder`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.logging import get_logger
from app.memory.embedding import DeterministicEmbedder, Embedder
from app.memory.providers import EmbedderReport, build_embedder
from app.memory.store import NativeMemoryBackend

logger = get_logger("app.memory.runtime")


class MemoryRuntime:
    def __init__(
        self,
        settings: Settings,
        *,
        engine: Engine | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.settings = settings
        self._engine: Engine | None = engine
        self._session_factory: sessionmaker[Session] | None = (
            build_session_factory(engine) if engine is not None else None
        )
        # B37 (req 51/53): the provider is chosen by configuration, with its reason;
        # an injected embedder (tests) is reported as what it is.
        if embedder is not None:
            self.embedder = embedder
            self.embedder_report = EmbedderReport(
                requested="injected",
                active="deterministic"
                if isinstance(embedder, DeterministicEmbedder)
                else "injected",
                model_id=embedder.model_id,
                model_version=embedder.model_version,
                dim=embedder.dim,
                semantic=not isinstance(embedder, DeterministicEmbedder),
            )
        else:
            self.embedder, self.embedder_report = build_embedder(settings)
        self._backend: NativeMemoryBackend | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = build_engine(self.settings.database_url)
            self._session_factory = build_session_factory(self._engine)
        return self._engine

    @contextlib.contextmanager
    def session(self) -> Iterator[Session]:
        _ = self.engine
        assert self._session_factory is not None
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    @property
    def backend(self) -> NativeMemoryBackend:
        if self._backend is None:
            self._backend = NativeMemoryBackend(self.session, self.embedder)
        return self._backend

    def health_check(self) -> dict[str, object]:
        """Backend + embedder identity for /v1/system/health ('memory').

        No I/O: DB reachability is already covered by the 'db' check; this
        reports which backend/embedding model serves memory retrieval.
        """
        return {
            "status": "ok",
            "latency_ms": 0.0,  # no I/O; keeps the uniform check shape
            "backend": self.backend.name,
            "embedder": {
                "model_id": self.embedder.model_id,
                "model_version": self.embedder.model_version,
                "dim": self.embedder.dim,
                # B37: which provider, whether it is semantic at all, and why not.
                "provider": self.embedder_report.active,
                "semantic": self.embedder_report.semantic,
                "fallback_reason": self.embedder_report.fallback_reason,
            },
        }

    # ------------------------------------------------------------ B37: the index

    def embedding_status(self) -> dict[str, object]:
        """The provider report beside the coverage of the memory table (req 53/54)."""
        return {
            "provider": self.embedder_report.as_dict(),
            "coverage": self.backend.embedding_coverage(),
        }

    def reindex(self, *, only_missing: bool = True) -> dict[str, object]:
        """Req 54: rebuild the active model's rows - the gaps only, or every memory."""
        rows = (
            self.backend.reindex_missing() if only_missing else self.backend.reindex(self.embedder)
        )
        return {"rows": rows, "model_id": self.embedder.model_id, "only_missing": only_missing}


__all__ = ["MemoryRuntime"]
