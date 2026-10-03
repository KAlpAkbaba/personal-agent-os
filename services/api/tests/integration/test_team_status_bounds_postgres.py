"""The live status' bounds on the REAL database (team-status-bounds, ADR-0214 addendum 4's shape).

``team_state.updated_at`` is VARCHAR(32): SQLite holds 46 characters in it, PostgreSQL answers
``value too long for type character varying(32)`` - a 500. The route now refuses a longer
timestamp with a 422 before the store is reached, and ``used_pct: 1e999`` with a 422 instead of
a 500. Each refusal is checked against the row itself, read through a fresh session.

The status row is a singleton and the dev database may hold a real one: it is put back when a
test ends.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.main import create_app
from app.team.models import TeamStateRow
from app.team.store import DbStore
from tests.integration.conftest import attach_owner

pytestmark = pytest.mark.integration

STATUS = "/v1/team/queue/status"
KIND = "status"


def _status(updated_at: str = "2026-10-03T03:00:00Z") -> dict[str, Any]:
    return {
        "cycle_id": "d20261003",
        "machine": "GMKADIRAKBABA-OFFICE-PC",
        "pid": 46484,
        "started_at": "2026-10-03T02:00:00Z",
        "runs": [
            {
                "task": "team-status-bounds",
                "role": "worker",
                "started_at": "2026-10-03T02:10:00Z",
                "model": "claude-opus-5-5",
            }
        ],
        "estimated_usd": 3.61,
        "usage_limit": {"state": "ok", "resets_at": None},
        "limits": {
            "fable": {"state": "ok", "resets_at": None, "used_pct": 37.5},
            "all": {"state": "ok", "resets_at": None, "used_pct": None},
            "fallback": True,
            "lowered": [],
        },
        "updated_at": updated_at,
    }


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(Settings().database_url)
    made = build_session_factory(engine)
    with made() as session:
        found = session.execute(select(TeamStateRow).where(TeamStateRow.kind == KIND)).scalars()
        kept = [(row.kind, row.key, row.doc, row.updated_at) for row in found]

    def clear() -> None:
        with made() as session:
            session.execute(delete(TeamStateRow).where(TeamStateRow.kind == KIND))
            session.commit()

    clear()
    try:
        yield made
    finally:
        clear()
        with made() as session:
            for kind, key, doc, updated_at in kept:
                session.add(TeamStateRow(kind=kind, key=key, doc=doc, updated_at=updated_at))
            session.commit()
        engine.dispose()


@pytest.fixture()
def client(factory) -> TestClient:
    settings = Settings()
    app = create_app(settings)
    app.state.team_store = DbStore(factory)
    made = TestClient(app)
    attach_owner(app, made, settings)
    return made


def _row(factory: sessionmaker[Session]) -> tuple[str, dict[str, Any]] | None:
    """The status row as PostgreSQL holds it, through a session of its own."""
    with factory() as session:
        row = session.execute(
            select(TeamStateRow).where(TeamStateRow.kind == KIND)
        ).scalar_one_or_none()
        return None if row is None else (row.updated_at, row.doc)


def test_a_33_character_updated_at_is_a_422_and_the_row_is_unchanged(client, factory) -> None:
    first = _status()
    assert client.put(STATUS, json=first).status_code == 200
    before = _row(factory)
    assert before == ("2026-10-03T03:00:00Z", first)

    refused = client.put(STATUS, json=_status("2" * 33))
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "status_stamp_too_long"
    assert _row(factory) == before
    # the inspector's 46 characters: a 500 on PostgreSQL before this task
    assert client.put(STATUS, json=_status("2" * 46)).status_code == 422
    assert _row(factory) == before


def test_a_32_character_updated_at_is_stored_and_read_back(client, factory) -> None:
    stamp = "2026-10-03T03:00:00.123456+00:00"
    assert len(stamp) == 32
    doc = _status(stamp)
    put = client.put(STATUS, json=doc)
    assert put.status_code == 200, put.text
    assert _row(factory) == (stamp, doc)
    assert client.get(STATUS).json() == doc


def test_used_pct_1e999_is_a_422_not_a_500_and_nothing_is_written(client, factory) -> None:
    first = _status()
    assert client.put(STATUS, json=first).status_code == 200
    before = _row(factory)
    text = json.dumps(_status("2026-10-03T03:05:00Z")).replace(
        '"used_pct": 37.5', '"used_pct": 1e999'
    )
    assert "1e999" in text
    refused = client.put(STATUS, content=text, headers={"Content-Type": "application/json"})
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "status_used_pct_invalid"
    assert _row(factory) == before
