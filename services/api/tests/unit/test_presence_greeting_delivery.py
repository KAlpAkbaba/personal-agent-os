"""Deciding to greet and having greeted are different facts.

``evaluate_greeting_now`` used to write a ``presence.greeting_delivered`` ledger row
whenever its decision came out true — while its own docstring said it does not speak or
notify the owner. Both could not be so, and the consequence ran the wrong way: asking
"should I greet?" started the cooldown, so the real greeting was then suppressed for the
whole window, and the ledger carried a delivery nobody heard.

These tests pin the split. Evaluation is pure; delivery is reported by whatever actually
narrated it, afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import EVENT_TYPE_PRESENCE_GREETING_DELIVERED
from app.presence import service as presence_service
from app.presence.engine import PresenceEpisode, PresenceFusionEngine
from app.presence.greeting import GreetingPolicy
from app.presence.states import PresenceState

DAY = datetime(2026, 9, 8, tzinfo=UTC)
POLICY = GreetingPolicy(timezone=UTC, min_rest_duration_s=20 * 60.0, min_awake_duration_s=90.0)


class _EpisodeEngine(PresenceFusionEngine):
    """A fusion engine with a fixed episode history, so these tests are about delivery."""

    def __init__(self, episodes: list[PresenceEpisode]) -> None:
        self._episodes = episodes

    def episodes(self) -> list[PresenceEpisode]:  # type: ignore[override]
        return list(self._episodes)


def _episode(state: PresenceState, *, start: datetime, duration_s: float) -> PresenceEpisode:
    return PresenceEpisode(
        state=state, start_at=start, end_at=start + timedelta(seconds=duration_s), confidence=0.9
    )


def _morning() -> tuple[_EpisodeEngine, datetime]:
    rest = _episode(PresenceState.RESTING, start=DAY.replace(hour=7), duration_s=30 * 60)
    wake = _episode(PresenceState.AWAKE, start=rest.end_at, duration_s=120)
    return _EpisodeEngine([rest, wake]), wake.end_at


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


def _delivered_rows(session) -> list[ActivityEventRow]:
    return list(
        session.execute(
            select(ActivityEventRow).where(
                ActivityEventRow.event_type == EVENT_TYPE_PRESENCE_GREETING_DELIVERED
            )
        )
        .scalars()
        .all()
    )


def test_evaluating_does_not_record_a_delivery(session) -> None:
    engine, now = _morning()

    decision = presence_service.evaluate_greeting_now(
        session, engine=engine, policy=POLICY, now=now
    )

    assert decision.should_greet is True
    assert _delivered_rows(session) == []


def test_evaluating_twice_still_says_yes(session) -> None:
    """The bug, stated as a test: asking must not consume the greeting."""
    engine, now = _morning()

    first = presence_service.evaluate_greeting_now(session, engine=engine, policy=POLICY, now=now)
    second = presence_service.evaluate_greeting_now(
        session, engine=engine, policy=POLICY, now=now + timedelta(seconds=1)
    )

    assert first.should_greet is True
    assert second.should_greet is True
    assert second.reason == "sustained_wake_after_rest"


def test_delivery_records_once_and_starts_the_cooldown(session) -> None:
    engine, now = _morning()
    decision = presence_service.evaluate_greeting_now(
        session, engine=engine, policy=POLICY, now=now
    )

    presence_service.record_greeting_delivered(session, decision, now=now)

    rows = _delivered_rows(session)
    assert len(rows) == 1
    assert rows[0].detail_json["should_greet"] is True

    # Now, and only now, the cooldown holds.
    after = presence_service.evaluate_greeting_now(
        session, engine=engine, policy=POLICY, now=now + timedelta(minutes=1)
    )
    assert after.should_greet is False
    assert after.reason == "cooldown_active"


def test_the_cooldown_is_read_from_the_delivery_record(session) -> None:
    engine, now = _morning()
    decision = presence_service.evaluate_greeting_now(
        session, engine=engine, policy=POLICY, now=now
    )
    presence_service.record_greeting_delivered(session, decision, now=now)

    assert presence_service.last_greeted_at(session) is not None


def test_a_refusal_cannot_be_recorded_as_a_delivery(session) -> None:
    """A cooldown started by a refusal would silence the next real greeting."""
    rest = _episode(PresenceState.RESTING, start=DAY.replace(hour=21), duration_s=6 * 60 * 60)
    brief = _episode(PresenceState.AWAKE, start=rest.end_at, duration_s=3)  # 03:00, three seconds
    engine = _EpisodeEngine([rest, brief])

    decision = presence_service.evaluate_greeting_now(
        session, engine=engine, policy=GreetingPolicy(timezone=UTC), now=brief.end_at
    )
    assert decision.should_greet is False

    with pytest.raises(ValueError):
        presence_service.record_greeting_delivered(session, decision, now=brief.end_at)
    assert _delivered_rows(session) == []
