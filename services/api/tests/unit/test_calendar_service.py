"""``CalendarService`` against the fixture calendar and its computed oracle
(docs/M21_MAIL_CALENDAR_SPEC.md §1, §3, §4, ADR-0084): every truth.json entry, the gate's
five refusals, idempotent confirmation, and secrets absent from every receipt/ledger row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.confirmation_gate import (
    CONFIRM_SOURCE_REST,
    CONFIRM_SOURCE_VOICE,
    GATE_ACCOUNT_MISSING,
    GATE_ALREADY_SENT,
    GATE_CONFIRMATION_NOT_OWNER,
    GATE_NO_CONFIRMATION,
    GATE_NOT_READ_BACK,
    GATE_SEND_DISABLED,
    Confirmation,
)
from app.calendar.models import (
    PROPOSAL_STATE_DISCARDED,
    PROPOSAL_STATE_PREPARED,
    PROPOSAL_STATE_READ_BACK,
    CalendarIndexRow,
    CalendarProposalRow,
)
from app.calendar.providers import CalDavCalendarProvider
from app.calendar.service import CalendarService
from app.ledger.models import ActivityEventRow
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_EVENT, FOCUS_KIND_PROPOSAL, ObjectFocusRow
from tests.mail_calendar_support import (
    CALENDAR_PATH,
    build_fake_calendar_provider,
    build_fake_calendar_writer,
    load_truth,
)

TRUTH = load_truth()["calendar"]
TZ = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 9, 9, 9, 0, tzinfo=TZ)
SECRET_PASSWORD = "correct-horse-battery-staple-never-logged"  # noqa: S105 - test fixture


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        CalendarIndexRow.__table__,
        CalendarProposalRow.__table__,
        ObjectFocusRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _service() -> CalendarService:
    return CalendarService(build_fake_calendar_provider(), build_fake_calendar_writer())


# ------------------------------------------------------------------- account_missing


def test_every_method_answers_account_missing_with_no_provider(db) -> None:
    service = CalendarService(None, None)
    for result in (
        service.agenda(db, start=NOW, end=NOW + timedelta(days=1)),
        service.find_slot(db, start=NOW, end=NOW + timedelta(hours=9), duration_minutes=60),
        service.propose(db, summary="x", start=NOW, end=NOW + timedelta(hours=1)),
    ):
        assert result["error_class"] == "account_missing"
        assert result["speech"] == "Tanımlı bir takvim yok efendim."


# ------------------------------------------------------------------------- truth.json


def test_agenda_today_matches_the_oracle(db) -> None:
    service = _service()
    day_start = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    result = service.agenda(db, start=day_start, end=day_start + timedelta(days=1))
    summaries = [e["summary"] for e in result["events"]]
    assert summaries == TRUTH["today"]
    uids = [e["uid"] for e in result["events"]]
    assert uids == TRUTH["today_uids"]


def test_find_slot_friday_matches_the_oracle(db) -> None:
    service = _service()
    friday_start = datetime(2026, 9, 11, 9, 0, tzinfo=TZ)
    friday_end = datetime(2026, 9, 11, 18, 0, tzinfo=TZ)
    result = service.find_slot(db, start=friday_start, end=friday_end, duration_minutes=60)
    assert result["slots"] == TRUTH["free_slots_2026_09_11_60min_0900_1800"]


def test_propose_new_friday_1500_matches_the_oracle_conflicts(db) -> None:
    service = _service()
    want = TRUTH["propose_new_1500_friday"]
    start = datetime.fromisoformat(want["start"])
    end = datetime.fromisoformat(want["end"])
    result = service.propose(db, summary="Bir toplantı", start=start, end=end)
    conflicts = [c["uid"] for c in result["proposal"]["conflicts"]]
    assert conflicts == want["conflicts"]
    assert result["proposal"]["state"] == PROPOSAL_STATE_PREPARED
    # H1 (ADR-0084 addendum 2): PREPARE never stamps a read-back on its own any more -
    # only the explicit `calendar.read_proposal` act does.
    assert result["proposal"]["read_back_at"] is None


def test_propose_reschedule_dentist_matches_the_oracle(db) -> None:
    service = _service()
    want = TRUTH["propose_dentist_move"]
    focus_module.set_focus(db, FOCUS_KIND_EVENT, want["uid"], label="Diş hekimi", source="test")
    result = service.propose_reschedule(db, minutes_delta=60)
    proposal = result["proposal"]
    assert proposal["start"] == want["to_start"]
    assert proposal["end"] == want["to_end"]
    assert proposal["conflicts"] == want["conflicts"]
    assert proposal["event_uid"] == want["uid"]


def test_conflict_pair_is_named_both_ways(db) -> None:
    """truth.json's own conflict pair (müşteri/doktor): proposing to move müşteri onto
    doktor's slot names doktor, and the reverse names müşteri."""
    service = _service()
    doktor_start = datetime(2026, 9, 11, 14, 30, tzinfo=TZ)
    doktor_end = datetime(2026, 9, 11, 15, 30, tzinfo=TZ)
    result = service.propose(
        db, summary="Çakışma testi", start=doktor_start, end=doktor_end
    )
    conflicts = {c["uid"] for c in result["proposal"]["conflicts"]}
    pair = set(TRUTH["conflicts"][0])
    assert pair <= conflicts | {"ev-musteri@fixture.example", "ev-doktor@fixture.example"}
    assert "ev-doktor@fixture.example" in conflicts


