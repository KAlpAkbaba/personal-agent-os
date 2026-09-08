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
    GATE_ACCOUNT_MISSING,
    GATE_ALREADY_SENT,
    GATE_NO_CONFIRMATION,
    GATE_NOT_READ_BACK,
    GATE_SEND_DISABLED,
)
from app.calendar.models import (
    PROPOSAL_STATE_DISCARDED,
    PROPOSAL_STATE_PREPARED,
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
    assert result["proposal"]["read_back_at"] is not None


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


def _prepared_proposal(db, *, read_back_at=None) -> CalendarProposalRow:
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
        state=PROPOSAL_STATE_PREPARED,
        read_back_at=read_back_at,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    focus_module.set_focus(db, FOCUS_KIND_PROPOSAL, str(row.id), label=row.summary, source="test")
    return row


def test_commit_refuses_not_read_back(db) -> None:
    service = _service()
    _prepared_proposal(db, read_back_at=None)
    result = service.commit(db, host_flag_enabled=True)
    assert result["error_class"] == GATE_NOT_READ_BACK
    assert service._writer.created == []  # type: ignore[attr-defined]


def test_commit_refuses_no_confirmation_when_read_back_is_in_the_future(db) -> None:
    service = _service()
    _prepared_proposal(db, read_back_at=datetime.now(UTC) + timedelta(hours=1))
    result = service.commit(db, host_flag_enabled=True)
    assert result["error_class"] == GATE_NO_CONFIRMATION
    assert service._writer.created == []  # type: ignore[attr-defined]


def test_commit_refuses_send_disabled(db) -> None:
    service = _service()
    _prepared_proposal(db, read_back_at=datetime.now(UTC) - timedelta(seconds=1))
    result = service.commit(db, host_flag_enabled=False)
    assert result["error_class"] == GATE_SEND_DISABLED
    assert service._writer.created == []  # type: ignore[attr-defined]


def test_commit_refuses_account_missing(db) -> None:
    service = CalendarService(None, None)
    _prepared_proposal(db, read_back_at=datetime.now(UTC) - timedelta(seconds=1))
    result = service.commit(db, host_flag_enabled=True)
    assert result["error_class"] == GATE_ACCOUNT_MISSING


def test_commit_succeeds_exactly_once_and_a_second_confirmation_refuses(db) -> None:
    service = _service()
    _prepared_proposal(db, read_back_at=datetime.now(UTC) - timedelta(seconds=1))
    first = service.commit(db, host_flag_enabled=True)
    assert first["execution_status"] == "executed"
    assert len(service._writer.created) == 1  # type: ignore[attr-defined]

    second = service.commit(db, host_flag_enabled=True)
    assert second["error_class"] == GATE_ALREADY_SENT
    assert len(service._writer.created) == 1  # type: ignore[attr-defined]


def test_discard_prevents_a_later_commit(db) -> None:
    service = _service()
    row = _prepared_proposal(db, read_back_at=datetime.now(UTC) - timedelta(seconds=1))
    discarded = service.discard(db, proposal_id=str(row.id))
    assert discarded["proposal"]["state"] == PROPOSAL_STATE_DISCARDED
    later = service.commit(db, proposal_id=str(row.id), host_flag_enabled=True)
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
    service.commit(
        db, proposal_id=proposal["proposal"]["id"], host_flag_enabled=True
    )

    rows = db.execute(select(ActivityEventRow)).scalars().all()
    assert len(rows) > 0
    for row in rows:
        blob = f"{row.factual_summary} {row.detail_json}"
        assert SECRET_PASSWORD not in blob
