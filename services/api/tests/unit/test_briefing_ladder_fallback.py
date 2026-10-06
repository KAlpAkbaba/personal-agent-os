"""Unit tests: a briefing reaches the owner without a live voice session.

ROADMAP "Owner's queue" item 1 had two halves and only the voice one was built: with no
live session ``narrate`` answers ``no_live_session``, the row counts a failure and expires
while the owner sleeps. The notification ladder (toast -> sound -> push -> inbox) already
reaches him without a session, so the announcer falls back to it - for that ONE reason.

The queue and the notification table are real (SQLite); the only fake is the speaker.
The ladder's own stamp is written by hand here, the way ``ladder.deliver_one`` writes it;
the Postgres test runs the real ladder.
"""

from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger import briefing as briefing_service
from app.ledger import briefing_announcer as announcer_module
from app.ledger import service as ledger_service
from app.ledger.briefing import VIA_NOTIFICATION_PREFIX, VIA_VOICE
from app.ledger.briefing_announcer import (
    NotificationBriefingFallback,
    PendingBriefingAnnouncer,
    SayOutcome,
)
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.ledger.vocabulary import EVENT_TYPE_BRIEFING_DELIVERED, EVENT_TYPE_RESEARCH_COMPLETED
from app.notifications.models import PRIORITY_NORMAL, PRIORITY_URGENT, NotificationRow

#: 14:00 in Istanbul - outside quiet hours.
NOW = datetime(2026, 9, 11, 11, 0, tzinfo=UTC)
#: 00:30 in Istanbul - inside quiet hours (23:00-07:30).
NIGHT = datetime(2026, 9, 11, 21, 30, tzinfo=UTC)


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        ActivityEventRow.__table__,
        PendingBriefingRow.__table__,
        NotificationRow.__table__,
    ):
        table.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@dataclass
class Speaker:
    """The transport only. ``reason`` is what ``BriefingDelivery.reason`` would say."""

    reason: str = "no_live_session"
    said: list[str] = field(default_factory=list)

    def say(self, text: str, briefing_ids=()) -> SayOutcome:
        self.said.append(text)
        return SayOutcome(delivered=self.reason == "delivered", reason=self.reason)


def _queue(
    factory, *, at: datetime = NOW, critical: bool = False, digest: bool = False
) -> uuid.UUID:
    if digest:
        event_type, severity = "evolution.idea_created", "info"
    elif critical:
        event_type, severity = "evolution.idea_created", "critical"
    else:
        event_type, severity = EVENT_TYPE_RESEARCH_COMPLETED, "info"
    with factory() as session:
        row = ledger_service.record(
            session,
            ledger_service.ActivityEvent(
                event_type=event_type,
                subsystem="research" if event_type.startswith("research") else "evolution",
                action=event_type.split(".", 1)[-1],
                factual_summary="Disk dolmak üzere.",
                occurred_at=at,
                severity=severity,
                detail_json={"findings": 2},
                source="live",
                source_ref=f"test:{uuid.uuid4()}",
            ),
        )
        queued = briefing_service.queue_briefing(session, row, now=at)
        assert queued is not None
        return queued.briefing_id


def _notes(factory) -> list[NotificationRow]:
    with factory() as session:
        return list(
            session.execute(select(NotificationRow).order_by(NotificationRow.created_at))
            .scalars()
            .all()
        )


def _briefing(factory, briefing_id: uuid.UUID) -> PendingBriefingRow:
    with factory() as session:
        return session.get(PendingBriefingRow, briefing_id)


def _ladder_stamps(factory, note_id: uuid.UUID, channel: str, at: datetime = NOW) -> None:
    """What ``ladder.deliver_one`` writes when a rung reaches the owner."""
    with factory() as session:
        note = session.get(NotificationRow, note_id)
        note.delivered_at = at
        note.delivered_via = channel
        note.attempted_json = [channel]
        session.commit()


def _announcer(factory, speaker) -> PendingBriefingAnnouncer:
    return PendingBriefingAnnouncer(
        factory, speaker, fallback=NotificationBriefingFallback(factory)
    )


# ---------------------------------------------------------- 2: one notification, once


def test_no_live_session_writes_one_normal_notification_and_never_a_second(factory) -> None:
    briefing_id = _queue(factory, critical=True)
    announcer = _announcer(factory, Speaker("no_live_session"))

    assert announcer.sweep_once(NOW) == 0, "a notification written is not a delivery"
    (note,) = _notes(factory)
    assert note.kind == "briefing.immediate"
    assert note.group_key == f"briefing:{briefing_id}"
    assert note.priority == PRIORITY_NORMAL
    assert note.title == "Brifing"
    assert note.body == _briefing(factory, briefing_id).speech
    assert note.data_json == {"briefing_ids": [str(briefing_id)], "policy": "immediate"}

    announcer.sweep_once(NOW + timedelta(seconds=20))
    assert len(_notes(factory)) == 1, "the group key is the duplicate lock"
    row = _briefing(factory, briefing_id)
    assert row.delivered_at is None
    assert (row.attempts or 0) == 0, "waiting on the ladder is not a failed attempt"
    assert row.quarantined_at is None


# --------------------------------------------- 3: a session that will drain it waits


@pytest.mark.parametrize("reason", ["queued_to_session", "already_queued"])
def test_a_web_session_that_will_drain_it_writes_no_notification(factory, reason) -> None:
    _queue(factory)
    announcer = _announcer(factory, Speaker(reason))

    assert announcer.sweep_once(NOW) == 0
    assert _notes(factory) == []


