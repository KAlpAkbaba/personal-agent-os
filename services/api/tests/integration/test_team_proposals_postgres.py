"""An idea's text in ``team_state`` on the REAL database, not on SQLite (ADR-0214 addendum 4).

A proposal is a row ``kind='proposal'``, ``key`` = its file name. ``key`` is VARCHAR(80):
SQLite would keep an 81-character name and PostgreSQL refuses it, so the store must refuse it
first (and never cut it - a cut name is another proposal's key). These tests take put, read,
replace, the 80/81 boundary and the Onay Merkezi's listing to the dev stack's PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.main import create_app
from app.team.models import TeamStateRow
from app.team.store import PROPOSAL_MAX_CHARS, DbStore, Invalid
from tests.integration.conftest import attach_owner

pytestmark = pytest.mark.integration

#: Every row this file writes carries the prefix, and only those are deleted: the dev
#: database's own queue (if it has one) is not this test's to touch.
PREFIX = "it-proposals-"
NAME = f"{PREFIX}ev-home-assistant.md"
TASK_ID = f"{PREFIX}idea"
TEXT = "# Ev otomasyonu\n\nIşığı sesle aç: ığüşöç İĞÜŞÖÇ.\n"
KEY_WIDTH = TeamStateRow.__table__.c.key.type.length


def _name_of_length(length: int) -> str:
    return PREFIX + "a" * (length - len(PREFIX) - 3) + ".md"


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(Settings().database_url)
    made = build_session_factory(engine)

    def clear() -> None:
        with made() as session:
            session.execute(delete(TeamStateRow).where(TeamStateRow.key.like(f"{PREFIX}%")))
            session.commit()

    clear()
    try:
        yield made
    finally:
        clear()
        engine.dispose()


@pytest.fixture()
def store(factory) -> DbStore:
    return DbStore(factory)


def _keys(factory) -> list[str]:
    with factory() as session:
        return sorted(
            session.execute(
                select(TeamStateRow.key).where(
                    TeamStateRow.kind == "proposal", TeamStateRow.key.like(f"{PREFIX}%")
                )
            ).scalars()
        )


def test_a_proposal_is_put_read_and_replaced_on_postgres(store: DbStore, factory) -> None:
    assert store.read_proposal(NAME) is None
    store.put_proposal(NAME, TEXT)
    assert store.read_proposal(NAME) == TEXT
    store.put_proposal(NAME, "ikinci\n")
    assert store.read_proposal(NAME) == "ikinci\n"
    assert _keys(factory) == [NAME]


def test_the_largest_text_under_the_longest_name_is_kept_whole_on_postgres(
    store: DbStore, factory
) -> None:
    longest = _name_of_length(KEY_WIDTH)
    assert len(longest) == 80
    whole = "ş" * PROPOSAL_MAX_CHARS
    store.put_proposal(longest, whole)
    assert store.read_proposal(longest) == whole
    assert _keys(factory) == [longest]


def test_a_name_one_character_longer_than_the_key_column_is_refused_not_cut_on_postgres(
    store: DbStore, factory
) -> None:
    too_long = _name_of_length(KEY_WIDTH + 1)
    assert len(too_long) == 81
    with pytest.raises(Invalid):
        store.put_proposal(too_long, TEXT)
    assert _keys(factory) == []  # neither the name nor its first 80 characters
    assert store.read_proposal(too_long) is None
    assert store.read_proposal(too_long[:KEY_WIDTH]) is None


def test_postgres_itself_refuses_the_row_the_store_refuses(factory) -> None:
    """Why the rule is the store's: this is the write SQLite would have kept."""
    with factory() as session:
        session.add(
            TeamStateRow(
                kind="proposal",
                key=_name_of_length(KEY_WIDTH + 1),
                doc={"text": "x"},
                updated_at="2026-10-01T12:00:00Z",
            )
        )
        with pytest.raises(DBAPIError, match="character varying\\(80\\)"):
            session.commit()
        session.rollback()
    assert _keys(factory) == []


def _task(**extra: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": TASK_ID,
        "title": "Fikir: ev otomasyonu",
        "roadmap_row": "",
        "state": "awaiting_owner",
        "area": [],
        "branch": "",
        "worktree": "",
        "assignee": "",
        "reports": [],
        "budget": {"max_usd": 5},
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
        "proposal": f"team/proposals/{NAME}",
    }
    task.update(extra)
    return task


def test_a_posted_proposal_is_what_the_onay_merkezi_lists_from_postgres(
    store: DbStore, factory, tmp_path
) -> None:
    """The route, the owner session and the listing over the real table - with a ``team/``
    that has no proposals folder, as on the Cloud Core."""
    settings = Settings()
    app = create_app(settings)
    app.state.team_store = store
    app.state.team_root = tmp_path / "team"
    client = TestClient(app)
    assert (
        client.post("/v1/team/queue/proposals", json={"name": NAME, "text": TEXT}).status_code
        == 401
    )
    attach_owner(app, client, settings)
    store.put_task(_task(), None)

    def listed() -> dict[str, Any]:
        body = client.get("/v1/team/approvals").json()
        return next(a for a in body["approvals"] if a["task_id"] == TASK_ID)

    assert listed()["proposal_text"] is None
    posted = client.post("/v1/team/queue/proposals", json={"name": NAME, "text": TEXT})
    assert posted.status_code == 200, posted.text
    assert posted.json() == {"ok": True}
    assert listed()["proposal_text"] == TEXT

    again = client.post("/v1/team/queue/proposals", json={"name": NAME, "text": "ikinci\n"})
    assert again.status_code == 200, again.text
    assert listed()["proposal_text"] == "ikinci\n"

    refused = client.post(
        "/v1/team/queue/proposals", json={"name": _name_of_length(KEY_WIDTH + 1), "text": TEXT}
    )
    assert refused.status_code == 422, refused.text
    assert _keys(factory) == [NAME]
