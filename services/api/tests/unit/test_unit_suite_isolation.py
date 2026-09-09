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
    with pytest.raises(UnitTestReachedRealDatabase):
        psycopg.connect("host=127.0.0.1 port=15432 dbname=pagentos user=pagentos")
    assert time.perf_counter() - started < 0.5, "the guard must not dial anything"


def test_the_guard_covers_the_path_sqlalchemy_actually_takes() -> None:
    """The one that matters. SQLAlchemy calls the MODULE's ``connect``; the first guard
    patched only the class and every engine sailed straight past it."""
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError

    engine = create_engine("postgresql+psycopg://pagentos:x@127.0.0.1:15432/pagentos")
    started = time.perf_counter()
    with pytest.raises((UnitTestReachedRealDatabase, DBAPIError)) as caught:
        engine.connect()
    assert "unit test tried to connect to a real database" in str(caught.value)
    assert time.perf_counter() - started < 0.5, "an engine.connect() must not dial anything"
    engine.dispose()


def test_the_app_lifespan_does_not_dial_the_real_database() -> None:
    """The exact shape of the second stall: `with TestClient(app)` runs the lifespan - the
    broker's orphan cleanup and sweeper, the artifact bucket check, the announcer sweep and
    the ledger backfill - every one of them best-effort and silent on a dead database."""
    settings = Settings(_env_file=None)
    app = create_app(settings)
    started = time.perf_counter()
    with TestClient(app) as client:
        response = client.get("/v1/system/health")
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    # The real dependency checks run here (nothing is mocked), so the answer is honestly
    # "degraded" - and it must arrive fast: every dial is refused at the client boundary,
    # never waited out.
    assert response.json()["checks"]["db"]["status"] == "fail"
    assert elapsed < 8.0, f"the lifespan + health took {elapsed:.1f}s; something is dialling"


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

    # TWO requests, and the SECOND one is the measurement.
    #
    # The claim is "nothing is dialling", and it was being tested with an absolute stopwatch:
    # one request, under 2 s. That measures the machine as much as the code, and on a CI runner
    # that had just finished 7,892 tests it read 7.5 s and failed a suite in which nothing was
    # dialling at all (run 34377649255). The first request pays whatever this process has not
    # paid yet - imports, the app's first route match, SQLite touching disk - and that cost is
    # not what the test is about.
    #
    # A dial, on the other hand, is paid EVERY time: a refusal that reaches the network blocks
    # on the same socket on the second request as on the first. So the second request is where
    # the claim lives, and the first keeps a generous backstop for a dial made once at startup
    # (test_create_app_alone_does_not_connect_anywhere covers that boundary directly).
    started = time.perf_counter()
    first = client.get("/v1/ledger/events")
    warm_up = time.perf_counter() - started

    started = time.perf_counter()
    response = client.get("/v1/ledger/events")
    elapsed = time.perf_counter() - started

    assert first.status_code == 401
    assert response.status_code == 401
    assert elapsed < 2.0, (
        f"a warm unauthenticated refusal took {elapsed:.1f}s (first was {warm_up:.1f}s); "
        "something is dialling on every request"
    )
    assert warm_up < 20.0, (
        f"the first unauthenticated refusal took {warm_up:.1f}s; something is dialling at start-up"
    )


def test_create_app_alone_does_not_connect_anywhere() -> None:
    """Building the app must not open a connection: every runtime is lazy. If a future
    change makes one eager, this fails with the guard's message instead of a suite stall."""
    settings = Settings(_env_file=None)
    started = time.perf_counter()
    create_app(settings)
    assert time.perf_counter() - started < 5.0
