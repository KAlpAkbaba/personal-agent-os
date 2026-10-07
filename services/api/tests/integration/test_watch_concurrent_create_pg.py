"""Watches made at the same moment, on the real application and real PostgreSQL.

The test team's round t-w10070808 (staging b1f8ef94): POST /v1/watches in a 2/4/8/16 ladder,
the 16 step 4 x 201 and 12 x 422. The ladder never cleared between steps (2 watches from the
steps + 2 + 4 + 8 = 16 before it), so those 12 were the cap's Turkish refusal - but the cap's
strict xfail 'watch-cap-race-21' in ``test_two_devices_same_time_pg.py`` never turned XPASS
after the advisory lock went in: with the count behind the lock, ``meet_after`` held every
request's count for its full wait one after another (25 x 3 s past the 60 s guard).

Here: 16 at once under the cap -> 16 x 201 and 16 rows; cap + 5 at once -> exactly cap rows and
5 Turkish refusals; never a 5xx. Each request's count meets the others' (the window held), and
the run ends well inside its hang guard. The claim is the rows and the status codes.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.watch.models import Watch, WatchReading
from app.watch.service import MAX_WATCHES
from tests.integration.concurrency import Meeting, fire_together
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

#: The watch route checks its URL's address; the gate has no network.
URL = "https://www.home-assistant.io/blog/"
#: The read the strict xfail holds open; the same pattern, so this file is its witness.
COUNT_READ = r"count\(\*\).*FROM watches\b"
#: Inside fire_together's 60 s default: green took 4-19 s here, the serialised wait 46-73 s.
HANG_GUARD_S = 40.0


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.research.destination.resolve_hostname", lambda host: ["93.184.216.34"])


def _clear(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        session.execute(delete(WatchReading))
        session.execute(delete(Watch))
        session.commit()


@pytest.fixture()
def factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    engine = build_engine(settings.database_url)
    sessions = build_session_factory(engine)
    _clear(sessions)
    try:
        yield sessions
    finally:
        _clear(sessions)
        engine.dispose()


def _rows(sessions: sessionmaker[Session]) -> int:
    with sessions() as session:
        return session.execute(select(func.count()).select_from(Watch)).scalar_one()


def _fire(client, n: int, meetings: list[Meeting]):  # noqa: ANN001, ANN202
    return fire_together(
        client,
        "POST",
        "/v1/watches",
        json=lambda i: {"url": URL, "condition": "changed", "label": f"eşzamanlı nöbet {i}"},
        n=n,
        meet_after=COUNT_READ,
        timeout_s=HANG_GUARD_S,
        on_meeting=meetings.append,
    )


def test_sixteen_watches_at_once_under_the_cap_are_all_made(factory, settings) -> None:
    client = owner_client(settings)
    meetings: list[Meeting] = []
    n = 16

    responses = _fire(client, n, meetings)

    codes = [r.status_code for r in responses]
    assert codes == [201] * n, [(r.status_code, r.text[:120]) for r in responses]
    assert _rows(factory) == n
    # The window was held: the first count let go had others' counts beside it (one at a
    # time behind a lock it would see only itself, and wait its full meeting each).
    assert meetings[0].released_seeing[0] > 1, meetings[0].released_seeing


def test_cap_plus_five_at_once_make_exactly_the_cap_and_five_turkish_refusals(
    factory, settings
) -> None:
    client = owner_client(settings)
    meetings: list[Meeting] = []
    n = MAX_WATCHES + 5

    responses = _fire(client, n, meetings)

    codes = sorted(r.status_code for r in responses)
    assert codes == [201] * MAX_WATCHES + [422] * 5, codes
    refusals = [r.json()["detail"] for r in responses if r.status_code == 422]
    assert all(f"En çok {MAX_WATCHES} nöbet" in d["message"] for d in refusals), refusals
    assert _rows(factory) == MAX_WATCHES
    assert meetings[0].released_seeing[0] > 1, meetings[0].released_seeing


def test_a_full_cap_refuses_a_burst_in_turkish_and_keeps_every_row(factory, settings) -> None:
    client = owner_client(settings)
    for i in range(MAX_WATCHES):
        made = client.post(
            "/v1/watches", json={"url": URL, "condition": "changed", "label": f"n{i}"}
        )
        assert made.status_code == 201, made.text
    meetings: list[Meeting] = []

    responses = _fire(client, 16, meetings)

    assert [r.status_code for r in responses] == [422] * 16
    assert all("En çok" in r.json()["detail"]["message"] for r in responses)
    assert _rows(factory) == MAX_WATCHES
