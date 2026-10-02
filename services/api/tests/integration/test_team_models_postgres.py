"""The model setting and a status with limits on the REAL database (ADR-0214 addendum 4).

The setting is one ``team_state`` row (``kind='models'``, ``key='models'``) and the live status
another; SQLite enforces no VARCHAR length and has no JSONB, so put / read / replace and the
route's own path are taken to the dev stack's PostgreSQL, and every row is held to the column
widths the model states.

The two rows are singletons, and the dev database may hold real ones: what was there is put
back when a test ends.
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
from app.team import models_setting
from app.team.models import TeamStateRow
from app.team.store import DbStore, Invalid
from tests.integration.conftest import attach_owner

pytestmark = pytest.mark.integration

FABLE, OPUS, SONNET = models_setting.CHAIN
KINDS = ("models", "status")
MODELS = "/v1/team/queue/models"
STATUS = "/v1/team/queue/status"
#: Longer than anything a real machine or cycle is called.
MACHINE = "GMKADIRAKBABA-OFFICE-PC"
CYCLE = "cycle-2026-10-02-a-long-cycle-id"


def _setting(stamp: str = "2026-10-02T12:00:00Z", fallback: bool = True, **roles: str) -> dict:
    return {
        "roles": {**models_setting.DEFAULT_ROLES, **roles},
        "fallback": fallback,
        "updated_at": stamp,
    }


def _status() -> dict[str, Any]:
    lowered = [
        {
            "task": f"a-task-with-a-long-id-{index:02d}",
            "role": "worker",
            "from": OPUS,
            "to": SONNET,
            "at": f"2026-10-02T11:{index:02d}:00Z",
        }
        for index in range(20)
    ]
    return {
        "cycle_id": CYCLE,
        "machine": MACHINE,
        "pid": 4_194_304,
        "started_at": "2026-10-02T11:00:00Z",
        "runs": [
            {
                "task": "a-task",
                "role": "worker",
                "started_at": "2026-10-02T11:00:05Z",
                "model": SONNET,
            },
            {
                "task": "b-task",
                "role": "inspector",
                "started_at": "2026-10-02T11:00:06Z",
                "model": FABLE,
            },
        ],
        "estimated_usd": 1.25,
        "usage_limit": {"state": "ok", "resets_at": None},
        "limits": {
            "fable": {"state": "limited", "resets_at": "2026-10-03T07:00:00Z", "used_pct": 100},
            "all": {"state": "ok", "resets_at": None, "used_pct": 47.5},
            "fallback": True,
            "lowered": lowered,
        },
        "updated_at": "2026-10-02T11:30:00Z",
    }


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(Settings().database_url)
    made = build_session_factory(engine)
    with made() as session:
        found = session.execute(select(TeamStateRow).where(TeamStateRow.kind.in_(KINDS))).scalars()
        kept = [(row.kind, row.key, row.doc, row.updated_at) for row in found]

    def clear() -> None:
        with made() as session:
            session.execute(delete(TeamStateRow).where(TeamStateRow.kind.in_(KINDS)))
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


def _fits_its_columns(factory: sessionmaker[Session]) -> list[tuple[str, str]]:
    widths = {
        column.name: column.type.length
        for column in TeamStateRow.__table__.columns
        if getattr(column.type, "length", None)
    }
    assert set(widths) == {"kind", "key", "updated_at"}
    with factory() as session:
        rows = session.execute(select(TeamStateRow).where(TeamStateRow.kind.in_(KINDS))).scalars()
        seen = []
        for row in rows:
            seen.append((row.kind, row.key))
            for name, width in widths.items():
                value = getattr(row, name)
                assert len(value) <= width, (
                    f"{row.kind}/{row.key}: {name} is {len(value)} > {width}"
                )
    return sorted(seen)


def test_the_setting_is_put_read_and_replaced_on_postgres(factory) -> None:
    store = DbStore(factory)
    assert store.read_models() is None
    first = _setting(worker=SONNET, inspector=OPUS)
    store.put_models(first)
    assert store.read_models() == first
    # through a store of its own: the row, not this object's memory
    assert DbStore(factory).read_models() == first

    second = _setting("2026-10-02T12:05:00Z", False, lead=OPUS)
    store.put_models(second)
    assert DbStore(factory).read_models() == second
    assert _fits_its_columns(factory) == [("models", "models")]

    with pytest.raises(Invalid):
        store.put_models(_setting(worker=FABLE, inspector=SONNET))
    with pytest.raises(Invalid):
        store.put_models(_setting(worker="claude-opus-4"))
    assert DbStore(factory).read_models() == second


def test_a_status_with_models_and_limits_is_written_on_postgres(factory) -> None:
    store = DbStore(factory)
    status = _status()
    store.put_status(status)
    assert DbStore(factory).read_status() == status
    store.put_status({**status, "runs": [], "updated_at": "2026-10-02T11:35:00Z"})
    assert DbStore(factory).read_status()["limits"] == status["limits"]
    store.put_models(_setting())
    assert _fits_its_columns(factory) == [("models", "models"), ("status", "status")]


def test_the_routes_keep_the_setting_and_the_status_on_postgres(factory) -> None:
    settings = Settings()
    app = create_app(settings)
    app.state.team_store = DbStore(factory)
    client = TestClient(app)
    attach_owner(app, client, settings)

    assert client.get(MODELS).json()["roles"] == models_setting.DEFAULT_ROLES
    wanted = {"roles": {**models_setting.DEFAULT_ROLES, "worker": SONNET}, "fallback": False}
    put = client.put(MODELS, json=wanted)
    assert put.status_code == 200, put.text
    assert client.get(MODELS).json() == put.json()
    assert DbStore(factory).read_models() == put.json()

    equal = {**wanted, "roles": {**wanted["roles"], "inspector": SONNET}}
    assert client.put(MODELS, json=equal).status_code == 200  # as strong as the worker
    weaker = {**wanted, "roles": {**wanted["roles"], "worker": FABLE, "inspector": OPUS}}
    refused = client.put(MODELS, json=weaker)
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "inspector_weaker_than_worker"
    assert DbStore(factory).read_models()["roles"]["inspector"] == SONNET

    status = _status()
    assert client.put(STATUS, json=status).status_code == 200
    assert client.get(STATUS).json() == status
    bad = _status()
    bad["limits"]["fable"]["used_pct"] = "ninety"
    assert client.put(STATUS, json=bad).status_code == 422
    assert DbStore(factory).read_status() == status

    office = client.get("/v1/team/office").json()
    assert office["models"] == DbStore(factory).read_models()
    assert office["cycle"]["limits"]["all"]["used_pct"] == 47.5
    assert _fits_its_columns(factory) == [("models", "models"), ("status", "status")]
