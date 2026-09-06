"""A held presence state must not expire on the Core while the evidence is fresh.

The bus publishes owner.* on CHANGE. The Core expires an observation after its ttl_s (the
camera's is 90 s). So in the 2026-09-06 owner run `away` was held from 09:43 to 09:54 on
1,102 fresh camera signals, published exactly once, and the Core showed "Sahip durumu
bilinmiyor" for ten of those eleven minutes. The heartbeat republishes a held state once half
its TTL has passed - and never UNKNOWN, which is the absence of a claim.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.presence import service as presence_service
from app.presence.states import PresenceAssertion, PresenceState
from app.uistate import UiStatePublisher, set_publisher
from app.uistate.publisher import get_publisher

T0 = datetime(2026, 9, 6, 9, 43, 32, tzinfo=UTC)


def _held(state: PresenceState, ttl_s: float = 90.0) -> PresenceAssertion:
    return PresenceAssertion(
        state=state,
        confidence=0.75,
        signals=(),
        observed_at=T0,
        state_started_at=T0,
        stale_after_s=ttl_s,
    )


@pytest.fixture(autouse=True)
def _fresh():
    presence_service.reset_heartbeat()
    previous = get_publisher()
    set_publisher(UiStatePublisher())
    yield
    set_publisher(previous)
    presence_service.reset_heartbeat()


def test_first_publish_of_a_state_is_always_due() -> None:
    assert presence_service.heartbeat_due(_held(PresenceState.AWAY), now=T0)


def test_not_due_again_before_half_the_ttl() -> None:
    a = _held(PresenceState.AWAY, ttl_s=90.0)
    presence_service._publish_presence(a)
    presence_service._note_published(a)
    assert not presence_service.heartbeat_due(a, now=datetime.now(UTC) + timedelta(seconds=30))


def test_due_once_half_the_ttl_has_passed() -> None:
    a = _held(PresenceState.AWAY, ttl_s=90.0)
    presence_service._publish_presence(a)
    presence_service._note_published(a)
    assert presence_service.heartbeat_due(a, now=datetime.now(UTC) + timedelta(seconds=46))


def test_unknown_is_never_republished() -> None:
    assert not presence_service.heartbeat_due(_held(PresenceState.UNKNOWN), now=T0)


def test_a_state_change_resets_the_clock() -> None:
    presence_service._note_published(_held(PresenceState.AWAY))
    assert presence_service.heartbeat_due(_held(PresenceState.PRESENT), now=datetime.now(UTC))


def test_the_republish_carries_confidence_and_ttl_like_a_transition() -> None:
    presence_service._publish_presence(_held(PresenceState.AWAY, ttl_s=90.0))
    (event,) = get_publisher().tail()
    assert event.state.value == "owner.away"
    assert event.metadata["confidence"] == 0.75
    assert event.metadata["ttl_s"] == 90.0
