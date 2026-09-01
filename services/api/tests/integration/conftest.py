"""Integration test fixtures. These tests require the local compose stack:

    powershell -File scripts/dev-up.ps1   (from repo root)

Every test in this package is marked `integration`.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient

from app.config import Settings
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.main import create_app

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]


_identity: dict[str, object] = {}


def shared_identity(settings: Settings) -> IdentityRuntime:
    """One owner, one identity runtime for the whole integration suite.

    Sessions live in real PostgreSQL `owner_sessions` rows, so this is the half
    of the authentication path the unit tests cannot cover. The runtime is a
    module-level singleton for a mundane reason: the integration suite builds a
    dozen apps, and one extra connection pool per app exhausts the dev
    database's connection limit.

    Only the credential root is swapped for an in-memory one, so a test run
    never writes an owner credential hash onto the developer's disk — and the
    owner credential survives `test_migrations`, which round-trips the schema
    (drop + recreate) and takes `owner_sessions` with it.
    """
    runtime = _identity.get("runtime")
    if runtime is None:
        runtime = IdentityRuntime(settings, root=InMemoryCredentialRoot())
        runtime.service.bootstrap()
        _identity["runtime"] = runtime
    return runtime  # type: ignore[return-value]


def attach_owner(app, client, settings: Settings | None = None) -> IdentityRuntime:
    """Wire the shared identity runtime into `app` and authenticate `client`.

    A fresh session per client, deliberately: it is one INSERT, it matches what
    a real client does on sign-in, and it survives the schema round-trip in
    `test_migrations` that would otherwise delete a suite-wide session row.
    """
    runtime = shared_identity(settings or Settings())
    app.state.identity = runtime
    issued = runtime.service.issue_session(client_kind="cli", label="integration-suite")
    client.headers["Authorization"] = f"Bearer {issued.token}"
    return runtime


def owner_client(settings: Settings) -> TestClient:
    """App + TestClient holding a real owner session (M9/ADR-0027)."""
    app = create_app(settings)
    client = TestClient(app)
    attach_owner(app, client, settings)
    return client


def pytest_collection_modifyitems(items) -> None:
    for item in items:
        item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """Ensure the schema is at head before any integration test runs."""
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(cfg, "head")
