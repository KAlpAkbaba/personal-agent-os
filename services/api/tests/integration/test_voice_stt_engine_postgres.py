"""stt-engine-on-turn-audit on the REAL database (ADR-0249 D4).

The unit suite runs on SQLite, where ``metadata_json`` and ``context_json`` are plain JSON
text. Here the same utterances go through the application ``create_app`` builds, to the dev
stack's PostgreSQL at alembic head, and the audit row is read back the way the ADR's
comparison SELECT reads it: a JSONB expression, ``metadata_json ->> 'stt_engine'``, through
a fresh session. No migration belongs to this card: the head is unchanged.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text as sql_text

from app.config import Settings
from app.db import build_engine
from app.voice.providers import TRANSPORT_TEXT
from app.voice.realtime_sessions import service
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
UNDERSTOOD = "Alarmlarla neler yapabilirsin?"

#: The comparison the ADR names, narrowed to one session: names and numbers only.
ENGINE_OF_TURNS = sql_text(
    """
    SELECT metadata_json ->> 'stt_engine' AS stt_engine,
           metadata_json -> 'understanding' ->> 'band' AS band,
           metadata_json ->> 'intent' AS intent
      FROM audit_events
     WHERE action = :action AND category = :category AND subject_ref = :sid
     ORDER BY id
    """
)


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def client(settings: Settings) -> Any:
    return owner_client(settings)


def _session(client: Any) -> str:
    created = client.post("/v1/voice/realtime/sessions", json={"transport": TRANSPORT_TEXT})
    assert created.status_code == 201, created.text
    assert created.json()["transport"] == TRANSPORT_TEXT
    return created.json()["session_id"]


def _say(client: Any, sid: str, *, turn: int, engine: Any = ...) -> dict[str, Any]:
    event: dict[str, Any] = {
        "kind": "utterance",
        "t_ms": 1000 * turn,
        "turn": turn,
        "text": UNDERSTOOD,
    }
    if engine is not ...:
        event["payload"] = {"stt_engine": engine}
    response = client.post(f"/v1/voice/realtime/sessions/{sid}/events", json={"events": [event]})
    assert response.status_code == 200, response.text
    return response.json()


def _turns(settings: Settings, sid: str) -> list[tuple[Any, ...]]:
    """The audit rows of one session through a FRESH connection, read with JSONB operators."""
    engine = build_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                ENGINE_OF_TURNS,
                {
                    "action": service.ACTION_INTENT_RESOLVED,
                    "category": service.AUDIT_CATEGORY,
                    "sid": sid,
                },
            )
            return [tuple(row) for row in rows]
    finally:
        engine.dispose()


def _turn_record(settings: Settings, sid: str) -> dict[str, Any]:
    engine = build_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            value = conn.execute(
                sql_text(
                    "SELECT context_json -> 'last_utterance' FROM realtime_sessions WHERE id = :id"
                ),
                {"id": uuid.UUID(sid)},
            ).scalar_one()
            return dict(value)
    finally:
        engine.dispose()


def test_postgres_each_name_is_stored_and_queryable_by_jsonb(
    settings: Settings, client: Any
) -> None:
    stored = []
    for name in ("chrome-cihaz-ici", "chrome-bulut", "bilinmiyor"):
        sid = _session(client)
        _say(client, sid, turn=1, engine=name)
        rows = _turns(settings, sid)
        assert len(rows) == 1, rows
        assert rows[0][0] == name
        assert rows[0][2] == "capabilities_query" and rows[0][1] is not None
        stored.append(rows[0][0])
    assert len(set(stored)) == 3


def test_postgres_an_unknown_name_is_stored_as_null(settings: Settings, client: Any) -> None:
    sid = _session(client)
    said = _say(client, sid, turn=1, engine="google")
    assert said["resolved_intents"][0]["intent"] == "capabilities_query"
    rows = _turns(settings, sid)
    assert len(rows) == 1 and rows[0][0] is None
    # Stored as JSON null, not as a missing key: the comparison groups it as "unnamed".
    engine = build_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            kind = conn.execute(
                sql_text(
                    "SELECT jsonb_typeof(metadata_json::jsonb -> 'stt_engine') FROM audit_events"
                    " WHERE action = :action AND subject_ref = :sid"
                ),
                {"action": service.ACTION_INTENT_RESOLVED, "sid": sid},
            ).scalar_one()
    finally:
        engine.dispose()
    assert kind == "null"


def test_postgres_the_turn_record_carries_the_latest_engine(
    settings: Settings, client: Any
) -> None:
    sid = _session(client)
    _say(client, sid, turn=1, engine="chrome-cihaz-ici")
    assert _turn_record(settings, sid)["stt_engine"] == "chrome-cihaz-ici"
    _say(client, sid, turn=2, engine="chrome-bulut")
    record = _turn_record(settings, sid)
    assert (record["turn"], record["stt_engine"]) == (2, "chrome-bulut")
    assert [row[0] for row in _turns(settings, sid)] == ["chrome-cihaz-ici", "chrome-bulut"]


def test_no_migration_belongs_to_this_card(settings: Settings) -> None:
    """One head, the database is at it, and no revision mentions the name."""
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, heads
    engine = build_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_heads() == tuple(heads)
    finally:
        engine.dispose()
    versions = API_ROOT / "alembic" / "versions"
    assert not [
        p.name for p in versions.glob("*.py") if "stt_engine" in p.read_text(encoding="utf-8")
    ]
