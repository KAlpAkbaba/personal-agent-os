"""Identity runtime: DB session factory + credential root + service.

Lives on ``app.state.identity`` (mirrors MemoryRuntime/SecurityRuntime). Tests
inject an ``engine`` (SQLite StaticPool) and an in-memory ``root``; nothing else
about the authentication path is stubbed, so a test that authenticates runs the
same hashing, expiry and audit code the owner's phone will.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.identity.root import CredentialRoot, FileCredentialRoot
from app.identity.service import AttemptLimiter, IdentityService
from app.logging import get_logger

logger = get_logger("app.identity.runtime")


class IdentityRuntime:
    def __init__(
        self,
        settings: Settings,
        *,
        engine: Engine | None = None,
        root: CredentialRoot | None = None,
    ) -> None:
        self.settings = settings
        self._engine: Engine | None = engine
        self._session_factory: sessionmaker[Session] | None = (
            build_session_factory(engine) if engine is not None else None
        )
        self.root: CredentialRoot = root or FileCredentialRoot(settings.identity_root_dir)
        self._service: IdentityService | None = None

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

    def use_root(self, root: CredentialRoot) -> None:
        """Swap the credential root (tests, and host-side recovery tooling)."""
        self.root = root
        self._service = None

    @property
    def service(self) -> IdentityService:
        if self._service is None:
            self._service = IdentityService(
                self.session,
                self.root,
                ttl_s=self.settings.session_ttl_s,
                idle_timeout_s=self.settings.session_idle_timeout_s,
                limiter=AttemptLimiter(
                    max_failures=self.settings.identity_auth_max_failures,
                    window_s=self.settings.identity_auth_failure_window_s,
                ),
            )
        return self._service

    def health_check(self) -> dict[str, object]:
        """Auth posture for /v1/system/health ('identity'). No I/O, no secrets.

        Deliberately does not report whether a credential is bootstrapped: the
        health endpoint is the one unauthenticated surface, and "nobody owns
        this system yet" is not something it should announce. The owner reads
        that from `python -m app.identity.recover --status` on the host.
        """
        return {
            "status": "ok",
            "latency_ms": 0.0,  # no I/O; keeps the uniform check shape
            "scheme": "owner_session_bearer",
            "credential_root": getattr(self.root, "kind", "unknown"),
            "session_ttl_s": self.settings.session_ttl_s,
            "session_idle_timeout_s": self.settings.session_idle_timeout_s,
            "fail_closed": True,
        }


__all__ = ["IdentityRuntime"]
