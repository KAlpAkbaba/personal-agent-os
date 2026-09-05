"""Unit tests: app.presence.states.

Covers the one rule this module exists to enforce: confidence is always a
number the caller can trust (clamped, never invented), and staleness
degrades a state toward UNKNOWN rather than letting a caller keep reading a
stale assertion as "still present" (M18_HOLOGRAPHIC_CORE_SPEC.md §1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.presence.observations import Observation
from app.presence.states import PresenceAssertion, PresenceState, unknown_assertion

NOW = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)


def _observation(**overrides) -> Observation:
    defaults = dict(
        person_present=True,
        presence_confidence=0.9,
        activity_level="low",
        posture="upright",
        awake_state="awake",
        observed_at=NOW,
        source="camera",
    )
    defaults.update(overrides)
    return Observation(**defaults)


def test_confidence_is_always_clamped_to_0_1() -> None:
    high = PresenceAssertion(
        state=PresenceState.PRESENT,
        confidence=5.0,
        signals=(),
        observed_at=NOW,
        state_started_at=NOW,
        stale_after_s=60.0,
    )
    low = PresenceAssertion(
        state=PresenceState.PRESENT,
        confidence=-3.0,
        signals=(),
        observed_at=NOW,
        state_started_at=NOW,
        stale_after_s=60.0,
    )
    assert high.confidence == 1.0
    assert low.confidence == 0.0


def test_never_certainty_the_state_is_always_an_inference() -> None:
    """The spec's own example: LIKELY_ASLEEP confidence=0.86, never a claim
    of fact. Nothing about the type lets a caller assert certainty."""
    assertion = PresenceAssertion(
        state=PresenceState.LIKELY_ASLEEP,
        confidence=0.86,
        signals=(_observation(),),
        observed_at=NOW,
        state_started_at=NOW,
        stale_after_s=600.0,
    )
    assert assertion.state == PresenceState.LIKELY_ASLEEP
    assert assertion.confidence == 0.86
    assert not assertion.is_stale(now=NOW)


def test_a_fresh_assertion_is_not_stale() -> None:
    assertion = PresenceAssertion(
        state=PresenceState.PRESENT,
        confidence=0.8,
        signals=(_observation(),),
        observed_at=NOW,
        state_started_at=NOW,
        stale_after_s=90.0,
    )
    assert not assertion.is_stale(now=NOW + timedelta(seconds=30))
    assert assertion.effective_state(now=NOW + timedelta(seconds=30)) == PresenceState.PRESENT
    assert assertion.effective_confidence(now=NOW + timedelta(seconds=30)) == 0.8


def test_an_expired_assertion_degrades_to_unknown_never_still_present() -> None:
    """M18 spec §1: "An expired observation degrades to UNKNOWN, never to
    'still present'." The raw state/confidence are still inspectable
    (as_dict exposes both), but the effective reading is honest."""
    assertion = PresenceAssertion(
        state=PresenceState.PRESENT,
        confidence=0.9,
        signals=(_observation(),),
        observed_at=NOW,
        state_started_at=NOW,
        stale_after_s=60.0,
    )
    later = NOW + timedelta(minutes=40)
    assert assertion.is_stale(now=later)
    assert assertion.effective_state(now=later) == PresenceState.UNKNOWN
    assert assertion.effective_confidence(now=later) == 0.0

    as_dict = assertion.as_dict(now=later)
    assert as_dict["state"] == "unknown"
    assert as_dict["stale"] is True
    # the raw evidence is still there for anyone who wants to know what it
    # thought before it went stale.
    assert as_dict["raw_state"] == "present"
    assert as_dict["raw_confidence"] == 0.9


def test_unknown_assertion_carries_zero_confidence_and_no_signals() -> None:
    assertion = unknown_assertion(now=NOW, reason="no_evidence")
    assert assertion.state == PresenceState.UNKNOWN
    assert assertion.confidence == 0.0
    assert assertion.signals == ()
    assert not assertion.is_stale(now=NOW)  # stale_after_s == 0, but "now" == observed_at
    assert assertion.is_stale(now=NOW + timedelta(seconds=1))
