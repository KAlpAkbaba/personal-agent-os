"""Integration test fixtures. These tests require the local compose stack:

    powershell -File scripts/dev-up.ps1   (from repo root)

Every test in this package is marked `integration`.
"""

import socket
import time
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.db import build_engine
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
    """Mark the tests in THIS directory as integration tests.

    Scoped to this conftest's own directory on purpose. Pytest hands a
    subdirectory conftest's hook the WHOLE session's collected items, not just
    the ones beneath it, so an unscoped loop marks every unit test as
    ``integration`` too. A repo-root ``pytest -m "not integration"`` then
    deselects all 1418 tests and exits 5 having asserted nothing at all -- a
    green-looking run that tests nothing, which is worse than a red one. The
    local gate never saw it because it scopes by path (``pytest tests/unit``);
    the first real CI run did.
    """
    here = Path(__file__).parent.resolve()
    for item in items:
        item_path = Path(str(getattr(item, "fspath", ""))).resolve()
        if here == item_path or here in item_path.parents:
            item.add_marker(pytest.mark.integration)


#: Arbitrary fixed key; any value works as long as every runner uses the same one.
_SUITE_LOCK_KEY = 0x5041_474E  # "PAGN"
_SUITE_LOCK_TIMEOUT_S = 900.0


@pytest.fixture(scope="session", autouse=True)
def exclusive_database() -> Iterator[None]:
    """Serialize concurrent runs of this suite against the one dev database.

    These tests share a database *and* a schema — there is one `tasks` table and
    one artifact-ready announcer sweeping it. Two pytest processes running at
    once (two agents, or a gate running beside a verification run) therefore
    interleave: one process's announcer drains the other's READY task, and the
    victim sees a sweep that returns 0 or an inbox missing its own notification.
    Every such failure observed in this project has been that, not a defect in
    the code under test — but it looks exactly like one, which is worse than a
    slow suite.

    A PostgreSQL session-level advisory lock makes the second runner wait rather
    than corrupt the first one's assumptions. It is polled rather than blocking
    so a stuck runner produces a clear message instead of a hang.
    """
    engine = build_engine(Settings().database_url)
    connection = engine.connect()
    deadline = time.monotonic() + _SUITE_LOCK_TIMEOUT_S
    while True:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": _SUITE_LOCK_KEY}
        ).scalar()
        connection.commit()
        if acquired:
            break
        if time.monotonic() > deadline:
            connection.close()
            engine.dispose()
            pytest.fail(
                "another integration run has held the suite lock for "
                f"{_SUITE_LOCK_TIMEOUT_S:.0f}s; stop it before running this one"
            )
        time.sleep(1.0)
    try:
        yield
    finally:
        connection.execute(
            text("SELECT pg_advisory_unlock(:key)"), {"key": _SUITE_LOCK_KEY}
        )
        connection.commit()
        connection.close()
        engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def no_live_api(exclusive_database: None) -> None:
    """Refuse to run beside a live API process on this database.

    The advisory lock above serialises pytest runs against each other. It cannot stop a
    developer API server: `scripts/dev-broker.ps1` runs the artifact-ready announcer, which
    sweeps `tasks` every five seconds and stamps rows these tests are about to assert on.
    That produced failures that looked exactly like product defects and cost real time to
    diagnose twice, so it is now detected rather than rediscovered.

    A warning, not an error: a developer may knowingly want both, and the failure is loud
    enough to recognise once it is named.
    """
    settings = Settings()
    port = 8001
    try:
        port = int(str(settings.api_port))  # type: ignore[attr-defined]
    except Exception:
        pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            warnings.warn(
                f"an API is listening on 127.0.0.1:{port}. Its artifact-ready announcer sweeps "
                "the same `tasks` table these tests use, and can consume rows a test is waiting "
                "for. Stop it with `scripts\\dev-broker.ps1 -Stop` if integration tests fail in "
                "ways that make no sense.",
                stacklevel=1,
            )


@pytest.fixture(scope="session", autouse=True)
def migrated_database(exclusive_database: None, no_live_api: None) -> None:
    """Ensure the schema is at head before any integration test runs."""
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(cfg, "head")
