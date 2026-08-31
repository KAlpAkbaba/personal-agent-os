"""Artifact runtime: DB session factory + ObjectStore for the artifact service.

Lives on app.state.artifacts (mirrors BrokerRuntime). Activities in the Temporal
worker build the same context from settings via `build_artifact_context` so the
persistence + render-store code is identical in-process and in the worker.
"""

import contextlib
from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.logging import get_logger
from app.object_store import S3ObjectStore

logger = get_logger("app.artifacts.runtime")


class ArtifactRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._store: S3ObjectStore | None = None

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
    def store(self) -> S3ObjectStore:
        if self._store is None:
            self._store = S3ObjectStore.from_settings(self.settings)
        return self._store

    async def start(self) -> None:
        """Best-effort bucket creation; never blocks app startup on object store."""
        import asyncio

        try:
            await asyncio.to_thread(self.store.ensure_bucket)
        except Exception as exc:  # noqa: BLE001 - object store may be down at boot
            logger.warning(
                "artifact_bucket_ensure_failed", error=f"{type(exc).__name__}: {exc}"
            )

    def health_check(self) -> dict[str, object]:
        """Object-store reachability for /v1/system/health ('artifacts')."""
        import time

        started = time.perf_counter()
        try:
            self.store.health_check()
        except Exception as exc:  # noqa: BLE001 - health checks never raise
            return {
                "status": "fail",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }


def build_artifact_context(settings: Settings) -> tuple[sessionmaker[Session], S3ObjectStore]:
    """Session factory + object store for use inside Temporal activities."""
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    store = S3ObjectStore.from_settings(settings)
    return factory, store
