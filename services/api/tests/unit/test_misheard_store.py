"""The misheard notebook's store (misheard-ledger-store): ``app.voice.misheard.service``.

The sentence the recogniser WROTE is kept when the system did not understand it - text only,
30 days, four reasons, one writer. Nothing here goes through the relay (that is
misheard-relay-wiring): these tests hold the writer's refusals, its widths, its idempotence,
the in-process hold, the three ways a row leaves, and that no log line carries the sentence.
The clock is always passed in; nothing sleeps.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import structlog
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.voice.misheard import service
from app.voice.misheard.models import MisheardUtterance

HEARD = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
SECOND = timedelta(seconds=1)
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

    with monkeypatch.context() as patch:
        patch.setattr(db, "flush", broken)
        assert _record(db) is None
    # The session is the caller's: it writes again once the fault is over (that PostgreSQL
    # keeps the TRANSACTION usable is tests/integration/test_misheard_postgres.py's claim).
    assert _record(db) is not None
    assert _count(db) == 1


def test_a_200_character_tool_is_stored_as_64(db: Session) -> None:
    row = _record(db, reason="tool_failed", tool="t" * 200)
    assert row is not None
    assert db.execute(select(MisheardUtterance.tool)).scalar_one() == "t" * 64


@pytest.mark.parametrize("reason", ["no_intent", "asked_question", "objected"])
def test_a_tool_is_kept_for_tool_failed_only(db: Session, reason: str) -> None:
    assert _record(db, reason=reason, tool="app.launch") is not None
    assert db.execute(select(MisheardUtterance.tool)).scalar_one() is None


def test_an_unknown_band_and_a_confidence_that_is_no_number_are_stored_as_null(
    db: Session,
) -> None:
    row = _record(db, band="certain", confidence=float("nan"))
    assert row is not None
    stored = db.execute(select(MisheardUtterance)).scalar_one()
    assert stored.band is None
    assert stored.confidence is None
    assert stored.sentence == SENTENCE  # the sentence is what the notebook is for


def test_without_a_session_nothing_is_written(db: Session) -> None:
    assert _record(db, session_id=None) is None
    assert _count(db) == 0


# ------------------------------------------------------------------- is_request


@pytest.mark.parametrize(
    ("candidates", "machine_named", "expected"),
    [
        (0, False, False),
        (1, False, True),
        (0, True, True),
        (2, True, True),
        # The relay may hand the readings themselves instead of their count.
        ([], False, False),
        (["app.launch"], False, True),
        ((), True, True),
        (("app.launch", "mail.read"), True, True),
    ],
)
def test_is_request_needs_a_candidate_reading_or_a_named_machine(
    candidates: object, machine_named: bool, expected: bool
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


def test_the_hold_is_gone_at_exactly_120_seconds() -> None:
    session_id = uuid.uuid4()
    service.hold(session_id, _entry(), HEARD)
    assert service.held(session_id, HEARD + timedelta(seconds=120)) is None


def test_an_expired_hold_leaves_memory_for_every_session_not_only_the_one_asking() -> None:
    silent, talking = uuid.uuid4(), uuid.uuid4()
    service.hold(silent, _entry(), HEARD)
    service.hold(talking, _entry("Ekranları kapatın"), HEARD + timedelta(seconds=300))
    assert list(service._hold) == [str(talking)]


def test_a_held_sentence_does_not_show_its_words_when_printed() -> None:
    entry = _entry()
    assert SENTENCE not in repr(entry) and SENTENCE not in str(entry)
    assert "medium" in repr(entry)


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


# ------------------------------------------------------------------- the lifespan's purge


def _scope(db: Session):
    @contextlib.contextmanager
    def scope() -> Iterator[Session]:
        yield db

    return scope


class _NoRows:
    """A session that deletes nothing: the loop's cadence without a database under it."""

    rowcount = 0

    def execute(self, *args: object, **kwargs: object) -> _NoRows:
        return self

    def commit(self) -> None:
        return None


