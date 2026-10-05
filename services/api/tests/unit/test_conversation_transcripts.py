"""Conversations as TEXT (conversation-transcripts), on SQLite and through ``create_app``.

The owner, 2026-10-05: keep his conversations as text like Wispr Flow, never the audio; tell the
voices apart; when he names a voice ('bu Ahmet') recognise it from then on - but ONLY when the
person's consent is recorded (KVKK: a voiceprint is biometric data). Without consent the voice
stays 'Konuşmacı N' and no profile is kept. Deleting a person removes the profile and every
transcript shows 'Konuşmacı N' again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import LargeBinary, create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.conversations import service
from app.conversations.models import (
    ConversationRow,
    ConversationSettingRow,
    PersonRow,
    SegmentRow,
)
from app.conversations.service import LiveConversations, transcribe_and_drop
from app.main import create_app
from app.voice.crypto import ProfileCipher
from app.voice.speaker import enroll_owner
from app.voice.speaker_profiles import open_profile
from tests.identity_support import authenticate, install_identity

NOON = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
CIPHER = ProfileCipher("test-secret")
TABLES = (ConversationRow, SegmentRow, PersonRow, ConversationSettingRow)

OWNER = [0.0, 0.0, 0.1, 1.0]
VOICE_A = [1.0, 0.1, 0.0, 0.0]
VOICE_A2 = [0.97, 0.15, 0.02, 0.0]
VOICE_B = [0.0, 0.1, 1.0, 0.0]
#: The audio a microphone handed over: it must never reach a row or a statement.
AUDIO_MARK = b"RIFF\x00\x01AUDIO-MARK-7f3c\x00" * 8


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.__table__.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def live() -> LiveConversations:
    return LiveConversations()


def _start(db, live, **kw):
    view = service.start_conversation(db, live, now=kw.pop("now", NOON), **kw)
    db.commit()
    return view


def _say(db, live, cid, text, embedding=None, *, is_owner=None, at=0, owner_profile=None):
    result = service.add_segment(
        db,
        live,
        CIPHER,
        cid,
        text=text,
        embedding=embedding,
        is_owner=is_owner,
        owner_profile=owner_profile,
        now=NOON + timedelta(seconds=at),
    )
    db.commit()
    return result


def _labels(db, cid) -> list[tuple[str, str]]:
    return [(s.speaker, s.text) for s in service.get_conversation(db, cid).segments]


# ------------------------------------------------------------------ (1) no audio persisted


def test_no_table_has_a_column_that_could_hold_audio() -> None:
    binary = [
        (t.__tablename__, c.name)
        for t in TABLES
        for c in t.__table__.columns
        if isinstance(c.type, LargeBinary)
    ]
    # The ONLY bytes column is the sealed, consented, derived profile.
    assert binary == [("conversation_people", "profile_sealed")]


def test_transcribe_and_drop_wipes_the_buffer_even_when_stt_fails() -> None:
    buffer = bytearray(AUDIO_MARK)
    text, embedding = transcribe_and_drop(
        buffer, stt=lambda audio: "merhaba", embedder=lambda audio: VOICE_A
    )
    assert (text, embedding) == ("merhaba", VOICE_A)
    assert buffer == bytearray(len(AUDIO_MARK))  # every byte zeroed

    failing = bytearray(AUDIO_MARK)

    def boom(audio: bytes) -> str:
        raise RuntimeError("stt down")

    with pytest.raises(RuntimeError):
        transcribe_and_drop(failing, stt=boom, embedder=None)
    assert failing == bytearray(len(AUDIO_MARK))


def test_every_write_of_a_whole_conversation_carries_no_audio(engine, factory, live) -> None:
    """Every INSERT/UPDATE/DELETE statement's parameters are inspected - the write paths of
    start, segments, naming, consent, stop, settings - and the stored rows are scanned."""
    written: list[object] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            rows = parameters if executemany else [parameters]
            for row in rows:
                written.extend(row.values() if isinstance(row, dict) else row)

    with factory() as db:
        cid = _start(db, live).id
        for i, embedding in enumerate((VOICE_A, VOICE_B, VOICE_A2)):
            buffer = bytearray(AUDIO_MARK)
            text, vector = transcribe_and_drop(
                buffer, stt=lambda audio, i=i: f"cümle {i}", embedder=lambda audio, e=embedding: e
            )
            _say(db, live, cid, text, vector, at=i)
        service.record_consent(db, live, CIPHER, name="Ahmet", now=NOON)
        service.name_speaker(db, live, CIPHER, cid, 1, "Ahmet", now=NOON)
        service.set_home_listen(db, True, now=NOON)
        service.stop_conversation(db, live, cid, now=NOON)
        db.commit()

    assert written, "the capture saw no writes"
    blobs = [v for v in written if isinstance(v, (bytes, bytearray, memoryview))]
    for value in written:
        raw = bytes(value) if isinstance(value, (bytes, bytearray, memoryview)) else str(value).encode()
        assert b"AUDIO-MARK" not in raw
    # The only bytes ever written: the one sealed profile, which opens to numbers.
    assert len(blobs) == 1
    assert all(isinstance(x, float) for x in open_profile(CIPHER, bytes(blobs[0])))
    with engine.connect() as conn:
        for table in TABLES:
            for row in conn.execute(select(table.__table__)):
                for value in row:
                    raw = value if isinstance(value, bytes) else str(value).encode()
                    assert b"AUDIO-MARK" not in raw


# --------------------------------------------------------- (2) voices, owner, 'bu kim?'


def test_two_voices_are_told_apart_and_bu_kim_is_asked_once_per_new_voice(factory, live) -> None:
    with factory() as db:
        cid = _start(db, live).id
        first = _say(db, live, cid, "Selam", VOICE_A)
        second = _say(db, live, cid, "Nasılsın", VOICE_B, at=1)
        again = _say(db, live, cid, "İyiyim", VOICE_A2, at=2)
        assert [first.ask_who, second.ask_who, again.ask_who] == [True, True, False]
        assert _labels(db, cid) == [
            ("Konuşmacı 1", "Selam"),
            ("Konuşmacı 2", "Nasılsın"),
            ("Konuşmacı 1", "İyiyim"),
        ]


def test_the_owners_lines_are_always_labelled(factory, live) -> None:
    profile = enroll_owner([OWNER, OWNER, OWNER], model_id="fake")
    with factory() as db:
        cid = _start(db, live).id
        by_flag = _say(db, live, cid, "Ben söyledim", None, is_owner=True)
        by_voice = _say(db, live, cid, "Ben yine", OWNER, at=1, owner_profile=profile)
        other = _say(db, live, cid, "Ben değilim", VOICE_A, at=2, owner_profile=profile)
        assert (by_flag.ask_who, by_voice.ask_who, other.ask_who) == (False, False, True)
        assert _labels(db, cid) == [
            ("Sen", "Ben söyledim"),
            ("Sen", "Ben yine"),
            ("Konuşmacı 1", "Ben değilim"),
        ]


# ------------------------------------------------------------------- (3) consent and names


def test_naming_without_consent_keeps_no_profile_and_the_label_stays(factory, live) -> None:
    with factory() as db:
        cid = _start(db, live).id
        _say(db, live, cid, "Selam", VOICE_A)
        named = service.name_speaker(db, live, CIPHER, cid, 1, "Ahmet", now=NOON)
        db.commit()
        assert named.applied is False
        assert "izin" in named.message
        person = db.execute(select(PersonRow)).scalar_one()
        assert person.profile_sealed is None and person.consent_at is None
        assert _labels(db, cid) == [("Konuşmacı 1", "Selam")]


def test_consent_after_naming_stores_the_profile_and_relabels(factory, live) -> None:
    with factory() as db:
        cid = _start(db, live).id
        _say(db, live, cid, "Selam", VOICE_A)
        service.name_speaker(db, live, CIPHER, cid, 1, "Ahmet", now=NOON)
        person = service.record_consent(db, live, CIPHER, name="Ahmet", now=NOON)
        db.commit()
        assert person.consent_at is not None and person.has_profile
        assert _labels(db, cid) == [("Ahmet", "Selam")]


def test_the_database_refuses_a_profile_without_consent(factory) -> None:
    with factory() as db:
        row = PersonRow(name="Gizli", name_key="gizli", created_at=NOON, profile_sealed=b"x")
        db.add(row)
        with pytest.raises(IntegrityError, match="profile_needs_consent|CHECK"):
            db.commit()
        db.rollback()
        # The same row with consent recorded is accepted: the CHECK is what refused it.
        db.add(PersonRow(name="Gizli", name_key="gizli", created_at=NOON, profile_sealed=b"x",
                         consent_at=NOON))
        db.commit()


def test_a_named_consenting_voice_is_recognised_in_a_later_conversation(factory, live) -> None:
    with factory() as db:
        service.record_consent(db, live, CIPHER, name="Ahmet", now=NOON)
        first = _start(db, live).id
        _say(db, live, first, "Selam", VOICE_A)
        result = service.name_speaker(db, live, CIPHER, first, 1, "ahmet", now=NOON)
        assert result.applied is True
        service.stop_conversation(db, live, first, now=NOON)
        db.commit()

        later = _start(db, live, now=NOON + timedelta(days=1)).id
        known = _say(db, live, later, "Yine ben", VOICE_A2)
        stranger = _say(db, live, later, "Ben yeniyim", VOICE_B, at=1)
        assert (known.ask_who, stranger.ask_who) == (False, True)
        # Every voice gets its number; the named one shows the name.
        assert _labels(db, later) == [("Ahmet", "Yine ben"), ("Konuşmacı 2", "Ben yeniyim")]
        # Below the threshold nobody is recognised: a voice ~0.6 from Ahmet's is a stranger.
        near = _say(db, live, later, "Benziyorum", [0.6, 0.0, 0.0, 0.8], at=2)
        assert near.ask_who is True and near.segment.speaker == "Konuşmacı 3"


def test_deleting_a_person_removes_the_profile_and_relabels_every_transcript(
    factory, live
) -> None:
    with factory() as db:
        service.record_consent(db, live, CIPHER, name="Ahmet", now=NOON)
        cid = _start(db, live).id
        _say(db, live, cid, "Selam", VOICE_A)
        service.name_speaker(db, live, CIPHER, cid, 1, "Ahmet", now=NOON)
        service.stop_conversation(db, live, cid, now=NOON)
        later_id = _start(db, live).id
        _say(db, live, later_id, "Yine ben", VOICE_A2)
        db.commit()
        assert _labels(db, cid)[0][0] == "Ahmet" and _labels(db, later_id)[0][0] == "Ahmet"

        person_id = db.execute(select(PersonRow.id)).scalar_one()
        assert service.delete_person(db, live, person_id) is True
        db.commit()
        assert db.execute(select(PersonRow)).first() is None
        assert _labels(db, cid) == [("Konuşmacı 1", "Selam")]
        assert _labels(db, later_id) == [("Konuşmacı 1", "Yine ben")]
        assert all(s.person_id is None for s in db.execute(select(SegmentRow)).scalars())
        # The voice is nobody's now: the next conversation does not know it.
        service.stop_conversation(db, live, later_id, now=NOON)
        third = _start(db, live).id
        assert _say(db, live, third, "Kimim?", VOICE_A).ask_who is True
        assert _labels(db, third) == [("Konuşmacı 1", "Kimim?")]


# ------------------------------------------------------------ (4) reading, search, 'unut'


def test_search_delete_and_forget(factory, live) -> None:
    with factory() as db:
        one = _start(db, live).id
        _say(db, live, one, "Yarın tapu dairesine gideceğiz", None, is_owner=True)
        service.stop_conversation(db, live, one, now=NOON)
        two = _start(db, live).id
        _say(db, live, two, "Market listesi: süt, ekmek", None, is_owner=True)
        service.stop_conversation(db, live, two, now=NOON)
        db.commit()
        assert [c.id for c in service.list_conversations(db, q="TAPU")] == [one]
        assert {c.id for c in service.list_conversations(db)} == {one, two}
        assert service.delete_conversation(db, live, one) is True
        db.commit()
        assert db.execute(select(SegmentRow).where(SegmentRow.conversation_id == one)).first() is None
        assert service.delete_conversation(db, live, one) is False
        assert service.forget_all(db, live) == 1
        db.commit()
        assert service.list_conversations(db) == []


def test_one_open_conversation_and_no_lines_after_stop(factory, live) -> None:
    with factory() as db:
        cid = _start(db, live).id
        with pytest.raises(service.ConversationRefused) as refused:
            _start(db, live)
        assert refused.value.code == "already_open"
        service.stop_conversation(db, live, cid, now=NOON)
        db.commit()
        with pytest.raises(service.ConversationRefused) as closed:
            _say(db, live, cid, "geç kaldım")
        assert closed.value.code == "closed"
        with pytest.raises(service.ConversationRefused):
            _say(db, live, _start(db, live).id, "   ")


def test_evde_dinle_is_a_standing_setting(factory) -> None:
    with factory() as db:
        assert service.get_home_listen(db) is False
        service.set_home_listen(db, True, now=NOON)
        db.commit()
        assert service.get_home_listen(db) is True
        service.set_home_listen(db, False, now=NOON)
        db.commit()
        assert service.get_home_listen(db) is False


# ----------------------------------------------------------------- (5) through create_app


@pytest.fixture()
def api(engine):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    return app, client


def test_the_routes_from_start_to_forget(api) -> None:
    _app, client = api
    started = client.post("/v1/conversations", json={"mode": "manual"})
    assert started.status_code == 201, started.text
    cid = started.json()["id"]
    line = client.post(
        f"/v1/conversations/{cid}/segments", json={"content": "Selam", "embedding": VOICE_A}
    )
    assert line.status_code == 201, line.text
    assert line.json()["ask_who"] is True and line.json()["speaker"] == "Konuşmacı 1"
    refused = client.post(
        f"/v1/conversations/{cid}/segments", json={"content": "x", "audio": "UklGRg=="}
    )
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "audio_refused"
    named = client.post(f"/v1/conversations/{cid}/speakers/1/name", json={"name": "Ahmet"})
    assert named.status_code == 200 and named.json()["applied"] is False
    people = client.get("/v1/conversations/people").json()["items"]
    assert [(p["name"], p["consent"], p["has_profile"]) for p in people] == [
        ("Ahmet", False, False)
    ]
    consent = client.post(f"/v1/conversations/people/{people[0]['id']}/consent", json={})
    assert consent.status_code == 200 and consent.json()["has_profile"] is True
    shown = client.get(f"/v1/conversations/{cid}").json()
    assert [s["speaker"] for s in shown["segments"]] == ["Ahmet"]
    assert client.post(f"/v1/conversations/{cid}/stop").status_code == 200
    assert client.put("/v1/conversations/settings", json={"home_listen": True}).json() == {
        "home_listen": True
    }
    assert client.get("/v1/conversations", params={"q": "sel"}).json()["items"][0]["id"] == cid
    assert client.delete(f"/v1/conversations/people/{people[0]['id']}").json() == {"deleted": 1}
    assert client.get(f"/v1/conversations/{cid}").json()["segments"][0]["speaker"] == "Konuşmacı 1"
    assert client.delete(f"/v1/conversations/{uuid.uuid4()}").status_code == 404
    assert client.delete("/v1/conversations").json() == {"deleted": 1}
