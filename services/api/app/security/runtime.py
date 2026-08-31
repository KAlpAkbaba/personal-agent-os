"""Security runtime: DB session factory, registry/scope/assessment services.

Lives on ``app.state.security`` (mirrors EvolutionRuntime/ArtifactRuntime).
Tests inject an ``engine`` (SQLite StaticPool) and, for artifact publication,
an ``ObjectStore``.

No configuration knobs are introduced. What the security agent may touch is
recorded per asset in the registry (`constraints.config_roots`,
`allowed_testing`, `max_disruption`), not in environment variables — an env var
is not owner authorization, and a scope authority that a stray environment
variable could widen would defeat the point of the table.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.logging import get_logger
from app.object_store import ObjectStore, S3ObjectStore
from app.security.assessments import AssessmentService
from app.security.checks import CHECKS
from app.security.provider import RegistryAuthorizationProvider
from app.security.redaction import SECRET_PATTERNS
from app.security.registry import AuthorizedAssetRegistry
from app.security.remediation import RemediationService
from app.security.scope import ScopeGuard

logger = get_logger("app.security.runtime")


class SecurityRuntime:
    def __init__(
        self,
        settings: Settings,
        *,
        engine: Engine | None = None,
        store: ObjectStore | None = None,
    ) -> None:
        self.settings = settings
        self._engine: Engine | None = engine
        self._session_factory: sessionmaker[Session] | None = (
            build_session_factory(engine) if engine is not None else None
        )
        self._store: ObjectStore | None = store

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
    def store(self) -> ObjectStore:
        if self._store is None:
            self._store = S3ObjectStore.from_settings(self.settings)
        return self._store

    @property
    def registry(self) -> AuthorizedAssetRegistry:
        return AuthorizedAssetRegistry(self.session)

    @property
    def guard(self) -> ScopeGuard:
        return ScopeGuard(self.registry)

    @property
    def assessments(self) -> AssessmentService:
        return AssessmentService(self.session, self.registry, self.guard)

    @property
    def remediation(self) -> RemediationService:
        return RemediationService(self.session, self.registry, self.guard)

    @property
    def authorization(self) -> RegistryAuthorizationProvider:
        """The verified source M7's permission model was waiting for."""
        return RegistryAuthorizationProvider(self.session)

    def health_check(self) -> dict[str, object]:
        """Posture only, no I/O — DB reachability is the 'db' check's job."""
        return {
            "status": "ok",
            "latency_ms": 0.0,
            "scope_authority": "authorized_asset_registry",
            "fail_safe": True,
            "collector": "configuration_audit",
            "checks_available": len(CHECKS),
            "redaction_patterns": len(SECRET_PATTERNS),
            "network_scanning": False,
            "authorization_provider": RegistryAuthorizationProvider.name,
        }


__all__ = ["SecurityRuntime"]