async def _until(condition) -> None:
    """Wait for an event the loop produces; the ceiling is a hang guard, not the claim."""
    async with asyncio.timeout(30):
        while not condition():
            await asyncio.sleep(0.005)


async def test_the_purge_loop_purges_once_at_start_and_is_cancelled_cleanly(db: Session) -> None:
    assert service.PURGE_INTERVAL_SECONDS == 24 * 60 * 60
    _record(db)
    _record(db, sentence="Maillerime bakın", heard_at=HEARD + timedelta(days=10))
    loop = service.PurgeLoop(_scope(db), clock=lambda: HEARD + timedelta(days=31))
    assert loop._interval_s == service.PURGE_INTERVAL_SECONDS
    await loop.start()
    await _until(lambda: loop.passes >= 1)
    # The next pass is 24 h away, so the session is this thread's again.
    assert loop.running is True
    assert loop.last_removed == 1
    assert [r.sentence for r in db.execute(select(MisheardUtterance)).scalars()] == [
        "Maillerime bakın"
    ]
    task = loop._task
    await loop.stop()
    assert loop.running is False
    assert task is not None and task.cancelled()
    assert loop.passes == 1
    await loop.stop()  # a second stop is nothing


async def test_the_purge_loop_passes_again_after_its_interval() -> None:
    @contextlib.contextmanager
    def scope() -> Iterator[_NoRows]:
        yield _NoRows()

    loop = service.PurgeLoop(scope, interval_s=0)
    await loop.start()
    try:
        await _until(lambda: loop.passes >= 3)
        assert loop.running is True
    finally:
        await loop.stop()
    assert loop.running is False


async def test_a_purge_pass_that_fails_does_not_end_the_loop() -> None:
    def scope():
        raise RuntimeError("the database is away")

    loop = service.PurgeLoop(scope, interval_s=0)
    await loop.start()
    try:
        await _until(lambda: loop.passes >= 2)
        assert loop.running is True
        assert loop.last_removed is None
    finally:
        await loop.stop()


HEALTH_KEYS = {
    "status",
    "running",
    "interval_s",
    "passes",
    "failures",
    "last_pass_at",
    "last_error",
    "required",
    "last_removed",
    "retention_days",
}


def test_a_purge_loop_that_was_never_started_is_skipped_in_health() -> None:
    @contextlib.contextmanager
    def scope() -> Iterator[_NoRows]:
        yield _NoRows()

    health = service.PurgeLoop(scope).health_check()
    assert set(health) == HEALTH_KEYS
    assert health["status"] == "skipped"
    assert health["running"] is False
    assert health["required"] is False
    assert health["retention_days"] == 30
    assert health["interval_s"] == 24 * 60 * 60


async def test_a_started_purge_loop_answers_ok_and_one_that_is_behind_is_reported(
    db: Session,
) -> None:
    """Advisory: three missed intervals are said out loud ("fail") and never required."""
    moment = [HEARD + timedelta(days=31)]
    _record(db)
    loop = service.PurgeLoop(_scope(db), clock=lambda: moment[0])
    await loop.start()
    try:
        health = loop.health_check()
        assert set(health) == HEALTH_KEYS
        assert health["status"] == "ok"
        assert health["running"] is True
        assert health["passes"] == 1 and health["failures"] == 0
        assert health["last_removed"] == 1
        assert health["last_pass_at"] == "2026-11-02T09:00:00Z"
        assert health["last_error"] is None
        moment[0] += timedelta(hours=71)
        assert loop.health_check()["status"] == "ok"
        moment[0] += timedelta(hours=2)
        behind = loop.health_check()
        assert behind["status"] == "fail"
        assert behind["required"] is False
    finally:
        await loop.stop()
    stopped = loop.health_check()
    assert stopped["status"] == "skipped" and stopped["running"] is False


def test_a_failed_pass_is_counted_in_health_by_its_type_and_never_by_its_text() -> None:
    """A database error's text carries the statement's parameters - here, the sentence -
    and /v1/system/health is the one endpoint that answers without an owner session."""

    def away():
        raise RuntimeError(f"DELETE ... {MARKER}")

    loop = service.PurgeLoop(away)
    loop.purge_once()
    health = loop.health_check()
    assert health["failures"] == 1 and health["passes"] == 0
    assert health["last_error"] == "RuntimeError"
    assert health["last_removed"] is None
    assert MARKER not in repr(health)


