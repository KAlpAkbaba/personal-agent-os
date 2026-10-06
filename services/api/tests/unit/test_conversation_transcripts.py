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
from app.voice.models import SpeakerProfile
from app.voice.service import enroll_and_store_owner
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
        raw = (
            bytes(value)
            if isinstance(value, (bytes, bytearray, memoryview))
            else str(value).encode()
        )
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
        db.add(
            PersonRow(
                name="Gizli",
                name_key="gizli",
                created_at=NOON,
                profile_sealed=b"x",
                consent_at=NOON,
            )
        )
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


def test_a_recognised_person_is_pinned_to_the_voice_group(factory, live) -> None:
    """Inspector, 2026-10-05: one voice came back 'Konuşmacı 1', 'Ahmet', 'Konuşmacı 1' because
    each line was matched alone. Once the group is recognised, every line of it - before and
    after - is the person, and 'bu kim?' is never asked for that group."""
    with factory() as db:
        service.record_consent(db, live, CIPHER, name="Ahmet", now=NOON)
        first = _start(db, live).id
        _say(db, live, first, "Selam", VOICE_A)
        service.name_speaker(db, live, CIPHER, first, 1, "Ahmet", now=NOON)
        service.stop_conversation(db, live, first, now=NOON)
        db.commit()

        later = _start(db, live).id
        # Alone, `far` is cos 0.72 from Ahmet's profile (bar 0.75) and `near` is his voice;
        # far-near is cos 0.72 too (group bar 0.70), so all three are one group.
        far = [0.6466, 0.7628, 0.0, 0.0]
        near = VOICE_A
        results = [
            _say(db, live, later, "Bir", far),
            _say(db, live, later, "İki", near, at=1),
            _say(db, live, later, "Üç", far, at=2),
        ]
        assert [r.segment.speaker_no for r in results] == [1, 1, 1]
        assert [r.segment.speaker for r in results[1:]] == ["Ahmet", "Ahmet"]
        assert [r.ask_who for r in results[1:]] == [False, False]
        assert _labels(db, later) == [("Ahmet", "Bir"), ("Ahmet", "İki"), ("Ahmet", "Üç")]
        # Twenty more `far` lines drag the group's centroid below the bar (cos ~0.74); the
        # group stays his - it is pinned, not matched afresh on every line.
        tail = [_say(db, live, later, f"satır {i}", far, at=3 + i) for i in range(20)]
        assert {(r.segment.speaker, r.segment.speaker_no, r.ask_who) for r in tail} == {
            ("Ahmet", 1, False)
        }


def test_a_known_voice_is_not_asked_bu_kim(factory, live) -> None:
    with factory() as db:
        service.record_consent(db, live, CIPHER, name="Ahmet", now=NOON)
        first = _start(db, live).id
        _say(db, live, first, "Selam", VOICE_A)
        service.name_speaker(db, live, CIPHER, first, 1, "Ahmet", now=NOON)
        service.stop_conversation(db, live, first, now=NOON)
        later = _start(db, live).id
        known = [_say(db, live, later, f"satır {i}", VOICE_A2, at=i) for i in range(3)]
        assert [k.ask_who for k in known] == [False, False, False]
        assert {k.segment.speaker for k in known} == {"Ahmet"}


def test_stop_drops_the_voice_groups(factory, live) -> None:
    """Nothing biometric outlives the conversation: after stop, no group of it is in memory."""
    with factory() as db:
        cid = _start(db, live).id
        _say(db, live, cid, "Selam", VOICE_A)
        assert live.peek(cid) is not None and live.peek(cid).clusterer.count == 1
        service.stop_conversation(db, live, cid, now=NOON)
        assert live.peek(cid) is None
        assert live.items() == []


def test_forget_all_deletes_every_line_too(factory, live) -> None:
    with factory() as db:
        cid = _start(db, live).id
        _say(db, live, cid, "Unutulacak satır", None, is_owner=True)
        db.commit()
        assert service.forget_all(db, live) == 1
        db.commit()
        assert db.execute(select(SegmentRow)).first() is None
        assert live.items() == []


