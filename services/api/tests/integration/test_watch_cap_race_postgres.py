"""The 20-watch cap under concurrency, on the REAL database.

The test team's finding (round t-manual-20261006d, staging 72884b71, tester-4): 32 POST
/v1/watches at once from empty made 21 watches. ``create_watch`` counted, then inserted; two
transactions that both counted 19 both inserted. On SQLite the writes are serial, so only
PostgreSQL can show it.

The window between the count and the insert is made wide on purpose: a cursor hook sleeps
after every ``count(*)`` over ``watches``, so without a guard every request reads the same
count and the cap fails every time, not one run in a hundred. With the guard the requests
take their turns and the run is about 32 x the sleep.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, delete, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.watch import service
from app.watch.models import Watch, WatchReading

pytestmark = pytest.mark.integration

REQUESTS = 32
WINDOW_S = 0.2
NOON = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
REFUSAL = f"En çok {service.MAX_WATCHES} nöbet tutulabilir; önce birini kaldır."


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.research.destination.resolve_hostname", lambda host: ["93.184.216.34"])


@pytest.fixture()
def engine() -> Iterator[Engine]:
    # The schema is at head already (conftest's migrated_database). One connection per
    # request, so no request waits for the pool instead of the database.
    eng = create_engine(Settings().database_url, pool_size=REQUESTS + 2, max_overflow=0)

    def clear() -> None:
        with eng.begin() as conn:
            conn.execute(delete(WatchReading))
            conn.execute(delete(Watch))

    clear()
    try:
        yield eng
    finally:
        clear()
        eng.dispose()


def _widen_the_window(eng: Engine) -> None:
    @event.listens_for(eng, "after_cursor_execute")
    def after(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        flat = " ".join(statement.lower().split())
        if flat.startswith("select count(*)") and "from watches" in flat:
            time.sleep(WINDOW_S)


def test_32_concurrent_creates_from_empty_make_exactly_20(engine: Engine) -> None:
    _widen_the_window(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    start = threading.Barrier(REQUESTS)

    def one(n: int) -> str:
        start.wait(timeout=30)
        with sessions() as session:  # as the route does: one session, commit on success
            try:
                service.create_watch(
                    session,
                    url=f"https://shop.example.com/urun/{n}",
                    condition="changed",
                    label=f"Nöbet {n}",
                    now=NOON,
                )
            except service.WatchRefused as refused:
                return refused.reason_tr
            session.commit()
            return "ok"

    with ThreadPoolExecutor(max_workers=REQUESTS) as pool:
        answers = list(pool.map(one, range(REQUESTS), timeout=120))

    with sessions() as session:
        rows = session.execute(select(func.count()).select_from(Watch)).scalar_one()
    refusals = [a for a in answers if a != "ok"]
    assert rows == service.MAX_WATCHES, answers
    assert answers.count("ok") == service.MAX_WATCHES
    assert refusals == [REFUSAL] * (REQUESTS - service.MAX_WATCHES)
