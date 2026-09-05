"""Unit tests: app.presence.greeting.

The defining negative case, stated in M18_HOLOGRAPHIC_CORE_SPEC.md §1
verbatim: a brief movement at 03:00 must NOT produce a morning greeting.
Both independent gates that catch it get their own test, plus the positive
case and every other named gate (cooldown, no prior rest, insufficient rest,
insufficient wake).

Every test fixes ``GreetingPolicy(timezone=UTC)`` so "local hour" is exactly
the UTC hour used to build episode timestamps below - the policy's default
timezone (Europe/Istanbul) is exercised separately in
``test_default_timezone_is_europe_istanbul``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.presence.engine import PresenceEpisode
from app.presence.greeting import DEFAULT_TIMEZONE, GreetingPolicy, evaluate_greeting
from app.presence.states import PresenceState

DAY = datetime(2026, 9, 8, tzinfo=UTC)  # a plain Tuesday


def _at(hour: int, minute: int = 0) -> datetime:
    return DAY.replace(hour=hour, minute=minute)


def _episode(state: PresenceState, *, start: datetime, duration_s: float) -> PresenceEpisode:
    return PresenceEpisode(
        state=state, start_at=start, end_at=start + timedelta(seconds=duration_s), confidence=0.9
    )


def test_sustained_wake_after_sustained_rest_at_a_plausible_hour_greets() -> None:
    policy = GreetingPolicy(timezone=UTC, min_rest_duration_s=20 * 60.0, min_awake_duration_s=90.0)
    rest = _episode(PresenceState.RESTING, start=_at(7, 0), duration_s=30 * 60)
    wake = _episode(PresenceState.AWAKE, start=rest.end_at, duration_s=120)
    decision = evaluate_greeting([rest, wake], now=wake.end_at, last_greeted_at=None, policy=policy)
    assert decision.should_greet is True
    assert decision.reason == "sustained_wake_after_rest"
    assert decision.rest_episode is rest
    assert decision.wake_episode is wake


def test_a_brief_movement_at_0300_does_not_produce_a_morning_greeting() -> None:
    """The spec's own counterexample, verbatim."""
    policy = GreetingPolicy(timezone=UTC)
    rest = _episode(PresenceState.RESTING, start=_at(21), duration_s=6 * 60 * 60)  # 21:00 -> 03:00
    brief_wake = _episode(PresenceState.AWAKE, start=rest.end_at, duration_s=3)
    decision = evaluate_greeting(
        [rest, brief_wake], now=brief_wake.end_at, last_greeted_at=None, policy=policy
    )
    assert decision.should_greet is False
    # The implausible-time gate is checked before wake duration, so it is
    # named first, but either gate alone would have refused this.
    assert decision.reason in ("implausible_time", "wake_not_sustained")


def test_implausible_hour_blocks_a_greeting_even_if_the_wake_were_sustained() -> None:
    """Isolates the time-context gate from the duration gate: a FULLY
    sustained wake-after-rest at 03:00 must still not greet."""
    policy = GreetingPolicy(timezone=UTC, min_awake_duration_s=90.0)
    rest = _episode(PresenceState.RESTING, start=_at(2, 0), duration_s=30 * 60)
    sustained_wake_at_3am = _episode(PresenceState.AWAKE, start=_at(3, 0), duration_s=300)
    decision = evaluate_greeting(
        [rest, sustained_wake_at_3am],
        now=sustained_wake_at_3am.end_at,
        last_greeted_at=None,
        policy=policy,
    )
    assert decision.should_greet is False
    assert decision.reason == "implausible_time"


def test_a_brief_wake_at_a_plausible_hour_is_still_refused_for_insufficient_duration() -> None:
    """Isolates the duration gate from the time-context gate: same brief
    movement, but at 08:00 instead of 03:00 - still refused."""
    policy = GreetingPolicy(timezone=UTC, min_awake_duration_s=90.0)
    rest = _episode(PresenceState.RESTING, start=_at(7, 30), duration_s=30 * 60)
    brief_wake = _episode(PresenceState.AWAKE, start=_at(8), duration_s=3)
    decision = evaluate_greeting(
        [rest, brief_wake], now=brief_wake.end_at, last_greeted_at=None, policy=policy
    )
    assert decision.should_greet is False
    assert decision.reason == "wake_not_sustained"


def test_no_prior_rest_episode_refuses_even_with_sustained_wake() -> None:
    policy = GreetingPolicy(timezone=UTC)
    wake = _episode(PresenceState.AWAKE, start=_at(8), duration_s=300)
    decision = evaluate_greeting([wake], now=wake.end_at, last_greeted_at=None, policy=policy)
    assert decision.should_greet is False
    assert decision.reason == "no_prior_rest"


def test_a_short_rest_before_waking_does_not_count_as_sustained() -> None:
    policy = GreetingPolicy(timezone=UTC, min_rest_duration_s=20 * 60.0)
    rest = _episode(PresenceState.RESTING, start=_at(7, 58), duration_s=60)  # only 1 minute
    wake = _episode(PresenceState.AWAKE, start=_at(8), duration_s=300)
    decision = evaluate_greeting([rest, wake], now=wake.end_at, last_greeted_at=None, policy=policy)
    assert decision.should_greet is False
    assert decision.reason == "rest_not_sustained"


def test_cooldown_blocks_a_second_greeting() -> None:
    policy = GreetingPolicy(timezone=UTC, cooldown_s=4 * 60 * 60.0)
    rest = _episode(PresenceState.RESTING, start=_at(7, 0), duration_s=30 * 60)
    wake = _episode(PresenceState.AWAKE, start=rest.end_at, duration_s=300)
    last_greeted_at = wake.end_at - timedelta(minutes=30)  # inside the 4h cooldown
    decision = evaluate_greeting(
        [rest, wake], now=wake.end_at, last_greeted_at=last_greeted_at, policy=policy
    )
    assert decision.should_greet is False
    assert decision.reason == "cooldown_active"


def test_a_greeting_outside_the_cooldown_window_is_allowed_again() -> None:
    policy = GreetingPolicy(timezone=UTC, cooldown_s=4 * 60 * 60.0)
    rest = _episode(PresenceState.RESTING, start=_at(7, 0), duration_s=30 * 60)
    wake = _episode(PresenceState.AWAKE, start=rest.end_at, duration_s=300)
    last_greeted_at = wake.end_at - timedelta(hours=5)  # outside the 4h cooldown
    decision = evaluate_greeting(
        [rest, wake], now=wake.end_at, last_greeted_at=last_greeted_at, policy=policy
    )
    assert decision.should_greet is True


def test_resting_that_is_still_ongoing_is_not_a_wake_transition() -> None:
    """The most recent episode must actually BE awake - a still-ongoing rest
    is not a wake, however long it has held."""
    policy = GreetingPolicy(timezone=UTC)
    rest = _episode(PresenceState.RESTING, start=_at(0), duration_s=8 * 60 * 60)
    decision = evaluate_greeting([rest], now=rest.end_at, last_greeted_at=None, policy=policy)
    assert decision.should_greet is False
    assert decision.reason == "not_currently_awake"


def test_empty_history_refuses_honestly() -> None:
    policy = GreetingPolicy(timezone=UTC)
    decision = evaluate_greeting([], now=_at(8), last_greeted_at=None, policy=policy)
    assert decision.should_greet is False
    assert decision.reason == "no_history"


def test_default_timezone_is_europe_istanbul() -> None:
    assert str(DEFAULT_TIMEZONE) == "Europe/Istanbul"