@pytest.mark.parametrize(
    ("needle", "text"),
    [
        ("ışık", "Işıkları kapat"),
        ("IŞIK", "Işıkları kapat"),
        ("işık", "Işıkları kapat"),
        ("istanbul", "İSTANBUL'a gidiyoruz"),
        ("İSTANBUL", "istanbul'a gidiyoruz"),
        ("şoför", "ŞOFÖR geldi"),
        ("ÇAĞRI", "çağrı geldi"),
    ],
)
def test_search_folds_turkish_letters(factory, live, needle, text) -> None:
    with factory() as db:
        cid = _start(db, live).id
        _say(db, live, cid, text, None, is_owner=True)
        _say(db, live, cid, "başka bir şey", None, is_owner=True, at=1)
        db.commit()
        assert [c.id for c in service.list_conversations(db, q=needle)] == [cid]
        assert service.list_conversations(db, q="olmayan kelime") == []


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"text": "a\x00b"}, "text_invalid"),
        ({"text": "selam", "embedding": [0.0, 0.0, 0.0]}, "embedding_invalid"),
        ({"text": "selam", "embedding": [float("nan"), 1.0]}, "embedding_invalid"),
        ({"text": "selam", "embedding": [float("inf"), 1.0]}, "embedding_invalid"),
    ],
)
def test_the_service_refuses_bad_lines(factory, live, kw, code) -> None:
    with factory() as db:
        cid = _start(db, live).id
        with pytest.raises(service.ConversationRefused) as refused:
            service.add_segment(db, live, CIPHER, cid, **kw)
        assert refused.value.code == code
        assert live.peek(cid).clusterer.count == 0  # a bad vector never reaches a group


def test_the_service_refuses_bad_titles_names_notes_and_speaker_numbers(factory, live) -> None:
    with factory() as db:
        for title in ("a\x00", 5, ["x"]):
            with pytest.raises(service.ConversationRefused) as refused:
                service.start_conversation(db, live, title=title)
            assert refused.value.code == "title_invalid"
        cid = _start(db, live).id
        _say(db, live, cid, "Selam", VOICE_A)
        for number in (0, -1, 2**31, 2**63):
            with pytest.raises(service.ConversationRefused) as refused:
                service.name_speaker(db, live, CIPHER, cid, number, "Ahmet")
            assert refused.value.code == "speaker_invalid"
        with pytest.raises(service.ConversationRefused) as refused:
            service.name_speaker(db, live, CIPHER, cid, 1, "Ah\x00met")
        assert refused.value.code == "name_invalid"
        for note in ("not\x00", 7):
            with pytest.raises(service.ConversationRefused) as refused:
                service.record_consent(db, live, CIPHER, name="Ahmet", note=note)
            assert refused.value.code == "note_invalid"


def test_deleting_a_person_removes_the_profile_and_relabels_every_transcript(factory, live) -> None:
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
        assert (
            db.execute(select(SegmentRow).where(SegmentRow.conversation_id == one)).first() is None
        )
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


