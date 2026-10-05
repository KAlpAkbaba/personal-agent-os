"""The conversation tables on the REAL database (migration 0067_conversation_transcripts).

What SQLite cannot say and PostgreSQL does: the tables are the ones the MIGRATION makes; the
CHECK refuses a voice profile without consent in the database itself; deleting a person sets
their lines' ``person_id`` to NULL by the foreign key (so the label falls back to
'Konuşmacı N'); deleting a conversation cascades to its lines; search folds the Turkish
I/ı/İ/i and capitals whatever the database locale; a NUL is a 422, not a 500 whose log carries
the line; and ``downgrade()`` takes all four tables away cleanly. The four tables are emptied around
every test; nothing else writes to them.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import delete, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.conversations import service
from app.conversations.models import (
    ConversationRow,
    ConversationSettingRow,
    PersonRow,
    SegmentRow,
)
from app.conversations.service import LiveConversations
from app.db import build_engine, build_session_factory
from app.voice.crypto import ProfileCipher
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "0066_watches"
NOON = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
CIPHER = ProfileCipher("integration-secret")
VOICE_A = [1.0, 0.1, 0.0, 0.0]
VOICE_A2 = [0.97, 0.15, 0.02, 0.0]
VOICE_B = [0.0, 0.1, 1.0, 0.0]
TABLES = ("conversations", "conversation_segments", "conversation_people", "conversation_settings")

#: column -> (data_type, character_maximum_length, is_nullable), as information_schema says.
COLUMNS = {
    "conversations": {
        "id": ("uuid", None, "NO"),
        "mode": ("character varying", 16, "NO"),
        "title": ("character varying", 120, "YES"),
        "started_at": ("timestamp with time zone", None, "NO"),
        "ended_at": ("timestamp with time zone", None, "YES"),
    },
    "conversation_segments": {
        "id": ("uuid", None, "NO"),
        "conversation_id": ("uuid", None, "NO"),
        "seq": ("integer", None, "NO"),
        "spoken_at": ("timestamp with time zone", None, "NO"),
        "text": ("character varying", 4000, "NO"),
        "is_owner": ("boolean", None, "NO"),
        "speaker_no": ("integer", None, "YES"),
        "person_id": ("uuid", None, "YES"),
    },
    "conversation_people": {
        "id": ("uuid", None, "NO"),
        "name": ("character varying", 80, "NO"),
        "name_key": ("character varying", 80, "NO"),
        "created_at": ("timestamp with time zone", None, "NO"),
        "consent_at": ("timestamp with time zone", None, "YES"),
        "consent_note": ("character varying", 200, "YES"),
        "profile_sealed": ("bytea", None, "YES"),
        "profile_model": ("character varying", 80, "YES"),
    },
    "conversation_settings": {
        "id": ("integer", None, "NO"),
        "home_listen": ("boolean", None, "NO"),
        "updated_at": ("timestamp with time zone", None, "NO"),
    },
}


def _alembic() -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


def _clear(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        for model in (SegmentRow, ConversationRow, PersonRow, ConversationSettingRow):
            session.execute(delete(model))
        session.commit()


@pytest.fixture()
def factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    command.upgrade(_alembic(), "head")
    engine = build_engine(settings.database_url)
    sessions = build_session_factory(engine)
    _clear(sessions)
    try:
        yield sessions
    finally:
        command.upgrade(_alembic(), "head")
        _clear(sessions)
        engine.dispose()


def _columns(session: Session, table: str) -> dict[str, tuple[str, int | None, str]]:
    rows = session.execute(
        sql_text(
            "SELECT column_name, data_type, character_maximum_length, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table"
        ),
        {"table": table},
    )
    return {name: (kind, width, nullable) for name, kind, width, nullable in rows}


def test_the_migration_makes_the_four_tables_with_the_contract_columns(factory) -> None:
    with factory() as session:
        for table in TABLES:
            assert _columns(session, table) == COLUMNS[table], table


def test_the_database_refuses_a_profile_without_consent(factory) -> None:
    with factory() as session:
        session.add(PersonRow(name="Gizli", name_key="gizli", created_at=NOON, profile_sealed=b"x"))
        with pytest.raises(IntegrityError, match="ck_conversation_people_profile_needs_consent"):
            session.commit()


def test_named_recognised_deleted_and_relabelled_on_postgres(factory) -> None:
    live = LiveConversations()
    with factory() as db:
        service.record_consent(db, live, CIPHER, name="Ahmet", now=NOON)
        first = service.start_conversation(db, live, now=NOON).id
        service.add_segment(
            db, live, CIPHER, first, text="Selam, tapuya gidelim", embedding=VOICE_A
        )
        assert service.name_speaker(db, live, CIPHER, first, 1, "AHMET").applied is True
        service.stop_conversation(db, live, first)
        later = service.start_conversation(db, live).id
        known = service.add_segment(db, live, CIPHER, later, text="Yine ben", embedding=VOICE_A2)
        other = service.add_segment(db, live, CIPHER, later, text="Ben Ayşe", embedding=VOICE_B)
        db.commit()
        assert (known.segment.speaker, known.ask_who) == ("Ahmet", False)
        assert (other.segment.speaker, other.ask_who) == ("Konuşmacı 2", True)
        assert [c.id for c in service.list_conversations(db, q="TAPUYA")] == [first]

        # The FOREIGN KEY sets person_id NULL - not the service: delete the row in SQL.
        person = db.execute(select(PersonRow.id)).scalar_one()
        db.execute(delete(PersonRow).where(PersonRow.id == person))
        db.commit()
        assert [s.speaker for s in service.get_conversation(db, later).segments] == [
            "Konuşmacı 1",
            "Konuşmacı 2",
        ]
        assert db.execute(select(SegmentRow.person_id)).scalars().all() == [None, None, None]

        # The conversation's lines go with it (ON DELETE CASCADE, in SQL).
        db.execute(delete(ConversationRow).where(ConversationRow.id == first))
        db.commit()
        left = db.execute(select(SegmentRow.conversation_id)).scalars().all()
        assert set(left) == {later}


def test_the_routes_through_the_real_application(factory, settings) -> None:
    client = owner_client(settings)
    cid = client.post("/v1/conversations", json={"mode": "home"}).json()["id"]
    line = client.post(
        f"/v1/conversations/{cid}/segments", json={"content": "Merhaba", "embedding": VOICE_A}
    )
    assert line.status_code == 201, line.text
    named = client.post(f"/v1/conversations/{cid}/speakers/1/name", json={"name": "Ahmet"}).json()
    assert named["applied"] is False
    consent = client.post(
        f"/v1/conversations/people/{named['person']['id']}/consent", json={"note": "sözlü izin"}
    ).json()
    assert consent["has_profile"] is True and consent["consent_at"]
    shown = client.get(f"/v1/conversations/{cid}").json()
    assert [s["speaker"] for s in shown["segments"]] == ["Ahmet"]
    assert client.post(f"/v1/conversations/{cid}/stop").status_code == 200
    assert client.put("/v1/conversations/settings", json={"home_listen": True}).json() == {
        "home_listen": True
    }
    assert client.delete(f"/v1/conversations/{uuid.uuid4()}").status_code == 404
    assert client.delete("/v1/conversations").json() == {"deleted": 1}


@pytest.mark.parametrize(
    ("needle", "text"),
    [
        ("ışık", "Işıkları kapat"),
        ("IŞIK", "Işıkları kapat"),
        ("istanbul", "İSTANBUL'a gidiyoruz"),
        ("İSTANBUL", "istanbul'a gidiyoruz"),
        ("şoför", "ŞOFÖR geldi"),
    ],
)
def test_search_folds_turkish_letters_on_postgres(factory, needle, text) -> None:
    """PostgreSQL's own lower() follows the database locale; the fold is spelled out in SQL."""
    live = LiveConversations()
    with factory() as db:
        cid = service.start_conversation(db, live, now=NOON).id
        service.add_segment(db, live, CIPHER, cid, text=text, is_owner=True)
        db.commit()
        assert [c.id for c in service.list_conversations(db, q=needle)] == [cid]
        assert service.list_conversations(db, q="olmayan") == []


