"""Mobile runtime: DB session factory + push provider registry + service.

Lives on ``app.state.mobile`` (mirrors IdentityRuntime/ArtifactRuntime). Tests
inject a SQLite engine; nothing about the push path is stubbed beyond the
provider itself, which is the seam that is *supposed* to be swapped.

The provider registry is built once and held here because a push provider is
stateful in a way the rest of this codebase's providers are not: it holds the
live vendor tokens that the database, by design, does not (see
`app/mobile/models.py`). Rebuilding it per request would silently drop every
registration's delivery target.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.logging import get_logger
from app.mobile.config import share_max_bytes
from app.mobile.providers import FakePushProvider, PushProvider, build_registry
from app.mobile.service import MobileService

logger = get_logger("app.mobile.runtime")


class MobileRuntime:
    def __init__(
        self,
        settings: Settings,
        *,
        engine: Engine | None = None,
        providers: dict[str, PushProvider] | None = None,
    ) -> None:
        self.settings = settings
        self._engine: Engine | None = engine
        self._session_factory: sessionmaker[Session] | None = (
            build_session_factory(engine) if engine is not None else None
        )
        self._providers: dict[str, PushProvider] = providers or build_registry()
        self._service: MobileService | None = None

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
    def providers(self) -> dict[str, PushProvider]:
        return self._providers

    @property
    def fake(self) -> FakePushProvider:
        """The deterministic provider, for the suite and the reference client."""
        provider = self._providers[FakePushProvider.name]
        assert isinstance(provider, FakePushProvider)
        return provider

    @property
    def service(self) -> MobileService:
        if self._service is None:
            self._service = MobileService(self.session, self._providers)
        return self._service

    def health_check(self) -> dict[str, object]:
        """Push posture for /v1/system/health ('mobile'). No I/O, no secrets.

        Absence of push credentials is the expected default — the real adapters
        are inert until an owner provisions them — so this is 'ok' with an
        activation map, exactly like the voice check.
        """
        service = self.service
        activated = {
            caps["name"]: bool(caps["activated"])
            for caps in service.capabilities()
            if caps["requires_credentials"]
        }
        return {
            "status": "ok",
            "latency_ms": 0.0,  # no I/O; keeps the uniform check shape
            "push_providers": sorted(self._providers),
            "real_providers_activated": activated,
            "share_max_bytes": share_max_bytes(),
            "note": "empty push credentials are expected; the fake is deterministic/offline",
        }


__all__ = ["MobileRuntime"]
