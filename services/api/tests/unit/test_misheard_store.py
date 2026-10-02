"""The misheard notebook's store (misheard-ledger-store): ``app.voice.misheard.service``.

The sentence the recogniser WROTE is kept when the system did not understand it - text only,
30 days, four reasons, one writer. Nothing here goes through the relay (that is
misheard-relay-wiring): these tests hold the writer's refusals, its widths, its idempotence,
the in-process hold, the three ways a row leaves, and that no log line carries the sentence.
The clock is always passed in; nothing sleeps.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.voice.misheard import service
from app.voice.misheard.models import MisheardUtterance

HEARD = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
SENTENCE = "Ofisü bilgisayarında hesap makinesini açın"
REASONS = ("no_intent", "asked_question", "objected", "tool_failed")
#: Exactly the CONTRACT's columns - the same list in the four misheard-* cards.
COLUMNS = {
    "id",
    "heard_at",
    "sentence",
    "mode",
    "engine",
    "device_id",
    "band",
    "confidence",
    "reason",
    "resolved_intent",
    "tool",
    "session_id",
    "meant",
    "answered_at",
    "expires_at",
}


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    MisheardUtterance.__table__.create(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def empty_hold():
    service.reset_hold()
    yield
    service.reset_hold()


def _record(db: Session, **overrides):
    arguments = {
        "sentence": SENTENCE,
        "mode": "paid",
        "reason": "no_intent",
        "session_id": uuid.uuid4(),
        "heard_at": HEARD,
        "now": HEARD,
    }
    arguments.update(overrides)
    return service.record(db, **arguments)


def _count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(MisheardUtterance)).scalar_one()


def _utc(value: datetime) -> datetime:
    """SQLite hands a timestamptz back naive; the value is UTC either way."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# ------------------------------------------------------------------- the one writer


def test_the_table_has_exactly_the_contract_columns() -> None:
    assert {c.name for c in MisheardUtterance.__table__.columns} == COLUMNS
    assert MisheardUtterance.__tablename__ == "misheard_utterances"


@pytest.mark.parametrize("reason", REASONS)
def test_each_of_the_four_reasons_writes_a_row_with_every_contract_column(
    db: Session, reason: str
) -> None:
    session_id, device_id = uuid.uuid4(), uuid.uuid4()
    row = _record(
        db,
        reason=reason,
        mode="local",
        engine="chrome-web-speech",
        device_id=device_id,
        band="low",
        confidence=0.41,
        resolved_intent="app.launch",
        tool="app.launch" if reason == "tool_failed" else None,
        session_id=session_id,
    )
    assert row is not None
    stored = db.execute(select(MisheardUtterance)).scalar_one()
    assert isinstance(stored.id, uuid.UUID)
    assert _utc(stored.heard_at) == HEARD
    assert stored.sentence == SENTENCE
    assert stored.mode == "local"
    assert stored.engine == "chrome-web-speech"
    assert stored.device_id == device_id
    assert stored.band == "low"
    assert stored.confidence == pytest.approx(0.41)
    assert stored.reason == reason
    assert stored.resolved_intent == "app.launch"
    assert stored.tool == ("app.launch" if reason == "tool_failed" else None)
    assert stored.session_id == session_id
    assert stored.meant is None
    assert stored.answered_at is None
    assert _utc(stored.expires_at) == HEARD + timedelta(days=30)
    assert service.RETENTION_DAYS == 30


@pytest.mark.parametrize("reason", REASONS)
def test_listen_only_writes_nothing_for_each_reason(db: Session, reason: str) -> None:
    assert _record(db, reason=reason, listen_only=True) is None
    assert _count(db) == 0


def test_an_unknown_reason_writes_nothing(db: Session) -> None:
    assert _record(db, reason="misheard") is None
    assert _count(db) == 0


def test_an_unknown_mode_writes_nothing(db: Session) -> None:
    assert _record(db, mode="free") is None
    assert _count(db) == 0


@pytest.mark.parametrize("sentence", ["", "   ", "\n\t"])
def test_an_empty_sentence_writes_nothing(db: Session, sentence: str) -> None:
    assert _record(db, sentence=sentence) is None
    assert _count(db) == 0


