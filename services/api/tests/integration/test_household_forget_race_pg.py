"""'Unut' said from several devices at once, and 'bitti' racing 'unut', on the real database.

Test team round t-r10070152 (staging da3e26b9, tester-4, ev-stoku):

* DELETE /v1/household/items/{id} from 8 clients at once: 200=2 404=6 - two devices were told
  they forgot the item. ``forget_item`` read the row, then deleted it, unlocked; a second read
  of the same row before the first commit "forgot" it again.
* 16 and 32 requests, half POST /items 'bitti' and half DELETE on the same item: 500s. A
  'bitti' read the row, the forget deleted it, and the 'bitti' then updated a row that was gone
  (or wrote an event for it).

The window is held open, not hoped for: ``fire_together``'s ``meet_after`` waits the requests
at their read of ``household_items``. Wanted: rows and status codes, never a duration - exactly
one 200 among the forgets of one id, no 5xx, and an end state one of the orders explains.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.household.models import HouseholdEvent, HouseholdItem
from tests.integration.concurrency import Meeting, _meeting, fire_together
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
#: The read every write route of this race starts with: the item by id or by key.
ITEM_READ = r"FROM household_items\s+WHERE household_items\.(id|key) ="
#: Held together at the read; under the engine's pool (5 + 10 overflow) so a held request
#: never waits for a connection another held request keeps.
HELD = 8


def _alembic() -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    command.upgrade(_alembic(), "head")
    engine = build_engine(settings.database_url)
    sessions = build_session_factory(engine)

    def clear() -> None:
        with sessions() as session:
            session.execute(delete(HouseholdEvent))
            session.execute(delete(HouseholdItem))
            session.commit()

    clear()
    try:
        yield sessions
    finally:
        clear()
        engine.dispose()


def _rows(factory: sessionmaker[Session]) -> tuple[list[HouseholdItem], list[HouseholdEvent]]:
    with factory() as session:
        return (
            list(session.scalars(select(HouseholdItem))),
            list(session.scalars(select(HouseholdEvent))),
        )


def _mixed(client, requests: list[tuple[str, str, dict | None]], meet: str, held: int):
    """Like ``fire_together``, for requests of different methods: released on one barrier,
    the first ``held`` reads matching ``meet`` wait for each other."""
    app = client.app
    headers = {"Authorization": client.headers["Authorization"]}

    async def run() -> list[httpx.Response]:
        barrier = asyncio.Barrier(len(requests))
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers=headers
        ) as http:

            async def one(method: str, path: str, body: dict | None) -> httpx.Response:
                await barrier.wait()
                return await http.request(method, path, json=body)

            return await asyncio.wait_for(asyncio.gather(*(one(*r) for r in requests)), timeout=120)

    with _meeting(meet, held, 3.0) as meeting:
        answers = asyncio.run(run())
    assert isinstance(meeting, Meeting) and meeting.arrived > 0, "the window was not held"
    return answers


def test_eight_devices_forget_one_item_once(factory, settings) -> None:
    client = owner_client(settings)
    made = client.post("/v1/household/list", json={"name": "süt"})
    assert made.status_code == 200, made.text
    item = made.json()["item"]["id"]
    client.post("/v1/household/items", json={"name": "süt", "level": "bitti"})

    met: list[Meeting] = []
    answers = fire_together(
        client,
        "DELETE",
        f"/v1/household/items/{item}",
        n=HELD,
        meet_after=ITEM_READ,
        on_meeting=met.append,
    )

    codes = sorted(a.status_code for a in answers)
    assert codes == [200] + [404] * (HELD - 1), codes
    assert met and met[0].arrived == HELD
    items, events = _rows(factory)
    assert (items, events) == ([], [])


@pytest.mark.parametrize("n", [16, 32])
def test_bitti_racing_unut_ends_in_one_explained_state(factory, settings, n) -> None:
    client = owner_client(settings)
    made = client.post("/v1/household/list", json={"name": "süt"})
    original = made.json()["item"]["id"]
    forget = ("DELETE", f"/v1/household/items/{original}", None)
    out = ("POST", "/v1/household/items", {"name": "süt", "level": "bitti"})
    requests = [forget if i % 2 else out for i in range(n)]

    answers = _mixed(client, requests, ITEM_READ, HELD)

    forgets = [a.status_code for a, r in zip(answers, requests, strict=True) if r is forget]
    outs = [a for a, r in zip(answers, requests, strict=True) if r is out]
    assert all(a.status_code < 500 for a in answers), [a.text[:120] for a in answers][:3]
    assert sorted(set(forgets)) <= [200, 404] and forgets.count(200) == 1, forgets
    assert [a.status_code for a in outs] == [200] * len(outs)

    items, events = _rows(factory)
    assert str(original) not in {str(i.id) for i in items}  # the one forget really forgot it
    assert len(items) <= 1
    for row in items:  # a 'bitti' after the forget said it again: süt, out, on the list
        assert (row.name, row.level, row.on_list) == ("süt", "bitti", True)
        assert str(row.id) in {a.json()["item"]["id"] for a in outs}
    assert {e.item_id for e in events} <= {i.id for i in items}
