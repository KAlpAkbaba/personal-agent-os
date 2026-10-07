"""A test round's proof in ``team_state`` on the REAL database, not on SQLite (ADR-0214 addendum 4).

proof-from-test-rounds-and-trials: a round's proof is a row ``kind='proof'``, ``key`` = the
round id, ``doc`` = the round with its per-JARVIS-row passed / failed scenarios. SQLite has no
JSONB and keeps a U+0000 that PostgreSQL refuses, so put, replace, read, the refusals and the
route -> Ofis strip path are taken to the dev stack's PostgreSQL.
"""

from __future__ import annotations

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
from app.team.store import KIND_PROOF, DbStore, Invalid
from tests.integration.conftest import attach_owner

pytestmark = pytest.mark.integration

#: Every row this file writes carries the prefix, and only those are deleted: the dev
#: database's own rounds (if it has any) are not this test's to touch.
PREFIX = "it-proof-"
SHA = "a5e68d92d9271ececec51da01b713e43a394e28c"
ROW = "Repairs and improves itself"
NUL = chr(0)


def _round(name: str, at: str, passed: int, failed: int, row: str = ROW) -> dict[str, Any]:
    return {
        "round": f"{PREFIX}{name}",
        "staging_sha": SHA,
        "at": at,
        "rows": [{"row": row, "passed": passed, "failed": failed, "families": ["öz-onarım"]}],
    }


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(Settings().database_url)
    made = build_session_factory(engine)

    def clear() -> None:
        with made() as session:
            session.execute(
                delete(TeamStateRow).where(
                    TeamStateRow.kind == KIND_PROOF, TeamStateRow.key.like(f"{PREFIX}%")
                )
            )
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


def _mine(store: DbStore) -> list[dict[str, Any]]:
    return [p for p in store.read_proofs() if p["round"].startswith(PREFIX)]


def _keys(factory) -> list[str]:
    with factory() as session:
        return sorted(
            session.execute(
                select(TeamStateRow.key).where(
                    TeamStateRow.kind == KIND_PROOF, TeamStateRow.key.like(f"{PREFIX}%")
                )
            ).scalars()
        )


def test_a_round_is_put_replaced_and_read_newest_first_on_postgres(store: DbStore, factory):
    store.put_proof(_round("t1", "2026-10-06T20:00:00Z", 2, 0))
    store.put_proof(_round("t2", "2026-10-06T21:00:00Z", 1, 1))
    store.put_proof(_round("t1", "2026-10-06T20:00:00Z", 5, 0))
    proofs = _mine(store)
    assert [p["round"] for p in proofs] == [f"{PREFIX}t2", f"{PREFIX}t1"]
    assert proofs[1]["rows"][0]["passed"] == 5
    assert proofs[1]["rows"][0]["families"] == ["öz-onarım"]
    assert _keys(factory) == [f"{PREFIX}t1", f"{PREFIX}t2"]


def test_a_nul_in_a_row_is_refused_before_postgres_sees_it(store: DbStore, factory):
    with pytest.raises(Invalid):
        store.put_proof(_round("t1", "2026-10-06T20:00:00Z", 1, 0, row="Talks" + NUL))
    assert _keys(factory) == []


def test_a_posted_round_reaches_the_office_strip_from_postgres(store: DbStore, factory, tmp_path):
    settings = Settings(release=SHA)  # the strip judges the rounds on the release it serves
    app = create_app(settings)
    app.state.team_store = store
    app.state.team_root = tmp_path / "team"
    client = TestClient(app)
    doc = _round("t1", "2026-10-06T20:00:00Z", 2, 0)
    assert client.post("/v1/team/queue/proof", json=doc).status_code == 401
    attach_owner(app, client, settings)

    posted = client.post("/v1/team/queue/proof", json=doc)
    assert posted.status_code == 200, posted.text
    proof = client.get("/v1/team/office").json()["progress"]["proof"]
    assert proof["release"] == SHA
    row = next(r for r in proof["rows"] if r["name"] == ROW)
    assert row["staging_proven"] is True
    assert row["staging"]["round"] == f"{PREFIX}t1"

    refused = client.post("/v1/team/queue/proof", json=doc | {"staging_sha": "x"})
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "invalid"
    assert _keys(factory) == [f"{PREFIX}t1"]
