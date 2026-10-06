"""inbound-calls-bridge on the REAL database: finalize and the daily allowance over PostgreSQL.

What SQLite cannot say and PostgreSQL does: the ledger's ``(source, source_ref)`` uniqueness
and the closed vocabulary are the ones the migrations made, the JSON detail round-trips through
``jsonb``, and timestamptz comes back aware for the Istanbul day boundary. No new table, no
migration: the rows land in ``activity_events`` and ``notifications``.

Each test uses its own CallSids (a uuid in them) and deletes exactly its own rows afterwards;
nothing else in the database is touched.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.ledger.models import ActivityEventRow
from app.notifications.models import NotificationRow
from app.telephony import inbound_bridge as br
from app.telephony import inbound_twilio as tw
from app.telephony.inbound_records import NOTIFICATION_KIND, InboundRecorder, day_start
from app.telephony.inbound_settings import InboundSettings

pytestmark = pytest.mark.integration

CALLER = "+905321112233"
#: 14:00 in Istanbul, far in the future so no real row shares its day.
NOON = datetime(2031, 3, 4, 11, 0, tzinfo=UTC)


@pytest.fixture()
def scope() -> Iterator[contextlib.AbstractContextManager[Session]]:
    engine = build_engine(Settings().database_url)
    factory = build_session_factory(engine)

    @contextlib.contextmanager
    def session_scope() -> Iterator[Session]:
        with factory() as db:
            yield db

    yield session_scope
    engine.dispose()


@pytest.fixture()
def tag(scope) -> Iterator[str]:
    marker = uuid.uuid4().hex[:12]
    yield marker
    with scope() as db:
        db.execute(
            delete(ActivityEventRow).where(
                ActivityEventRow.source == "telephony",
                ActivityEventRow.source_ref.like(f"inbound:CA{marker}%"),
            )
        )
        db.execute(
            delete(NotificationRow).where(
                NotificationRow.kind == NOTIFICATION_KIND,
                NotificationRow.data_json["call_sid"].as_string().like(f"CA{marker}%"),
            )
        )
        # the day-cap test's rows sit on a day nobody else uses
        db.execute(
            delete(ActivityEventRow).where(
                ActivityEventRow.event_type == "telephony.inbound_answered",
                ActivityEventRow.occurred_at >= day_start(NOON),
                ActivityEventRow.occurred_at < day_start(NOON) + timedelta(days=1),
            )
        )
        db.commit()


def _counts(scope, call_sid: str) -> tuple[int, int]:
    with scope() as db:
        ledger = db.execute(
            select(func.count())
            .select_from(ActivityEventRow)
            .where(ActivityEventRow.source_ref == f"inbound:{call_sid}")
        ).scalar_one()
        notes = db.execute(
            select(func.count())
            .select_from(NotificationRow)
            .where(NotificationRow.data_json["call_sid"].as_string() == call_sid)
        ).scalar_one()
    return int(ledger), int(notes)


def test_finalize_writes_one_row_and_one_notification_and_a_second_adds_nothing(
    scope, tag: str
) -> None:
    call_sid = f"CA{tag}" + "0" * 20
    recorder = InboundRecorder(scope)
    lines = [br.TranscriptLine("Arayan", "Ben Zeynep, akşam arasın.", NOON)]
    assert recorder.finalize(call_sid, CALLER, NOON, NOON + timedelta(seconds=75), lines)
    assert not recorder.finalize(call_sid, CALLER, NOON, NOON + timedelta(seconds=80), lines)
    assert _counts(scope, call_sid) == (1, 1)
    with scope() as db:
        row = db.execute(
            select(ActivityEventRow).where(ActivityEventRow.source_ref == f"inbound:{call_sid}")
        ).scalar_one()
        note = db.execute(
            select(NotificationRow).where(NotificationRow.data_json["call_sid"].as_string() == call_sid)
        ).scalar_one()
    assert row.event_type == "telephony.inbound_answered"
    assert row.detail_json["duration_s"] == 75
    assert "Arayan: Ben Zeynep" in row.detail_json["transcript"]
    assert row.occurred_at.tzinfo is not None
    assert note.title == f"{CALLER} aradı" and note.body == "Ben Zeynep, akşam arasın."


def test_the_daily_allowance_is_read_from_the_real_ledger(scope, tag: str) -> None:
    recorder = InboundRecorder(scope)
    for n in range(3):
        start = NOON - timedelta(hours=3) + timedelta(minutes=15 * n)
        recorder.finalize(f"CA{tag}{n:020d}", CALLER, start, start + timedelta(minutes=12), [])
    assert recorder.seconds_used_today(NOON) == 3 * 12 * 60

    line = br.InboundLine(
        settings=InboundSettings(
            enabled=True,
            public_base_url="https://jarvis.tail1234.ts.net:8443",
            twilio_auth_token="t" * 32,
            openai_api_key="sk-test",
        ),
        provider=tw.TwilioInbound("t" * 32),
        recorder=recorder,
        leg_factory=lambda: None,  # type: ignore[arg-type,return-value]
        clock=lambda: NOON,
    )
    admission = line.admit(f"CA{tag}" + "9" * 20, CALLER, used_seconds=recorder.seconds_used_today(NOON))
    assert admission.token is None and admission.reason == "daily_cap"
