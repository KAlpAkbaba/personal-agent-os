"""B20 req 238: the voice history says what the conversation was.

The matrix files this as "defter çift yazıyor" — the ledger writes every voice session
twice. That was true when it was written and was fixed by requirement 70 in B06:
`voice_session_source_ref` gives the live writer and the backfill ONE natural key, and
`_voice_live_already_recorded` is the backfill asking whether live already covered the
session. Those halves are tested in `test_ledger_service.py`; the first test here asserts
the property end to end, through a real session lifecycle rather than through the guard.

What was NOT true is the rest of the requirement. The close AUDIT row has carried
`lifetime_ms` and `barge_in_count` since M16, and the LEDGER row — the surface the owner
reads — carried a reason code and nothing else. An activity history made of "Sesli oturum
kapandı." cannot answer "how long did we talk this morning", which is the only question
anyone asks a voice history. The numbers were computed two statements earlier and dropped.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.broker.models import AuditEvent
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_VOICE_SESSION_CLOSED,
    EVENT_TYPE_VOICE_SESSION_CREATED,
    SUBSYSTEM_VOICE,
)
from app.voice.realtime_sessions import service as rt
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall


#: `close_session` reads the clock itself (it is the moment the session ended, and there is
#: no injection point), so these ages are measured back from the real now rather than from
#: a fixed instant the production code has never heard of.
def _ago(**delta: float) -> datetime:
    return datetime.now(UTC) - timedelta(**delta)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        RealtimeSessionRow.__table__,
        RealtimeToolCall.__table__,
        ActivityEventRow.__table__,
        AuditEvent.__table__,
    ):
        table.create(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s


def _session_row(
    session, *, created_at: datetime | None = None, barge_ins: int = 0
) -> RealtimeSessionRow:
    created_at = created_at or datetime.now(UTC)
    row = RealtimeSessionRow(
        provider="openai-realtime",
        transport="webrtc",
        client_kind="web",
        owner_session_id=uuid.uuid4(),
        language="tr-TR",
        state="active",
        created_at=created_at,
        updated_at=created_at,
        context_json={"barge_in_count": barge_ins} if barge_ins else {},
    )
    session.add(row)
    session.commit()
    return row


def _voice_rows(session, event_type: str) -> list[ActivityEventRow]:
    return [
        r
        for r in session.query(ActivityEventRow).all()
        if r.subsystem == SUBSYSTEM_VOICE and r.event_type == event_type
    ]


def test_closing_a_session_writes_exactly_one_history_row(session):
    row = _session_row(session)

    rt.close_session(session, row, reason="client_closed")
    # Closing again is a no-op: the state guard, and therefore the history, is idempotent.
    rt.close_session(session, row, reason="client_closed")

    assert len(_voice_rows(session, EVENT_TYPE_VOICE_SESSION_CLOSED)) == 1


def test_the_history_row_carries_what_the_audit_row_always_had(session):
    row = _session_row(session, created_at=_ago(minutes=12, seconds=30), barge_ins=3)
    for i in range(2):
        session.add(
            RealtimeToolCall(
                session_id=row.id,
                call_id=f"call-{i}",
                name="research.start",
                arguments_json={},
                status="succeeded",
            )
        )
    session.commit()

    rt.close_session(session, row, reason="client_closed")

    (closed,) = _voice_rows(session, EVENT_TYPE_VOICE_SESSION_CLOSED)
    detail = closed.detail_json or {}
    assert detail["reason"] == "client_closed"
    assert detail["barge_in_count"] == 3
    assert detail["tool_calls"] == 2
    assert detail["provider"] == "openai-realtime"
    assert detail["transport"] == "webrtc"
    # The lifetime is READ from the row's own timestamps, not estimated.
    assert 12 * 60_000 <= detail["lifetime_ms"] <= 13 * 60_000


def test_the_sentence_a_person_reads_carries_the_numbers(session):
    row = _session_row(session, created_at=_ago(minutes=12, seconds=30), barge_ins=3)
    session.add(
        RealtimeToolCall(
            session_id=row.id, call_id="c1", name="clock.now", arguments_json={}, status="succeeded"
        )
    )
    session.commit()

    rt.close_session(session, row, reason="client_closed")

    (closed,) = _voice_rows(session, EVENT_TYPE_VOICE_SESSION_CLOSED)
    assert closed.factual_summary.startswith("Sesli oturum kapandı (12 dk")
    assert "3 söze girme" in closed.factual_summary
    assert "1 araç çağrısı" in closed.factual_summary


def test_a_short_quiet_session_says_so_without_inventing_zeroes(session):
    row = _session_row(session, created_at=_ago(seconds=8))

    rt.close_session(session, row, reason="client_closed")

    (closed,) = _voice_rows(session, EVENT_TYPE_VOICE_SESSION_CLOSED)
    # No barge-ins and no tools: the sentence does not list them as zero.
    assert "söze girme" not in closed.factual_summary
    assert "araç" not in closed.factual_summary
    assert "sn)" in closed.factual_summary


def test_creating_a_session_still_writes_its_own_row(session):
    # The close row got richer; the other two states are unchanged.
    row = _session_row(session)
    rt._ledger(session, "created", row, detail={"provider": row.provider})

    (created,) = _voice_rows(session, EVENT_TYPE_VOICE_SESSION_CREATED)
    assert created.factual_summary == "Sesli oturum oluşturuldu."
    assert (created.detail_json or {})["provider"] == "openai-realtime"