def test_a_2500_character_sentence_is_stored_as_2000(db: Session) -> None:
    row = _record(db, sentence="a" * 2500, engine="e" * 100, resolved_intent="i" * 100)
    assert row is not None
    stored = db.execute(select(MisheardUtterance)).scalar_one()
    assert len(stored.sentence) == 2000
    assert len(stored.engine) == 64
    assert len(stored.resolved_intent) == 64


def test_the_same_session_and_heard_at_twice_is_one_row_with_the_first_reason(
    db: Session,
) -> None:
    session_id = uuid.uuid4()
    first = _record(db, session_id=session_id, reason="no_intent")
    second = _record(db, session_id=session_id, reason="objected")
    assert first is not None and second is not None
    assert second.id == first.id
    assert _count(db) == 1
    assert db.execute(select(MisheardUtterance.reason)).scalar_one() == "no_intent"


def test_another_heard_at_in_the_same_session_is_a_second_row(db: Session) -> None:
    session_id = uuid.uuid4()
    _record(db, session_id=session_id)
    _record(db, session_id=session_id, heard_at=HEARD + timedelta(seconds=5))
    assert _count(db) == 2


def test_a_database_fault_never_reaches_the_caller(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the database is away")

    monkeypatch.setattr(db, "flush", broken)
    assert _record(db) is None


# ------------------------------------------------------------------- is_request


@pytest.mark.parametrize(
    ("candidates", "machine_named", "expected"),
    [
        (0, False, False),
        (1, False, True),
        (0, True, True),
        (2, True, True),
    ],
)
def test_is_request_needs_a_candidate_reading_or_a_named_machine(
    candidates: int, machine_named: bool, expected: bool
) -> None:
    assert service.is_request(candidates, machine_named) is expected


# ------------------------------------------------------------------- the hold


def _entry(sentence: str = SENTENCE) -> service.HeldSentence:
    return service.HeldSentence(
        sentence=sentence,
        mode="paid",
        engine="gpt-realtime",
        device_id=None,
        band="medium",
        confidence=0.7,
        resolved_intent="mail.read",
        heard_at=HEARD,
    )


def test_the_hold_keeps_the_latest_sentence_of_a_session_for_120_seconds() -> None:
    assert service.HOLD_TTL_SECONDS == 120
    session_id = uuid.uuid4()
    service.hold(session_id, _entry(), HEARD)
    assert service.held(session_id, HEARD + timedelta(seconds=119)) == _entry()
    assert service.held(session_id, HEARD + timedelta(seconds=121)) is None
    # Gone, not merely hidden: an earlier clock does not bring it back.
    assert service.held(session_id, HEARD) is None


def test_the_hold_answers_nothing_for_another_session() -> None:
    service.hold(uuid.uuid4(), _entry(), HEARD)
    assert service.held(uuid.uuid4(), HEARD) is None


def test_a_second_hold_replaces_the_first() -> None:
    session_id = uuid.uuid4()
    service.hold(session_id, _entry("Maillerime bakın"), HEARD)
    service.hold(session_id, _entry("Ekranları kapatın"), HEARD + timedelta(seconds=1))
    held = service.held(session_id, HEARD + timedelta(seconds=2))
    assert held is not None and held.sentence == "Ekranları kapatın"


def test_reset_hold_empties_it() -> None:
    session_id = uuid.uuid4()
    service.hold(session_id, _entry(), HEARD)
    service.reset_hold()
    assert service.held(session_id, HEARD) is None


# ------------------------------------------------------------------- how a row leaves


def test_a_row_is_listed_on_day_30_and_purged_on_day_31(db: Session) -> None:
    _record(db)
    day_30 = HEARD + timedelta(days=30) - timedelta(seconds=1)
    day_31 = HEARD + timedelta(days=31)
    assert service.purge(db, day_30) == 0
    assert [item.sentence for item in service.list_items(db, day_30)] == [SENTENCE]
    assert service.purge(db, day_31) == 1
    assert _count(db) == 0
    assert service.list_items(db, day_31) == []


def test_list_items_never_returns_an_expired_row_even_when_purge_did_not_run(
    db: Session,
) -> None:
    _record(db)
    fresh = HEARD + timedelta(days=20)
    _record(db, sentence="Maillerime bakın", heard_at=fresh, now=HEARD)
    listed = service.list_items(db, HEARD + timedelta(days=31))
    assert [item.sentence for item in listed] == ["Maillerime bakın"]
    assert _count(db) == 2  # nothing was purged: the list alone hid the expired row


def test_list_items_is_newest_first(db: Session) -> None:
    for offset, sentence in enumerate(["bir", "iki", "üç"]):
        _record(db, sentence=sentence, heard_at=HEARD + timedelta(minutes=offset))
    listed = service.list_items(db, HEARD + timedelta(hours=1))
    assert [item.sentence for item in listed] == ["üç", "iki", "bir"]


def test_purge_twice_the_second_changes_nothing(db: Session) -> None:
    _record(db)
    _record(db, sentence="Maillerime bakın", heard_at=HEARD + timedelta(days=10))
    day_31 = HEARD + timedelta(days=31)
    assert service.purge(db, day_31) == 1
    before = [(r.id, r.sentence) for r in db.execute(select(MisheardUtterance)).scalars()]
    assert service.purge(db, day_31) == 0
    after = [(r.id, r.sentence) for r in db.execute(select(MisheardUtterance)).scalars()]
    assert after == before and len(after) == 1


def test_record_purges_what_has_expired(db: Session) -> None:
    _record(db)
    day_31 = HEARD + timedelta(days=31)
    _record(db, sentence="Maillerime bakın", heard_at=day_31, now=day_31)
    assert [r.sentence for r in db.execute(select(MisheardUtterance)).scalars()] == [
        "Maillerime bakın"
    ]


def test_answer_sets_meant_and_answered_at_and_keeps_the_sentence(db: Session) -> None:
    row = _record(db)
    assert row is not None
    answered_at = HEARD + timedelta(hours=3)
    answered = service.answer(db, row.id, "ofis bilgisayarımda hesap makinesini aç", answered_at)
    assert answered is not None
    stored = db.execute(select(MisheardUtterance)).scalar_one()
    assert stored.meant == "ofis bilgisayarımda hesap makinesini aç"
    assert _utc(stored.answered_at) == answered_at
    assert stored.sentence == SENTENCE
    assert _utc(stored.expires_at) == HEARD + timedelta(days=30)


def test_answer_for_an_unknown_or_expired_row_is_none(db: Session) -> None:
    assert service.answer(db, uuid.uuid4(), "postamı oku", HEARD) is None
    row = _record(db)
    assert row is not None
    assert service.answer(db, row.id, "postamı oku", HEARD + timedelta(days=31)) is None


def test_forget_one_removes_that_row_only(db: Session) -> None:
    first = _record(db)
    _record(db, sentence="Maillerime bakın", heard_at=HEARD + timedelta(minutes=1))
    assert first is not None
    assert service.forget_one(db, first.id) is True
    assert service.forget_one(db, first.id) is False
    assert [r.sentence for r in db.execute(select(MisheardUtterance)).scalars()] == [
        "Maillerime bakın"
    ]


def test_forget_all_returns_the_count_and_leaves_zero_rows(db: Session) -> None:
    for offset in range(3):
        _record(db, heard_at=HEARD + timedelta(minutes=offset))
    assert service.forget_all(db) == 3
    assert _count(db) == 0
    assert service.forget_all(db) == 0


# ------------------------------------------------------------------- wordless logs


def test_no_log_record_of_any_path_contains_the_sentence(
    db: Session, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "kimsenin görmemesi gereken cümle"
    meant = "kimsenin görmemesi gereken anlam"
    session_id = uuid.uuid4()
    with caplog.at_level(logging.DEBUG):
        row = _record(db, sentence=marker, session_id=session_id)
        assert row is not None
        _record(db, sentence=marker, session_id=session_id, reason="objected")
        _record(db, sentence=marker, listen_only=True)
        _record(db, sentence=marker, reason="misheard")
        _record(db, sentence=marker, mode="free")
        service.hold(session_id, _entry(marker), HEARD)
        service.held(session_id, HEARD)
        service.held(session_id, HEARD + timedelta(seconds=500))
        service.list_items(db, HEARD)
        service.answer(db, row.id, meant, HEARD)
        service.purge(db, HEARD)
        service.forget_one(db, row.id)
        service.forget_all(db)
        with monkeypatch.context() as patch:
            patch.setattr(db, "flush", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(marker)))
            assert _record(db, sentence=marker, heard_at=HEARD + timedelta(minutes=9)) is None
    for entry in caplog.records:
        rendered = f"{entry.getMessage()} {entry.__dict__!r} {entry.exc_text or ''}"
        assert marker not in rendered, entry
        assert meant not in rendered, entry