def test_one_voice_is_one_person_on_postgres(factory) -> None:
    """Inspector's probe: three lines of one group came back 'Konuşmacı 1', 'Ahmet',
    'Konuşmacı 1'. A recognised group is pinned and its earlier lines relabelled."""
    live = LiveConversations()
    with factory() as db:
        service.record_consent(db, live, CIPHER, name="Ahmet", now=NOON)
        first = service.start_conversation(db, live, now=NOON).id
        service.add_segment(db, live, CIPHER, first, text="Selam", embedding=VOICE_A)
        service.name_speaker(db, live, CIPHER, first, 1, "Ahmet")
        service.stop_conversation(db, live, first)
        later = service.start_conversation(db, live).id
        far = [0.6466, 0.7628, 0.0, 0.0]  # alone below Ahmet's bar, in his group
        for text, vector in (("Bir", far), ("İki", VOICE_A), ("Üç", far)):
            service.add_segment(db, live, CIPHER, later, text=text, embedding=vector)
        db.commit()
        segments = service.get_conversation(db, later).segments
        assert [(s.speaker, s.speaker_no) for s in segments] == [("Ahmet", 1)] * 3


def test_a_nul_in_a_line_is_a_422_and_never_reaches_the_log(factory, settings, capsys) -> None:
    client = owner_client(settings)
    cid = client.post("/v1/conversations", json={}).json()["id"]
    secret = "Gizli satır 9c2e"
    response = client.post(f"/v1/conversations/{cid}/segments", json={"content": f"{secret}\x00"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "text_invalid"
    for body, code in (
        ({"title": 5}, "title_invalid"),
        ({"title": "a\x00"}, "title_invalid"),
    ):
        refused = client.post("/v1/conversations", json=body)
        assert refused.status_code == 422 and refused.json()["detail"]["code"] == code
    client.post(f"/v1/conversations/{cid}/segments", json={"content": "x", "embedding": VOICE_A})
    big = client.post(f"/v1/conversations/{cid}/speakers/2147483648/name", json={"name": "Ahmet"})
    assert big.status_code == 422 and big.json()["detail"]["code"] == "speaker_invalid"
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert "request_failed" not in captured.out + captured.err
    assert client.post(f"/v1/conversations/{cid}/stop").status_code == 200


def test_downgrade_drops_the_four_tables_and_upgrade_recreates_them(factory) -> None:
    live = LiveConversations()
    with factory() as session:
        cid = service.start_conversation(session, live).id
        service.add_segment(session, live, CIPHER, cid, text="bir", is_owner=True)
        session.commit()
    command.downgrade(_alembic(), BEFORE)
    try:
        with factory() as session:
            for table in TABLES:
                assert _columns(session, table) == {}, table
    finally:
        command.upgrade(_alembic(), "head")
    with factory() as session:
        for table in TABLES:
            assert _columns(session, table) == COLUMNS[table]
        assert session.execute(select(ConversationRow)).first() is None