def test_a_queue_failure_is_not_a_missing_session(factory) -> None:
    _queue(factory)
    assert _announcer(factory, Speaker("queue_failed")).sweep_once(NOW) == 0
    assert _notes(factory) == []


# ------------------------------------ 4: stamped by the channel that delivered it


def test_the_briefing_is_stamped_by_the_rung_that_reached_him_not_by_the_queue(factory) -> None:
    briefing_id = _queue(factory)
    announcer = _announcer(factory, Speaker("no_live_session"))

    announcer.sweep_once(NOW)
    assert _briefing(factory, briefing_id).delivered_at is None, "a queue is not a delivery"

    (note,) = _notes(factory)
    _ladder_stamps(factory, note.id, "push")
    assert announcer.sweep_once(NOW + timedelta(seconds=20)) == 1

    row = _briefing(factory, briefing_id)
    assert row.delivered_at is not None
    assert row.delivered_via == VIA_NOTIFICATION_PREFIX + "push" == "notification:push"
    with factory() as session:
        (event,) = (
            session.execute(
                select(ActivityEventRow).where(
                    ActivityEventRow.event_type == EVENT_TYPE_BRIEFING_DELIVERED
                )
            )
            .scalars()
            .all()
        )
    assert event.detail_json["via"] == "notification:push"
    assert len(_notes(factory)) == 1


def test_an_inbox_only_delivery_is_not_heard_and_the_next_live_session_says_it(factory) -> None:
    briefing_id = _queue(factory)
    speaker = Speaker("no_live_session")
    announcer = _announcer(factory, speaker)

    announcer.sweep_once(NOW)
    (note,) = _notes(factory)
    _ladder_stamps(factory, note.id, "inbox")

    assert announcer.sweep_once(NOW + timedelta(seconds=20)) == 0
    row = _briefing(factory, briefing_id)
    assert row.delivered_at is None, "the inbox is a floor, not being heard"
    assert (row.attempts or 0) == 0

    speaker.reason = "delivered"
    assert announcer.sweep_once(NOW + timedelta(seconds=40)) == 1
    row = _briefing(factory, briefing_id)
    assert row.delivered_via == VIA_VOICE
    (still,) = _notes(factory)
    assert still.id == note.id, "the inbox notification stays where it is"


def test_a_ladder_that_gave_up_counts_a_failure(factory) -> None:
    briefing_id = _queue(factory)
    announcer = _announcer(factory, Speaker("no_live_session"))
    announcer.sweep_once(NOW)
    (note,) = _notes(factory)
    with factory() as session:
        dead = session.get(NotificationRow, note.id)
        dead.ladder_exhausted = True
        dead.quarantined_at = NOW
        session.commit()

    announcer.sweep_once(NOW + timedelta(seconds=20))
    assert (_briefing(factory, briefing_id).attempts or 0) == 1
    assert len(_notes(factory)) == 1


# ------------------------------------------------------------ 5: digests stay home


def test_digests_never_go_to_the_ladder(factory) -> None:
    for index in range(3):
        _queue(factory, digest=True, at=NOW + timedelta(seconds=index))
    _announcer(factory, Speaker("no_live_session")).sweep_once(NOW)
    assert _notes(factory) == []


# ------------------------------------------------------------- 6: five per sweep


def test_at_most_five_notifications_per_sweep_oldest_first(factory) -> None:
    ids = [_queue(factory, at=NOW + timedelta(seconds=index)) for index in range(7)]
    announcer = _announcer(factory, Speaker("no_live_session"))

    announcer.sweep_once(NOW + timedelta(minutes=1))
    notes = _notes(factory)
    assert len(notes) == 5
    assert {n.group_key for n in notes} == {f"briefing:{i}" for i in ids[:5]}

    announcer.sweep_once(NOW + timedelta(minutes=2))
    assert len(_notes(factory)) == 7


# --------------------------------------------------- 7: quiet hours, never urgent


def test_a_night_notification_is_deferred_and_never_urgent(factory) -> None:
    _queue(factory, critical=True, at=NIGHT)
    _announcer(factory, Speaker("no_live_session")).sweep_once(NIGHT)
    (note,) = _notes(factory)
    assert note.priority == PRIORITY_NORMAL
    assert note.deferred_until is not None, "quiet hours keep a briefing from ringing"


def test_no_path_in_the_fallback_names_the_urgent_priority() -> None:
    source = inspect.getsource(NotificationBriefingFallback)
    assert "PRIORITY_URGENT" not in source
    assert f'"{PRIORITY_URGENT}"' not in source
    assert "PRIORITY_NORMAL" in source


# ------------------------------------------------------------- 9: None is today


def test_without_a_fallback_nothing_is_written(factory) -> None:
    briefing_id = _queue(factory)
    PendingBriefingAnnouncer(factory, Speaker("no_live_session")).sweep_once(NOW)
    assert _notes(factory) == []
    assert _briefing(factory, briefing_id).attempts == 1, "today's say_failed count"


def test_the_realtime_speaker_carries_the_reason() -> None:
    from app.routines.dispatch import BriefingDelivery

    class Port:
        def narrate(self, **_kw) -> BriefingDelivery:
            return BriefingDelivery(False, "no_live_session")

    outcome = announcer_module.RealtimeSayBriefingSpeaker(Port()).say("x", [])
    assert outcome == SayOutcome(delivered=False, reason="no_live_session")
