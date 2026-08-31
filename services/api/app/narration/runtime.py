"""Narration runtime: DB session factory for the narration service.

Mirrors ArtifactRuntime but needs no object store (narration persists only the
cursor + pronunciation dictionary; audio bytes are ephemeral and live in the
in-process ``ChunkCache``). Lives on ``app.state.narration``; the routes create
it lazily on first use so app wiring in main.py stays a single import + one
``include_router`` line.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.logging import get_logger

logger = get_logger("app.narration.runtime")


class NarrationRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

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


__all__ = ["NarrationRuntime"]