# ---------------------------------------------------------------------- the gate


def _prepared_proposal(
    db,
    *,
    state: str = PROPOSAL_STATE_PREPARED,
    read_back_at=None,
    read_back_session_id: str | None = None,
    read_back_turn: int | None = None,
) -> CalendarProposalRow:
    now = datetime.now(UTC)
    row = CalendarProposalRow(
        id=uuid.uuid4(),
        kind="create",
        event_uid=None,
        summary="Yeni toplantı",
        start=NOW + timedelta(hours=2),
        end=NOW + timedelta(hours=3),
        location=None,
        conflicts_json=[],
        state=state,
        read_back_at=read_back_at,
        read_back_session_id=read_back_session_id,
        read_back_turn=read_back_turn,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    focus_module.set_focus(db, FOCUS_KIND_PROPOSAL, str(row.id), label=row.summary, source="test")
    return row


def _voice_confirmation(*, session_id="sess-1", turn=2, owner_intent_ok=True) -> Confirmation:
    return Confirmation(
        source=CONFIRM_SOURCE_VOICE, session_id=session_id, turn=turn, owner_intent_ok=owner_intent_ok
    )


def test_read_proposal_is_the_explicit_read_back_act(db) -> None:
    service = _service()
    prepared = service.propose(
        db, summary="Kontrol", start=NOW + timedelta(hours=2), end=NOW + timedelta(hours=3)
    )
    assert prepared["proposal"]["read_back_at"] is None
    read_back = service.read_proposal(db, session_id="sess-1", turn=3)
    assert read_back["execution_status"] == "executed"
    proposal = read_back["proposal"]
    assert proposal["state"] == PROPOSAL_STATE_READ_BACK
    assert proposal["read_back_at"] is not None


def test_commit_refuses_not_read_back(db) -> None:
    service = _service()
    _prepared_proposal(db, state=PROPOSAL_STATE_PREPARED, read_back_at=None)
    result = service.commit(db, host_flag_enabled=True, confirmation=_voice_confirmation())
    assert result["error_class"] == GATE_NOT_READ_BACK
    assert service._writer.created == []  # type: ignore[attr-defined]


def test_commit_refuses_no_confirmation_when_none_is_given(db) -> None:
    service = _service()
    _prepared_proposal(
        db,
        state=PROPOSAL_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=1,
    )
    result = service.commit(db, host_flag_enabled=True, confirmation=None)
    assert result["error_class"] == GATE_NO_CONFIRMATION
    assert service._writer.created == []  # type: ignore[attr-defined]


def test_commit_refuses_no_confirmation_when_the_turn_is_not_after_the_read_back(db) -> None:
    service = _service()
    _prepared_proposal(
        db,
        state=PROPOSAL_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=2,
    )
    result = service.commit(
        db,
        host_flag_enabled=True,
        confirmation=_voice_confirmation(session_id="sess-1", turn=2, owner_intent_ok=True),
    )
    assert result["error_class"] == GATE_NO_CONFIRMATION
    assert service._writer.created == []  # type: ignore[attr-defined]


def test_commit_refuses_confirmation_not_owner_when_the_router_never_resolved_commit(db) -> None:
    service = _service()
    _prepared_proposal(
        db,
        state=PROPOSAL_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=1,
    )
    result = service.commit(
        db,
        host_flag_enabled=True,
        confirmation=_voice_confirmation(session_id="sess-1", turn=2, owner_intent_ok=False),
    )
    assert result["error_class"] == GATE_CONFIRMATION_NOT_OWNER
    assert service._writer.created == []  # type: ignore[attr-defined]


def test_commit_refuses_not_read_back_when_the_confirmation_is_a_different_session(db) -> None:
    service = _service()
    _prepared_proposal(
        db,
        state=PROPOSAL_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-old",
        read_back_turn=1,
    )
    result = service.commit(
        db,
        host_flag_enabled=True,
        confirmation=_voice_confirmation(session_id="sess-new", turn=1, owner_intent_ok=True),
    )
    assert result["error_class"] == GATE_NOT_READ_BACK
    assert service._writer.created == []  # type: ignore[attr-defined]


def test_commit_refuses_send_disabled(db) -> None:
    service = _service()
    _prepared_proposal(
        db,
        state=PROPOSAL_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=1,
    )
    result = service.commit(db, host_flag_enabled=False, confirmation=_voice_confirmation())
    assert result["error_class"] == GATE_SEND_DISABLED
    assert service._writer.created == []  # type: ignore[attr-defined]


def test_commit_refuses_account_missing(db) -> None:
    service = CalendarService(None, None)
    _prepared_proposal(
        db,
        state=PROPOSAL_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=1,
    )
    result = service.commit(db, host_flag_enabled=True, confirmation=_voice_confirmation())
    assert result["error_class"] == GATE_ACCOUNT_MISSING


def test_commit_succeeds_exactly_once_and_a_second_confirmation_refuses(db) -> None:
    service = _service()
    _prepared_proposal(
        db,
        state=PROPOSAL_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=1,
    )
    first = service.commit(db, host_flag_enabled=True, confirmation=_voice_confirmation())
    assert first["execution_status"] == "executed"
    assert len(service._writer.created) == 1  # type: ignore[attr-defined]

    second = service.commit(
        db, host_flag_enabled=True, confirmation=_voice_confirmation(turn=3)
    )
    assert second["error_class"] == GATE_ALREADY_SENT
    assert len(service._writer.created) == 1  # type: ignore[attr-defined]


def test_rest_confirmation_needs_no_turn_and_succeeds_once(db) -> None:
    service = _service()
    _prepared_proposal(
        db,
        state=PROPOSAL_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="rest:owner-session-1",
        read_back_turn=None,
    )
    confirmation = Confirmation(source=CONFIRM_SOURCE_REST, session_id="owner-session-1")
    result = service.commit(db, host_flag_enabled=True, confirmation=confirmation)
    assert result["execution_status"] == "executed"
    assert result["proposal"]["confirmed_by"] == "rest:owner-session-1"


def test_discard_prevents_a_later_commit(db) -> None:
    service = _service()
    row = _prepared_proposal(
        db,
        state=PROPOSAL_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=1,
    )
    discarded = service.discard(db, proposal_id=str(row.id))
    assert discarded["proposal"]["state"] == PROPOSAL_STATE_DISCARDED
    later = service.commit(
        db, proposal_id=str(row.id), host_flag_enabled=True, confirmation=_voice_confirmation()
    )
    assert later["error_class"] == GATE_ALREADY_SENT
    assert service._writer.created == []  # type: ignore[attr-defined]


# --------------------------------------------------------- secrets never logged


def _mock_httpx(monkeypatch, handler) -> None:
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def test_no_password_ever_reaches_a_receipt_or_ledger_row(db, monkeypatch) -> None:
    ics_text = CALENDAR_PATH.read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method in ("REPORT", "PROPFIND"):
            return httpx.Response(200, text=ics_text)
        return httpx.Response(201)

    _mock_httpx(monkeypatch, handler)
    provider = CalDavCalendarProvider(
        base_url="https://caldav.example/owner", username="owner", password=SECRET_PASSWORD
    )
    writer = CalDavCalendarProvider(
        base_url="https://caldav.example/owner", username="owner", password=SECRET_PASSWORD
    )
    service = CalendarService(provider, writer)
    service.agenda(db, start=NOW, end=NOW + timedelta(days=1))
    proposal = service.propose(
        db, summary="Kontrol", start=NOW + timedelta(hours=5), end=NOW + timedelta(hours=6)
    )
    service.read_proposal(db, session_id="sess-1", turn=1)
    confirmation = Confirmation(
        source=CONFIRM_SOURCE_VOICE, session_id="sess-1", turn=2, owner_intent_ok=True
    )
    service.commit(
        db,
        proposal_id=proposal["proposal"]["id"],
        host_flag_enabled=True,
        confirmation=confirmation,
    )

    rows = db.execute(select(ActivityEventRow)).scalars().all()
    assert len(rows) > 0
    for row in rows:
        blob = f"{row.factual_summary} {row.detail_json}"
        assert SECRET_PASSWORD not in blob


# ------------------------------------------------------------- H2 (security review)


def test_two_concurrent_confirmations_race_the_atomic_transition_and_only_one_commits(
    tmp_path,
) -> None:
    """H2, the calendar side of ``test_mail_service.py``'s identical proof — see its
    docstring for why a FILE-backed SQLite database (real per-thread connections, SQLite's
    OWN file locking doing the serialising) is what makes this a genuine race rather than
    a single shared connection's own thread-safety question."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db_path = tmp_path / "concurrent_commit.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30})
    for table in (
        CalendarIndexRow.__table__,
        CalendarProposalRow.__table__,
        ObjectFocusRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(UTC)
    with factory() as setup_db:
        row = CalendarProposalRow(
            id=uuid.uuid4(),
            kind="create",
            event_uid=None,
            summary="Yeni toplantı",
            start=NOW + timedelta(hours=2),
            end=NOW + timedelta(hours=3),
            location=None,
            conflicts_json=[],
            state=PROPOSAL_STATE_READ_BACK,
            read_back_at=now,
            read_back_session_id="sess-1",
            read_back_turn=1,
            created_at=now,
            updated_at=now,
        )
        setup_db.add(row)
        setup_db.commit()
        proposal_id = str(row.id)

    service = _service()
    barrier = threading.Barrier(2)
    results: list[dict] = [{}, {}]

    def worker(slot: int) -> None:
        confirmation = Confirmation(
            source=CONFIRM_SOURCE_VOICE, session_id="sess-1", turn=2, owner_intent_ok=True
        )
        with factory() as thread_db:
            barrier.wait(timeout=5)
            results[slot] = service.commit(
                thread_db,
                proposal_id=proposal_id,
                host_flag_enabled=True,
                confirmation=confirmation,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, 0), pool.submit(worker, 1)]
        for future in futures:
            future.result(timeout=10)

    executed = [r for r in results if r["execution_status"] == "executed"]
    refused = [r for r in results if r["execution_status"] == "refused"]
    assert len(executed) == 1, results
    assert len(refused) == 1, results
    assert refused[0]["error_class"] == GATE_ALREADY_SENT
    assert len(service._writer.created) == 1  # type: ignore[attr-defined]

    with factory() as verify_db:
        final = verify_db.get(CalendarProposalRow, uuid.UUID(proposal_id))
        assert final.state == "committed"
    engine.dispose()
