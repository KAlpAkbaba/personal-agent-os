"""Shared test support for owner authentication (M9/ADR-0027).

Every M0-M8 test that drives the HTTP surface now has to authenticate, because
every endpoint it drives is protected. Two rules shaped this helper:

1. **Authenticate, never bypass.** There is no test-only backdoor, no "auth
   disabled in tests" setting and no dependency override of
   `require_owner_session`. A test client here holds a real session token,
   minted by the real service, verified by the real dependency on every call.
   If the authentication path breaks, the whole suite goes red — which is the
   only way a test suite can be evidence that the endpoint is protected.
2. **Keep the credential root out of the filesystem.** Tests use
   `InMemoryCredentialRoot`, so nothing writes a credential hash next to the
   source tree and no test can inherit another test's owner credential.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.identity.models import OwnerSession, SessionEvent
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime

IDENTITY_TABLES = [OwnerSession.__table__, SessionEvent.__table__]


def make_identity_engine() -> Engine:
    """In-memory SQLite with the identity tables created (StaticPool: the
    asyncio.to_thread workers must share the one connection)."""
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in IDENTITY_TABLES:
        table.create(engine)
    return engine


def install_identity(
    app: Any,
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    use_app_engine: bool = False,
) -> IdentityRuntime:
    """Give `app` a bootstrapped identity runtime and return it.

    `use_app_engine=True` keeps the runtime the app already built (integration
    tests: real PostgreSQL sessions) and only swaps the credential root, so the
    persisted-session path is genuinely exercised. Otherwise the runtime is
    rebuilt on an isolated SQLite engine (unit tests: fully offline).
    """
    if use_app_engine:
        runtime: IdentityRuntime = app.state.identity
        runtime.use_root(InMemoryCredentialRoot())
    else:
        runtime = IdentityRuntime(
            settings or Settings(_env_file=None),
            engine=engine or make_identity_engine(),
            root=InMemoryCredentialRoot(),
        )
        app.state.identity = runtime
    runtime.service.bootstrap()
    return runtime


def issue_token(
    runtime: IdentityRuntime,
    *,
    client_kind: str = "cli",
    label: str = "test-suite",
    device_id: uuid.UUID | None = None,
    scopes: list[str] | None = None,
    ttl_s: int | None = None,
) -> str:
    issued = runtime.service.issue_session(
        client_kind=client_kind,
        label=label,
        device_id=device_id,
        scopes=scopes,
        ttl_s=ttl_s,
    )
    return issued.token


def authenticate(
    app: Any,
    client: Any,
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    use_app_engine: bool = False,
    client_kind: str = "cli",
    device_id: uuid.UUID | None = None,
    scopes: list[str] | None = None,
) -> IdentityRuntime:
    """Bootstrap identity on `app` and put a live bearer token on `client`."""
    runtime = install_identity(
        app, settings=settings, engine=engine, use_app_engine=use_app_engine
    )
    token = issue_token(runtime, client_kind=client_kind, device_id=device_id, scopes=scopes)
    client.headers["Authorization"] = f"Bearer {token}"
    return runtime


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


__all__ = [
    "IDENTITY_TABLES",
    "authenticate",
    "bearer",
    "install_identity",
    "issue_token",
    "make_identity_engine",
]
