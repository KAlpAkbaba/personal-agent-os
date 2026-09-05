"""The unit suite must not be able to reach real infrastructure - and an unauthenticated
request must be refused in milliseconds, not after a network timeout.

Both halves of the 2026-09-06 suite stall (see ``tests/unit/conftest.py``), pinned as tests
so the stall cannot come back quietly: it looked exactly like a deadlock for two days, and
the only evidence that it was not was CPU time.
"""

from __future__ import annotations

import time

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.identity_support import install_identity
from tests.unit.conftest import UnitTestReachedRealDatabase


def test_a_real_database_connection_is_refused_instantly_and_names_itself() -> None:
    started = time.perf_counter()
    with pytest.raises(UnitTestReachedRealDatabase, match="host=127.0.0.1"):
        psycopg.Connection.connect("host=127.0.0.1 port=15432 dbname=pagentos user=pagentos")
    assert time.perf_counter() - started < 0.5, "the guard must not dial anything"


def test_an_unauthenticated_request_is_refused_in_milliseconds() -> None:
    """The exact shape of the stall: create_app(), no token, a protected route.

    With the isolated identity runtime installed the refusal is audited to SQLite; without
    it the audit would reach the real database - which the conftest guard now turns into an
    instant, named error rather than a silent wait. Either way this must be fast.
    """
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    client = TestClient(app)

    started = time.perf_counter()
    response = client.get("/v1/ledger/events")
    elapsed = time.perf_counter() - started

    assert response.status_code == 401
    assert elapsed < 2.0, f"an unauthenticated refusal took {elapsed:.1f}s; something is dialling"


def test_create_app_alone_does_not_connect_anywhere() -> None:
    """Building the app must not open a connection: every runtime is lazy. If a future
    change makes one eager, this fails with the guard's message instead of a suite stall."""
    settings = Settings(_env_file=None)
    started = time.perf_counter()
    create_app(settings)
    assert time.perf_counter() - started < 5.0