class _MemoryStore:
    """The object store the owner's sealed voice profile lives in, in memory."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, **_kw: object) -> None:
        self.objects[key] = bytes(data)

    def get(self, key: str) -> bytes:
        return self.objects[key]


@pytest.fixture()
def api(engine):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessions
    app.state.artifacts = artifacts
    # The owner's enrolled profile is read the voice runtime's way: row + sealed object.
    SpeakerProfile.__table__.create(engine)
    voice = app.state.voice
    voice._engine, voice._session_factory, voice._store = engine, sessions, _MemoryStore()
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    return app, client


def _enroll_owner(app) -> None:
    voice = app.state.voice
    with voice.session() as session:
        enroll_and_store_owner(
            session,
            voice.store,
            voice.cipher,
            sample_embeddings=[OWNER, OWNER, OWNER],
            model_id="fake",
            prefix="voice/speakers",
        )


def test_the_route_labels_the_owner_by_flag_and_by_his_enrolled_voice(api) -> None:
    app, client = api
    cid = client.post("/v1/conversations", json={}).json()["id"]

    def say(body):
        response = client.post(f"/v1/conversations/{cid}/segments", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    flagged = say({"content": "Ben dedim", "is_owner": True})
    assert (flagged["speaker"], flagged["is_owner"], flagged["ask_who"]) == ("Sen", True, False)
    # Before enrolment his voice is just a voice.
    assert say({"content": "Ben miyim", "embedding": OWNER})["speaker"] == "Konuşmacı 1"
    _enroll_owner(app)
    by_voice = say({"content": "Benim", "embedding": OWNER})
    assert (by_voice["speaker"], by_voice["is_owner"], by_voice["ask_who"]) == ("Sen", True, False)
    other = say({"content": "Ben başkasıyım", "embedding": VOICE_B})
    assert (other["speaker"], other["is_owner"]) == ("Konuşmacı 2", False)


@pytest.mark.parametrize(
    "key",
    ["audio", "Audio", "AUDIO_B64", "audio_data", "rawAudio", "pcm16", "voice_wav", "Samples"],
)
def test_a_body_naming_audio_is_refused_whatever_the_case(api, key) -> None:
    _app, client = api
    cid = client.post("/v1/conversations", json={}).json()["id"]
    refused = client.post(f"/v1/conversations/{cid}/segments", json={"content": "x", key: "AAAA"})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "audio_refused"


@pytest.mark.parametrize(
    ("path", "body", "code"),
    [
        ("segments", {"content": "a\x00b"}, "text_invalid"),
        ("segments", {"content": 5}, "text_invalid"),
        ("segments", {"content": ["Selam"]}, "text_invalid"),
        ("segments", {"content": "x", "embedding": [0, 0, 0]}, "embedding_invalid"),
        # Python's json reads NaN and Infinity; a body carrying them is a bad vector, not a 500.
        ("segments", '{"content": "x", "embedding": [NaN, 1.0]}', "embedding_invalid"),
        ("segments", '{"content": "x", "embedding": [Infinity, 1.0]}', "embedding_invalid"),
        ("segments", {"content": "x", "embedding": "1,2"}, "embedding_invalid"),
        ("segments", {"content": "x", "is_owner": "evet"}, "is_owner_invalid"),
        ("speakers/2147483648/name", {"name": "Ahmet"}, "speaker_invalid"),
        ("speakers/0/name", {"name": "Ahmet"}, "speaker_invalid"),
        ("speakers/1/name", {"name": {"x": 1}}, "name_invalid"),
    ],
)
def test_bad_input_is_a_422_not_a_500(api, path, body, code) -> None:
    _app, client = api
    cid = client.post("/v1/conversations", json={}).json()["id"]
    client.post(
        f"/v1/conversations/{cid}/segments", json={"content": "Selam", "embedding": VOICE_A}
    )
    url = f"/v1/conversations/{cid}/{path}"
    if isinstance(body, str):
        response = client.post(url, content=body, headers={"content-type": "application/json"})
    else:
        response = client.post(url, json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == code


def test_bad_titles_and_notes_are_a_422(api) -> None:
    _app, client = api
    for title in (5, "a\x00"):
        response = client.post("/v1/conversations", json={"title": title})
        assert response.status_code == 422 and response.json()["detail"]["code"] == "title_invalid"
    cid = client.post("/v1/conversations", json={}).json()["id"]
    client.post(
        f"/v1/conversations/{cid}/segments", json={"content": "Selam", "embedding": VOICE_A}
    )
    person = client.post(f"/v1/conversations/{cid}/speakers/1/name", json={"name": "Ahmet"})
    pid = person.json()["person"]["id"]
    response = client.post(f"/v1/conversations/people/{pid}/consent", json={"note": ["x"]})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "note_invalid"


def test_a_store_failure_keeps_the_line_out_of_the_log(api, monkeypatch, capsys) -> None:
    """KVKK: the database's own error text carries the INSERT's parameters - the line itself.
    A failed write answers a sanitized error and the log names only the error's class."""
    from sqlalchemy.exc import IntegrityError as Integrity

    _app, client = api
    cid = client.post("/v1/conversations", json={}).json()["id"]
    secret_line = "Gizli konuşma metni 4f1d"

    def failing(*_args, **_kw):
        raise Integrity("INSERT INTO conversation_segments ...", {"text": secret_line}, None)

    monkeypatch.setattr(service, "add_segment", failing)
    response = client.post(f"/v1/conversations/{cid}/segments", json={"content": secret_line})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "store_conflict"
    assert secret_line not in response.text
    captured = capsys.readouterr()
    assert secret_line not in captured.out + captured.err
    assert "IntegrityError" in captured.out + captured.err


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
