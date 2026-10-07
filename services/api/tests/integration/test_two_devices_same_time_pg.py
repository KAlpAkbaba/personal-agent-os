"""Two devices, the same write, the same moment - on the real application and real PostgreSQL.

The three races that reached production on 2026-10-06 (found by the test team, not by a
worker or the inspector), reproduced with ``fire_together``. Each is a strict xfail under its
defect id: the gate stays green while the defect stands, and the card that fixes it sees
XPASS turn red and removes the mark - this file is those cards' RED evidence.

Each names the route's read in ``meet_after`` (the SQL its check-then-write starts with): the
bare race lost a line in 4 of 5 runs and a watch past the cap in 2 of 5, and a strict xfail
that misses one run is a red gate. The claim is the rows and the status codes, never a
duration.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.conversations.models import (
    ConversationRow,
    ConversationSettingRow,
    PersonRow,
    SegmentRow,
)
from app.db import build_engine, build_session_factory
from app.household.models import HouseholdEvent, HouseholdItem
from app.watch.models import Watch, WatchReading
from app.watch.service import MAX_WATCHES
from tests.integration.concurrency import fire_together
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

#: The watch route checks its URL's address; the gate has no network.
URL = "https://www.home-assistant.io/blog/"


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.research.destination.resolve_hostname", lambda host: ["93.184.216.34"])


def _clear(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        for model in (
            SegmentRow,
            ConversationRow,
            PersonRow,
            ConversationSettingRow,
            HouseholdEvent,
            HouseholdItem,
            WatchReading,
            Watch,
        ):
            session.execute(delete(model))
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


def _count(sessions: sessionmaker[Session], model: type) -> int:
    with sessions() as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


def test_two_devices_add_a_line_to_one_conversation_and_both_lines_stay(factory, settings) -> None:
    client = owner_client(settings)
    started = client.post("/v1/conversations", json={"mode": "manual"})
    assert started.status_code == 201, started.text
    cid = started.json()["id"]
    n = 4

    responses = fire_together(
        client,
        "POST",
        f"/v1/conversations/{cid}/segments",
        json=lambda i: {"content": f"cihaz {i} satırı", "is_owner": True},
        n=n,
        meet_after=r"max\(conversation_segments\.seq\)",
    )

    codes = [r.status_code for r in responses]
    assert codes == [201] * n, codes
    assert _count(factory, SegmentRow) == n


def test_two_devices_say_the_same_new_item_ran_out_and_one_item_is_kept(factory, settings) -> None:
    client = owner_client(settings)

    responses = fire_together(
        client,
        "POST",
        "/v1/household/items",
        json={"name": "Süt", "level": "bitti"},
        n=4,
        meet_after=r"FROM household_items\s+WHERE household_items\.key\b",
    )

    codes = [r.status_code for r in responses]
    assert all(200 <= code < 300 for code in codes), codes
    assert _count(factory, HouseholdItem) == 1


@pytest.mark.xfail(strict=True, reason="watch-cap-race-21")
def test_the_watch_cap_holds_when_cap_plus_five_arrive_at_once(factory, settings) -> None:
    client = owner_client(settings)
    n = MAX_WATCHES + 5

    responses = fire_together(
        client,
        "POST",
        "/v1/watches",
        json=lambda i: {"url": URL, "condition": "changed", "label": f"nöbet {i}"},
        n=n,
        meet_after=r"count\(\*\).*FROM watches\b",
    )

    codes = sorted(r.status_code for r in responses)
    assert set(codes) <= {201, 422}, codes
    assert _count(factory, Watch) <= MAX_WATCHES, codes
