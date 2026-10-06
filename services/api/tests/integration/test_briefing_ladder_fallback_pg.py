"""Integration: a briefing with no live session rides the REAL notification ladder.

PostgreSQL at head, the real ``ladder.sweep``; only the speaker (no session) and the push
transport (``FakePushProvider``) are fakes. Two runs of the same chain:

* inbox only -> the notification is ``inbox``, the briefing is NOT heard and waits;
* push accepted -> the notification is ``push``, the briefing is ``notification:push``.

Every row this file writes is deleted again: the queue is shared with the rest of the
suite, and a leftover pending briefing would be spoken by somebody else's announcer.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import build_engine
from app.ledger import briefing as briefing_service
from app.ledger import service as ledger_service
from app.ledger.briefing_announcer import (
    NotificationBriefingFallback,
    PendingBriefingAnnouncer,
    SayOutcome,
)
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.ledger.vocabulary import EVENT_TYPE_RESEARCH_COMPLETED
from app.notifications import ladder
from app.notifications.models import CHANNEL_INBOX, CHANNEL_PUSH, NotificationRow
from app.webpush import ece
from app.webpush.models import PushSubscriptionRow
from app.webpush.provider import FakePushProvider
from app.webpush.vapid import generate_key_pair

pytestmark = pytest.mark.integration


@dataclass
class NoSession:
    def say(self, text: str, briefing_ids=()) -> SayOutcome:
        return SayOutcome(delivered=False, reason="no_live_session")


@pytest.fixture()
def factory() -> Iterator[sessionmaker]:
    engine = build_engine(Settings().database_url)
    made = sessionmaker(bind=engine, expire_on_commit=False)
    created: dict[str, list] = {"briefings": [], "events": [], "subs": []}
    made.created = created  # type: ignore[attr-defined]
    started = datetime.now(UTC)
    try:
        yield made
    finally:
        with made() as db:
            # The announcer sweeps the whole queue, so another suite's pending row may
            # have been notified during this test too; that notification is ours to remove.
            db.execute(
                delete(NotificationRow).where(
                    NotificationRow.kind.like("briefing.%"),
                    NotificationRow.created_at >= started,
                )
            )
            keys = [f"briefing:{i}" for i in created["briefings"]]
            if keys:
                db.execute(delete(NotificationRow).where(NotificationRow.group_key.in_(keys)))
                db.execute(
                    delete(PendingBriefingRow).where(
                        PendingBriefingRow.briefing_id.in_(created["briefings"])
                    )
                )
                refs = [f"pending_briefings:{i}:%" for i in created["briefings"]]
                for ref in refs:
                    db.execute(
                        delete(ActivityEventRow).where(ActivityEventRow.source_ref.like(ref))
                    )
            if created["events"]:
                db.execute(
                    delete(ActivityEventRow).where(ActivityEventRow.event_id.in_(created["events"]))
                )
            if created["subs"]:
                db.execute(
                    delete(PushSubscriptionRow).where(
                        PushSubscriptionRow.endpoint.in_(created["subs"])
                    )
                )
            db.commit()
        engine.dispose()


def _queue(factory) -> uuid.UUID:
    now = datetime.now(UTC)
    with factory() as db:
        event = ledger_service.record(
            db,
            ledger_service.ActivityEvent(
                event_type=EVENT_TYPE_RESEARCH_COMPLETED,
                subsystem="research",
                action="completed",
                factual_summary="Araştırma tamamlandı.",
                occurred_at=now,
                detail_json={"findings": 1},
                source="live",
                source_ref=f"pg-ladder-test:{uuid.uuid4()}",
            ),
        )
        factory.created["events"].append(event.event_id)
        queued = briefing_service.queue_briefing(db, event, now=now)
        assert queued is not None
        factory.created["briefings"].append(queued.briefing_id)
        return queued.briefing_id


def _note(factory, briefing_id: uuid.UUID) -> NotificationRow:
    with factory() as db:
        (note,) = (
            db.query(NotificationRow)
            .filter(NotificationRow.group_key == f"briefing:{briefing_id}")
            .all()
        )
        return note


def _briefing(factory, briefing_id: uuid.UUID) -> PendingBriefingRow:
    with factory() as db:
        return db.get(PendingBriefingRow, briefing_id)


def _run_ladder_on(factory, note_id: uuid.UUID, rungs) -> str | None:
    """The real ``ladder.deliver_one`` on exactly our row: ``ladder.sweep`` over a shared
    table would also touch other tests' notifications."""
    with factory() as db:
        row = db.get(NotificationRow, note_id)
        return ladder.deliver_one(db, row, rungs=rungs)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_inbox_only_leaves_the_briefing_waiting(factory) -> None:
    briefing_id = _queue(factory)
    announcer = PendingBriefingAnnouncer(
        factory, NoSession(), fallback=NotificationBriefingFallback(factory)
    )

    announcer.sweep_once()
    note = _note(factory, briefing_id)
    assert note.kind == "briefing.completion" and note.priority == "normal"
    assert _run_ladder_on(factory, note.id, ladder.default_rungs()) == CHANNEL_INBOX

    announcer.sweep_once()
    row = _briefing(factory, briefing_id)
    assert row.delivered_at is None, "the inbox is not being heard"
    assert (row.attempts or 0) == 0
    _note(factory, briefing_id)  # still exactly one


def test_push_stamps_the_briefing_notification_push(factory) -> None:
    endpoint = f"https://fcm.googleapis.com/fcm/send/pg-ladder-{uuid.uuid4()}"
    private = ec.generate_private_key(ece.CURVE)
    with factory() as db:
        db.add(
            PushSubscriptionRow(
                endpoint=endpoint,
                p256dh=_b64url(ece.raw_public_key(private.public_key())),
                auth=_b64url(b"\x03" * 16),
                user_agent="pytest",
            )
        )
        db.commit()
    factory.created["subs"].append(endpoint)

    briefing_id = _queue(factory)
    announcer = PendingBriefingAnnouncer(
        factory, NoSession(), fallback=NotificationBriefingFallback(factory)
    )
    announcer.sweep_once()
    note = _note(factory, briefing_id)

    push = ladder.PushRung(
        session_factory=factory,
        provider=FakePushProvider(),
        vapid_private_key=generate_key_pair().private_key,
        vapid_subject="mailto:owner@example.com",
    )
    assert _run_ladder_on(factory, note.id, ladder.default_rungs(push_rung=push)) == CHANNEL_PUSH

    assert announcer.sweep_once() == 1
    row = _briefing(factory, briefing_id)
    assert row.delivered_via == "notification:push"
