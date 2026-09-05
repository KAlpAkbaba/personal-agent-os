"""The unit suite never reaches real infrastructure - enforced, not assumed.

Why this exists (2026-09-06): the full unit suite "stalled" at roughly the same point for
two days. It was not a hang. ``create_app()`` builds ``app.state.identity`` on the real
``database_url`` (the compose Postgres on 127.0.0.1:15432), and the fixtures that drive an
UNAUTHENTICATED request never replaced it - so the refusal audit
(``IdentityService._reject`` -> ``session.commit()``) opened a psycopg connection to a port
nothing was listening on. On this machine even a closed loopback port takes ~2 s to refuse,
psycopg waited far longer, and every ``*_endpoints_require_owner_session`` test paid it: zero
CPU, minutes of silence, then a pass. The suite looked deadlocked and was merely dialling.

Two guards, each of which alone would have surfaced that in seconds:

1. **A real database connection from a unit test is an error, loudly and instantly.**
   ``psycopg.Connection.connect`` is replaced for the whole unit session with one that
   raises, naming the DSN's host. Unit tests use SQLite in memory; the integration suite
   under ``tests/integration`` has its own conftest and is not affected.
2. The fixtures that build an app for unauthenticated requests now install the same isolated
   SQLite identity runtime the authenticated ones do (``tests.identity_support``), so a
   refusal is genuinely audited - to SQLite, in microseconds - rather than swallowed after a
   network timeout.

``test_unit_suite_isolation.py`` pins both.
"""

from __future__ import annotations

import re
from typing import Any

import botocore.client
import botocore.exceptions
import psycopg
import pytest
import redis.connection
import redis.exceptions


class UnitTestReachedRealDatabase(RuntimeError):
    """A unit test tried to open a real Postgres connection."""


def _refuse_real_connection(*args: Any, **kwargs: Any) -> Any:
    conninfo = str(args[1] if len(args) > 1 else kwargs.get("conninfo", ""))
    host = kwargs.get("host") or (re.search(r"host=(\S+)", conninfo) or [None, "?"])[1]
    raise UnitTestReachedRealDatabase(
        f"a unit test tried to connect to a real database (host={host}). Unit tests run on "
        "SQLite in memory; if this app needs identity, install it with "
        "tests.identity_support.install_identity(app, ...) instead of leaving create_app()'s "
        "real-database runtime in place."
    )


def _refuse_object_store_call(self: Any, operation_name: str, api_params: Any) -> Any:
    raise botocore.exceptions.EndpointConnectionError(
        endpoint_url=f"<unit test refused object-store call {operation_name}; nothing was dialled>"
    )


def _refuse_redis_connect(self: Any, *args: Any, **kwargs: Any) -> Any:
    raise redis.exceptions.ConnectionError(
        "unit test refused a real Redis connection; nothing was dialled"
    )


@pytest.fixture(autouse=True, scope="session")
def _no_real_database_in_unit_tests() -> Any:
    """Session-wide: every psycopg connect attempt fails immediately and says why.

    The MODULE attribute is the one that matters. ``psycopg.connect`` is
    ``Connection.connect`` bound at import time, and SQLAlchemy's dialect calls
    ``loaded_dbapi.connect(...)`` - the module attribute - so patching only the class's
    ``connect`` leaves every SQLAlchemy engine dialling the real port. The first version
    of this guard did exactly that, and the full suite proved it by stalling in three
    lifespan threads at once (broker sweeper, ledger backfill, announcer sweep) with the
    guard "in place". Patch every name a caller could reach.
    """
    patch = pytest.MonkeyPatch()
    patch.setattr(psycopg, "connect", _refuse_real_connection)
    patch.setattr(psycopg.Connection, "connect", classmethod(_refuse_real_connection))
    patch.setattr(psycopg.AsyncConnection, "connect", classmethod(_refuse_real_connection))
    # The object store and Redis, same rule. boto retries a dead endpoint with backoff
    # (~7 s per call on this machine) and redis-py waits its socket timeout; both are
    # "best-effort, swallowed" in the app's start-up paths, which is exactly why they cost
    # minutes without ever going red. Raising at the API-call boundary skips the retries.
    patch.setattr(botocore.client.BaseClient, "_make_api_call", _refuse_object_store_call)
    patch.setattr(redis.connection.AbstractConnection, "connect", _refuse_redis_connect)
    try:
        yield
    finally:
        patch.undo()