def test_a_purge_pass_drops_the_holds_that_have_expired() -> None:
    session_id = uuid.uuid4()
    service.hold(session_id, _entry(), HEARD)

    @contextlib.contextmanager
    def scope() -> Iterator[_NoRows]:
        yield _NoRows()

    service.PurgeLoop(scope, clock=lambda: HEARD + timedelta(seconds=121)).purge_once()
    assert service._hold == {}


# ------------------------------------------------------------------- wordless logs

#: ASCII on purpose: the house renderer is JSON, which writes "ü" as "ü" - a Turkish
#: marker would be invisible in the printed line even when the sentence is right there.
MARKER = "GIZLICUMLE7F3A"
MEANT_MARKER = "GIZLIANLAM9C1D"


def _walk_every_path(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    sentence = f"Ofisü bilgisayarında {MARKER} açın"
    meant = f"ofis bilgisayarımda {MEANT_MARKER} aç"
    session_id = uuid.uuid4()
    row = _record(db, sentence=sentence, session_id=session_id)
    assert row is not None
    _record(db, sentence=sentence, session_id=session_id, reason="objected")
    _record(db, sentence=sentence, listen_only=True)
    _record(db, sentence=sentence, reason="misheard")
    _record(db, sentence=sentence, mode="free")
    _record(db, sentence=sentence, reason="tool_failed", tool=MARKER, heard_at=HEARD + SECOND)
    entry = _entry(sentence)
    service.hold(session_id, entry, HEARD)
    service.held(session_id, HEARD)
    service.held(session_id, HEARD + timedelta(seconds=500))
    service.list_items(db, HEARD)
    service.answer(db, row.id, meant, HEARD)
    service.purge(db, HEARD)
    service.PurgeLoop(_scope(db), clock=lambda: HEARD + timedelta(days=31)).purge_once()

    def away():
        raise RuntimeError(sentence)

    service.PurgeLoop(away).purge_once()
    service.forget_one(db, row.id)
    service.forget_all(db)
    with monkeypatch.context() as patch:
        # A database error's text carries the statement's parameters - here, the sentence.
        patch.setattr(db, "flush", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(sentence)))
        assert _record(db, sentence=sentence, heard_at=HEARD + timedelta(minutes=9)) is None


def test_no_structured_log_event_of_any_path_contains_the_sentence(
    db: Session, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The house logger is structlog (``app.logging.get_logger``), which a stdlib handler
    never sees; both are read here."""
    with structlog.testing.capture_logs() as events, caplog.at_level(logging.DEBUG):
        _walk_every_path(db, monkeypatch)
    # Not vacuous: the fault and the failed pass DID log, through the logger that is read.
    names = [event["event"] for event in events]
    assert "misheard_record_failed" in names and "misheard_purge_failed" in names, names
    rendered = repr(events) + "".join(
        f"{entry.getMessage()} {entry.__dict__!r} {entry.exc_text or ''}"
        for entry in caplog.records
    )
    assert MARKER not in rendered
    assert MEANT_MARKER not in rendered


def test_no_printed_log_line_of_any_path_contains_the_sentence(
    db: Session, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same paths as production prints them: the house configuration's processors and
    renderer, writing to a buffer instead of the process's stdout."""
    import app.logging as house

    printed = io.StringIO()
    was_configured, before = structlog.is_configured(), structlog.get_config()
    house.configure_logging()
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(printed))
    try:
        _walk_every_path(db, monkeypatch)
    finally:
        if was_configured:
            structlog.configure(**before)
        else:
            structlog.reset_defaults()
    lines = printed.getvalue()
    assert "misheard_record_failed" in lines and "misheard_purge_failed" in lines, lines
    captured = capsys.readouterr()
    everything = lines + captured.out + captured.err
    assert MARKER not in everything
    assert MEANT_MARKER not in everything
