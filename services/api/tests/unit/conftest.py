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

import psycopg
import pytest


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


@pytest.fixture(autouse=True, scope="session")
def _no_real_database_in_unit_tests() -> Any:
    """Session-wide: every psycopg connect attempt fails immediately and says why."""
    patch = pytest.MonkeyPatch()
    patch.setattr(psycopg.Connection, "connect", classmethod(_refuse_real_connection))
    try:
        yield
    finally:
        patch.undo()
