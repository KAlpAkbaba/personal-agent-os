"""The misheard notebook through the REAL relay on the dev stack's PostgreSQL (alembic head).

The unit suite (``tests/unit/test_misheard_relay.py``) proves the four conditions on SQLite.
Here the same turns go through the application ``create_app`` builds, to PostgreSQL, where
one failed statement aborts a whole transaction unless it ran in a savepoint - so a notebook
that fails is the one thing SQLite cannot show. Every row is read back through a FRESH
session. Each case runs in a paid (simulator) and a local-router session.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import delete, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.misheard import service as misheard
from app.voice.misheard.models import MisheardUtterance
from app.voice.providers import TRANSPORT_TEXT
from app.voice.providers_local_router import LOCAL_ROUTER_PROVIDER_NAME
from app.voice.realtime_sessions import service as relay
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]

UNBOUND_REQUEST = "Mutfaktaki bilgisayarda bir şey yap."
UNBOUND_APP = "Mutfaktaki bilgisayarda hesap makinesini aç."
ACTING = "Hesap makinesini kapat."
OBJECTION = "Hayır."
FAILING = "Saat kaç?"


class _Clock:
    def __init__(self) -> None:
        self.at = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def client(settings: Settings) -> Any:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    return owner_client(settings)


@pytest.fixture()
def fresh(settings: Settings) -> Iterator[sessionmaker[Session]]:
    engine = build_engine(settings.database_url)
    try:
        yield build_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture()
def sessions(fresh: sessionmaker[Session]) -> Iterator[list[str]]:
    """The sessions a test made; their notebook rows are removed after it."""
    made: list[str] = []
    misheard.reset_hold()
    yield made
    misheard.reset_hold()
    with fresh() as db:
        ids = [uuid.UUID(sid) for sid in made]
        if ids:
            db.execute(delete(MisheardUtterance).where(MisheardUtterance.session_id.in_(ids)))
            db.commit()


@pytest.fixture()
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(relay, "utcnow", c)
    return c


@pytest.fixture(params=["paid", "local"])
def mode(request) -> str:
    return request.param


def _session(client: Any, mode: str, made: list[str]) -> str:
    body = {"transport": TRANSPORT_TEXT} if mode == "local" else {}
    created = client.post("/v1/voice/realtime/sessions", json=body)
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    assert (created.json()["provider"] == LOCAL_ROUTER_PROVIDER_NAME) is (mode == "local")
    made.append(sid)
    return sid


def _say(client: Any, sid: str, text: str) -> dict[str, Any]:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "utterance", "t_ms": 1000, "turn": 1, "text": text}]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _tool(client: Any, sid: str, name: str) -> dict[str, Any]:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": f"c-{uuid.uuid4()}", "name": name, "arguments": {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _rows(fresh: sessionmaker[Session], sid: str) -> list[MisheardUtterance]:
    with fresh() as db:
        return list(
            db.execute(
                select(MisheardUtterance)
                .where(MisheardUtterance.session_id == uuid.UUID(sid))
                .order_by(MisheardUtterance.heard_at)
            ).scalars()
        )


def _replace_handler(monkeypatch, client: Any, name: str, handler: Any) -> None:
    registry = client.app.state.voice_realtime.registry
    spec = registry.get(name)
    monkeypatch.setitem(registry._tools, name, dataclasses.replace(spec, handler=handler))


def test_postgres_no_intent(client, fresh, sessions, clock, mode) -> None:
    sid = _session(client, mode, sessions)
    _say(client, sid, UNBOUND_REQUEST)
    rows = _rows(fresh, sid)
    assert [(r.reason, r.sentence, r.mode, r.band) for r in rows] == [
        ("no_intent", UNBOUND_REQUEST, mode, "low")
    ]
    assert rows[0].heard_at.tzinfo is not None and rows[0].heard_at == clock.at
    assert rows[0].expires_at == clock.at + timedelta(days=30)


def test_postgres_asked_question(client, fresh, sessions, clock, mode) -> None:
    sid = _session(client, mode, sessions)
    assert _say(client, sid, UNBOUND_APP)["resolved_intents"][0]["band"] == "low"
    rows = _rows(fresh, sid)
    assert [(r.reason, r.sentence, r.resolved_intent) for r in rows] == [
        ("asked_question", UNBOUND_APP, "app_open")
    ]


def test_postgres_objected_carries_the_acted_sentence(client, fresh, sessions, clock, mode) -> None:
    sid = _session(client, mode, sessions)
    _say(client, sid, ACTING)
    acted_at = clock.at
    clock.advance(5)
    _say(client, sid, OBJECTION)
    rows = _rows(fresh, sid)
    assert [(r.reason, r.sentence, r.resolved_intent) for r in rows] == [
        ("objected", ACTING, "app_close")
    ]
    assert rows[0].heard_at == acted_at


def _raises_voice_error(ctx, arguments):
    raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "planned failure")


def _crashes(ctx, arguments):
    raise RuntimeError("planned crash")


def _earns_failed(ctx, arguments):
    return {"status": "ok"}


@pytest.mark.parametrize(
    ("tool", "handler"),
    [
        ("clock.now", _raises_voice_error),
        ("clock.now", _crashes),
        ("research.sources", _earns_failed),
    ],
    ids=["voice_error", "crash", "earns_failed"],
)
def test_postgres_tool_failed(
    client, fresh, sessions, clock, mode, monkeypatch, tool, handler
) -> None:
    _replace_handler(monkeypatch, client, tool, handler)
    sid = _session(client, mode, sessions)
    _say(client, sid, FAILING)
    assert _tool(client, sid, tool)["status"] == "failed"
    clock.advance(1)
    assert _tool(client, sid, tool)["status"] == "failed"  # a second failure: the same row
    rows = _rows(fresh, sid)
    assert [(r.reason, r.tool, r.sentence) for r in rows] == [("tool_failed", tool, FAILING)]


def test_postgres_a_failed_notebook_leaves_the_turn_committed(
    client, fresh, sessions, clock, mode, monkeypatch
) -> None:
    """A statement that FAILS inside the notebook's savepoint aborts only the savepoint: the
    turn's own audit row and turn record are committed, and no notebook row exists."""

    def _fails(db, **kwargs):
        with db.begin_nested():
            db.execute(sql_text("SELECT 1/0"))

    monkeypatch.setattr(misheard, "record", _fails)
    _replace_handler(monkeypatch, client, "clock.now", _crashes)
    sid = _session(client, mode, sessions)
    said = _say(client, sid, UNBOUND_REQUEST)
    assert said["resolved_intents"][0]["intent"] == "none"
    clock.advance(1)
    _say(client, sid, FAILING)
    assert _tool(client, sid, "clock.now")["status"] == "failed"
    with fresh() as db:
        audits = db.execute(
            sql_text(
                "SELECT action FROM audit_events WHERE subject_ref = :sid AND action IN "
                "('voice_intent_resolved', 'voice_tool_call') ORDER BY id"
            ),
            {"sid": sid},
        ).scalars()
        assert list(audits) == ["voice_intent_resolved", "voice_intent_resolved", "voice_tool_call"]
        record = db.execute(
            sql_text(
                "SELECT context_json -> 'last_utterance' ->> 'intent' FROM realtime_sessions "
                "WHERE id = :id"
            ),
            {"id": uuid.UUID(sid)},
        ).scalar_one()
        assert record is not None
        status = db.execute(
            sql_text("SELECT status FROM realtime_tool_calls WHERE session_id = :id"),
            {"id": uuid.UUID(sid)},
        ).scalar_one()
        assert status == "failed"
    assert _rows(fresh, sid) == []
