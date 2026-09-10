"""Unit tests: the briefing deliverer (the half ADR-0110 named and did not build).

The queue is real (``app.ledger.briefing`` on SQLite); the only fake is the speaker,
because that is the transport. What is asserted is the contract that matters: a row is
stamped by the thing that actually delivered it, one thing is said per pass, digests
become one sentence, and the real application object carries the component and its
lifespan runs it.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger import briefing as briefing_service
from app.ledger import service as ledger_service
from app.ledger.briefing_announcer import (
    VIA_VOICE,
    PendingBriefingAnnouncer,
    RealtimeSayBriefingSpeaker,
    digest_sentence,
)
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.ledger.vocabulary import EVENT_TYPE_RESEARCH_COMPLETED

NOW = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (ActivityEventRow.__table__, PendingBriefingRow.__table__):
        table.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@dataclass
class FakeSpeaker:
    deliver: bool = True
    raise_on_say: bool = False
    said: list[str] = field(default_factory=list)

    def say(self, text: str) -> bool:
        if self.raise_on_say:
            raise RuntimeError("transport fell over")
        self.said.append(text)
        return self.deliver


def _queue(factory, *, event_type: str, summary: str, at: datetime, **detail) -> uuid.UUID:
    with factory() as session:
        row = ledger_service.record(
            session,
            ledger_service.ActivityEvent(
                event_type=event_type,
                subsystem="research" if event_type.startswith("research") else "evolution",
                action=event_type.split(".", 1)[-1],
                factual_summary=summary,
                occurred_at=at,
                detail_json=dict(detail),
                source="live",
                source_ref=f"test:{uuid.uuid4()}",
            ),
        )
        queued = briefing_service.queue_briefing(session, row, now=at)
        assert queued is not None, "the fixture event must be one the policy queues"
        return queued.briefing_id


def _rows(factory) -> list[PendingBriefingRow]:
    with factory() as session:
        return list(session.execute(select(PendingBriefingRow)).scalars().all())


# -------------------------------------------------------------- one at a time


def test_an_urgent_briefing_is_spoken_as_itself_and_stamped_by_the_delivery(factory) -> None:
    briefing_id = _queue(
        factory,
        event_type=EVENT_TYPE_RESEARCH_COMPLETED,
        summary="Araştırma tamamlandı.",
        at=NOW,
        findings=2,
    )
    speaker = FakeSpeaker(deliver=True)

    delivered = PendingBriefingAnnouncer(factory, speaker).sweep_once()

    assert delivered == 1
    assert speaker.said == [
        "Efendim, bilginize; araştırma tamamlandı. İki önemli sonuç çıkardım. "
        "İsterseniz özetini anlatabilirim."
    ]
    (row,) = _rows(factory)
    assert row.briefing_id == briefing_id
    assert row.delivered_at is not None
    assert row.delivered_via == VIA_VOICE


def test_nobody_heard_it_means_it_is_not_delivered(factory) -> None:
    """The stamp is the speaker's own return value. No live session, no bound device,
    a push that failed: the row stays, and is spoken on the next pass instead."""
    _queue(factory, event_type=EVENT_TYPE_RESEARCH_COMPLETED, summary="x", at=NOW, findings=1)
    speaker = FakeSpeaker(deliver=False)

    delivered = PendingBriefingAnnouncer(factory, speaker).sweep_once()

    assert delivered == 0
    assert len(speaker.said) == 1, "it tried"
    (row,) = _rows(factory)
    assert row.delivered_at is None


def test_a_speaker_that_raises_stamps_nothing_and_does_not_kill_the_sweep(factory) -> None:
    _queue(factory, event_type=EVENT_TYPE_RESEARCH_COMPLETED, summary="x", at=NOW, findings=1)

    delivered = PendingBriefingAnnouncer(factory, FakeSpeaker(raise_on_say=True)).sweep_once()

    assert delivered == 0
    (row,) = _rows(factory)
    assert row.delivered_at is None


def test_only_one_thing_is_said_per_pass(factory) -> None:
    """The constitution: notify briefly and wait. Two completions queued, one pass,
    one sentence -- the other waits for the next pass."""
    _queue(factory, event_type=EVENT_TYPE_RESEARCH_COMPLETED, summary="a", at=NOW, findings=1)
    _queue(
        factory,
        event_type=EVENT_TYPE_RESEARCH_COMPLETED,
        summary="b",
        at=NOW + timedelta(minutes=1),
        findings=3,
    )
    speaker = FakeSpeaker()
    announcer = PendingBriefingAnnouncer(factory, speaker)

    assert announcer.sweep_once() == 1
    assert len(speaker.said) == 1
    assert announcer.sweep_once() == 1
    assert len(speaker.said) == 2
    assert announcer.sweep_once() == 0, "nothing left"
    assert all(row.delivered_at is not None for row in _rows(factory))


# ------------------------------------------------------------------- digests


def test_digests_become_one_sentence_and_are_all_stamped_together(factory) -> None:
    """Nine evolution rows read aloud one by one is the flood the constitution forbids.
    They are what the spec calls "accumulated and summarized at delivery time"."""
    for index in range(3):
        _queue(
            factory,
            event_type="evolution.idea_created",
            summary=f"Evrim fırsatı {index} açıldı.",
            at=NOW + timedelta(seconds=index),
        )
    speaker = FakeSpeaker()

    delivered = PendingBriefingAnnouncer(factory, speaker).sweep_once()

    assert delivered == 3
    assert speaker.said == [
        "Efendim, bilginize; kendi üzerimde üç kayıt biriktirdim. İsterseniz tek tek anlatayım."
    ]
    assert all(row.delivered_via == VIA_VOICE for row in _rows(factory))


def test_a_single_digest_is_spoken_as_its_own_sentence(factory) -> None:
    _queue(factory, event_type="evolution.idea_created", summary="Evrim fırsatı açıldı.", at=NOW)
    speaker = FakeSpeaker()

    PendingBriefingAnnouncer(factory, speaker).sweep_once()

    assert speaker.said == ["Efendim, bilginize; Evrim fırsatı açıldı."]


def test_an_urgent_row_is_spoken_before_an_older_digest(factory) -> None:
    _queue(factory, event_type="evolution.idea_created", summary="eski özet", at=NOW)
    _queue(
        factory,
        event_type=EVENT_TYPE_RESEARCH_COMPLETED,
        summary="yeni",
        at=NOW + timedelta(hours=1),
        findings=1,
    )
    speaker = FakeSpeaker()

    delivered = PendingBriefingAnnouncer(factory, speaker).sweep_once()

    assert delivered == 1
    assert "araştırma tamamlandı" in speaker.said[0]
    undelivered = [row for row in _rows(factory) if row.delivered_at is None]
    assert [row.policy for row in undelivered] == [briefing_service.POLICY_DIGEST]


def test_digest_sentence_never_reads_the_pile() -> None:
    rows = [
        PendingBriefingRow(
            briefing_id=uuid.uuid4(),
            created_at=NOW,
            policy=briefing_service.POLICY_DIGEST,
            priority=30,
            speech=f"kayıt {index}",
            event_ids=[],
            expires_at=NOW + timedelta(days=1),
        )
        for index in range(40)
    ]
    sentence = digest_sentence(rows)
    assert "kayıt 0" not in sentence
    assert "epeyce" in sentence


# ------------------------------------------------------------------- expiry


def test_an_expired_briefing_is_never_spoken(factory) -> None:
    """Fourteen expired rows on 2026-09-10 must NOT become fourteen sentences the day
    the deliverer arrives -- stale news is not news."""
    _queue(
        factory,
        event_type=EVENT_TYPE_RESEARCH_COMPLETED,
        summary="çok eski",
        at=NOW - timedelta(days=3),
        findings=1,
    )
    speaker = FakeSpeaker()

    delivered = PendingBriefingAnnouncer(factory, speaker).sweep_once()

    assert delivered == 0
    assert speaker.said == []


# ------------------------------------------------------------ the real adapter


def test_the_realtime_speaker_reports_exactly_what_the_port_delivered() -> None:
    from app.routines.dispatch import BriefingDelivery

    class Port:
        def __init__(self, delivered: bool) -> None:
            self.delivered = delivered
            self.calls: list[dict] = []

        def narrate(self, *, text: str, routine_id, firing_id) -> BriefingDelivery:
            self.calls.append({"text": text, "routine_id": routine_id, "firing_id": firing_id})
            return BriefingDelivery(
                self.delivered, "delivered" if self.delivered else "no_live_session"
            )

    yes = Port(True)
    assert RealtimeSayBriefingSpeaker(yes).say("merhaba") is True
    assert yes.calls[0]["text"] == "merhaba"
    assert yes.calls[0]["routine_id"] == RealtimeSayBriefingSpeaker.SYSTEM_ORIGIN

    no = Port(False)
    assert RealtimeSayBriefingSpeaker(no).say("merhaba") is False


# ------------------------------------------------------------------- wiring


def test_the_real_application_object_carries_it_and_the_lifespan_runs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through ``create_app`` and its real lifespan. ``queue_briefing`` had callers and
    no deliverer for five days because the deliverer did not exist; a deliverer that
    exists and is not started would be the same silence with more code."""
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None))
    announcer = app.state.briefing_announcer
    assert isinstance(announcer, PendingBriefingAnnouncer)

    passes = 0
    stopped = False

    def counted() -> int:
        nonlocal passes
        passes += 1
        return 0

    real_stop = announcer.stop

    async def watched_stop() -> None:
        nonlocal stopped
        stopped = True
        await real_stop()

    monkeypatch.setattr(announcer, "sweep_once", counted)
    monkeypatch.setattr(announcer, "stop", watched_stop)
    monkeypatch.setattr(announcer, "_interval_s", 0.05)

    with TestClient(app) as client:
        assert announcer.running, "the lifespan did not start it"
        client.get("/v1/system/health")
        deadline = time.perf_counter() + 5.0
        while not passes and time.perf_counter() < deadline:
            time.sleep(0.05)

    assert passes >= 1, "started but never swept"
    assert stopped, "the lifespan did not stop it"
