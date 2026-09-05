"""Unit tests: app.presence.engine.

Each MUST-hold fusion rule from M18_HOLOGRAPHIC_CORE_SPEC.md §1 gets its own
test:

1. No state is classified from a single observation.
2. A state CHANGE requires sustained evidence over a configurable duration.
3. An observation past its TTL degrades the assertion toward UNKNOWN, never
   "still present".
4. Conflicting signals lower confidence rather than silently picking a
   winner.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.presence.engine import (
    DEFAULT_POLICY,
    FusionPolicy,
    PresenceFusionEngine,
    classify_window,
)
from app.presence.observations import Observation
from app.presence.states import PresenceState

NOW = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)


def _obs(
    *,
    t: float = 0.0,
    present: bool = True,
    conf: float = 0.9,
    activity: str = "medium",
    posture: str = "upright",
    awake: str = "awake",
    source: str = "camera",
) -> Observation:
    return Observation(
        person_present=present,
        presence_confidence=conf,
        activity_level=activity,
        posture=posture,
        awake_state=awake,
        observed_at=NOW + timedelta(seconds=t),
        source=source,
    )


# --------------------------------------------------------- rule 1: no single observation


def test_a_single_observation_never_classifies_a_state() -> None:
    assertion = classify_window([_obs(t=0)], previous=None, now=NOW, policy=DEFAULT_POLICY)
    assert assertion.state == PresenceState.UNKNOWN
    assert assertion.reason == "insufficient_observations"


def test_two_observations_too_close_together_still_do_not_classify() -> None:
    """Enough COUNT, not enough SPAN — a blip is still one instant."""
    observations = [_obs(t=0), _obs(t=0.2)]
    assertion = classify_window(
        observations, previous=None, now=NOW + timedelta(seconds=0.2), policy=DEFAULT_POLICY
    )
    assert assertion.state == PresenceState.UNKNOWN
    assert assertion.reason == "insufficient_window"


def test_enough_observations_over_enough_span_does_classify() -> None:
    observations = [_obs(t=0), _obs(t=25)]
    assertion = classify_window(
        observations, previous=None, now=NOW + timedelta(seconds=25), policy=DEFAULT_POLICY
    )
    assert assertion.state == PresenceState.PRESENT
    assert assertion.confidence > 0.0
    assert len(assertion.signals) == 2


# --------------------------------------------------------- rule 2: sustained change


def test_a_state_change_requires_evidence_sustained_over_the_configured_duration() -> None:
    policy = FusionPolicy(
        min_observations=2,
        min_window_s=10.0,
        min_sustain_s=60.0,
        likely_asleep_after_s=10_000.0,
        ttl_s={"camera": 600.0, "input": 600.0, "voice": 600.0, "task": 600.0},
    )

    # Establish a firm PRESENT baseline.
    baseline_obs = [_obs(t=0, activity="medium", posture="upright", awake="awake")]
    baseline_obs.append(_obs(t=15, activity="medium", posture="upright", awake="awake"))
    present = classify_window(
        baseline_obs, previous=None, now=NOW + timedelta(seconds=15), policy=policy
    )
    assert present.state == PresenceState.PRESENT

    # A brief resting-looking window (15s span < 60s min_sustain_s) must NOT
    # flip the state, even though it is classifiable on its own (rule 1 is
    # satisfied — 2 observations spanning >= min_window_s).
    brief_rest = [
        _obs(t=20, activity="none", posture="resting", awake="resting"),
        _obs(t=35, activity="none", posture="resting", awake="resting"),
    ]
    still_present = classify_window(
        brief_rest, previous=present, now=NOW + timedelta(seconds=35), policy=policy
    )
    assert still_present.state == PresenceState.PRESENT, (
        "a 15s resting blip flipped the state despite min_sustain_s=60 - the "
        "sustained-evidence gate did not hold"
    )

    # The SAME kind of evidence, sustained long enough (70s span >=
    # min_sustain_s), DOES flip the state.
    sustained_rest = [
        _obs(t=20, activity="none", posture="resting", awake="resting"),
        _obs(t=35, activity="none", posture="resting", awake="resting"),
        _obs(t=90, activity="none", posture="resting", awake="resting"),
    ]
    resting = classify_window(
        sustained_rest, previous=still_present, now=NOW + timedelta(seconds=90), policy=policy
    )
    assert resting.state == PresenceState.RESTING


def test_resting_long_enough_escalates_to_likely_asleep() -> None:
    """A separate, harder-to-fake threshold on top of RESTING itself
    (``FusionPolicy.likely_asleep_after_s``) - restlessness for a minute is
    not sleep."""
    policy = FusionPolicy(
        min_observations=2,
        min_window_s=10.0,
        min_sustain_s=10.0,
        likely_asleep_after_s=60.0,
        ttl_s={"camera": 600.0, "input": 600.0, "voice": 600.0, "task": 600.0},
    )
    resting_obs = [
        _obs(t=0, activity="none", posture="resting", awake="resting"),
        _obs(t=15, activity="none", posture="resting", awake="resting"),
    ]
    resting = classify_window(
        resting_obs, previous=None, now=NOW + timedelta(seconds=15), policy=policy
    )
    assert resting.state == PresenceState.RESTING

    # Still resting, but now the fresh window itself spans >= likely_asleep_after_s.
    still_resting_obs = [
        _obs(t=15, activity="none", posture="resting", awake="resting"),
        _obs(t=80, activity="none", posture="resting", awake="resting"),
    ]
    likely_asleep = classify_window(
        still_resting_obs, previous=resting, now=NOW + timedelta(seconds=80), policy=policy
    )
    assert likely_asleep.state == PresenceState.LIKELY_ASLEEP


# --------------------------------------------------------- rule 3: staleness -> UNKNOWN


def test_an_expired_observation_degrades_toward_unknown_never_still_present() -> None:
    policy = FusionPolicy(ttl_s={"camera": 90.0, "input": 300.0, "voice": 900.0, "task": 3600.0})
    observations = [_obs(t=0), _obs(t=25)]
    present = classify_window(
        observations, previous=None, now=NOW + timedelta(seconds=25), policy=policy
    )
    assert present.state == PresenceState.PRESENT

    # A short gap in observations (well within the camera TTL) is not the
    # same as staleness: the previous assertion is simply re-reported.
    gap = classify_window([], previous=present, now=NOW + timedelta(seconds=60), policy=policy)
    assert gap.state == PresenceState.PRESENT

    # Once the TTL is actually exceeded, the assertion must degrade to
    # UNKNOWN - never silently continue reporting "present".
    expired = classify_window(
        [], previous=present, now=NOW + timedelta(seconds=400), policy=policy
    )
    assert expired.state == PresenceState.UNKNOWN
    assert expired.confidence == 0.0


# --------------------------------------------------------- rule 4: conflict lowers confidence


def test_conflicting_signals_lower_confidence_and_say_so() -> None:
    policy = DEFAULT_POLICY
    unanimous = [
        _obs(t=0, present=True, conf=0.9),
        _obs(t=25, present=True, conf=0.9),
    ]
    conflicted = [
        _obs(t=0, present=True, conf=0.9),
        _obs(t=25, present=False, conf=0.9),
    ]

    unanimous_assertion = classify_window(
        unanimous, previous=None, now=NOW + timedelta(seconds=25), policy=policy
    )
    conflicted_assertion = classify_window(
        conflicted, previous=None, now=NOW + timedelta(seconds=25), policy=policy
    )

    assert conflicted_assertion.reason == "conflicting_signals"
    assert unanimous_assertion.reason == ""
    assert conflicted_assertion.confidence < unanimous_assertion.confidence, (
        "conflicting signals must lower confidence rather than being ignored"
    )


def test_a_mild_conflict_still_produces_a_state_at_reduced_confidence() -> None:
    """Rule 4's other half: disagreement is not a refusal to answer. A
    majority vote still classifies, just with confidence pulled down."""
    policy = DEFAULT_POLICY
    # 4 observations, weight exactly at the conflict threshold (1 of 4
    # disagrees => 25% weighted disagreement).
    observations = [
        _obs(t=0, present=True, conf=0.99, activity="low", awake="awake"),
        _obs(t=7, present=True, conf=0.99, activity="low", awake="awake"),
        _obs(t=14, present=True, conf=0.99, activity="low", awake="awake"),
        _obs(t=25, present=False, conf=0.99),
    ]
    unanimous = [
        _obs(t=0, present=True, conf=0.99, activity="low", awake="awake"),
        _obs(t=7, present=True, conf=0.99, activity="low", awake="awake"),
        _obs(t=14, present=True, conf=0.99, activity="low", awake="awake"),
        _obs(t=25, present=True, conf=0.99, activity="low", awake="awake"),
    ]

    conflicted_assertion = classify_window(
        observations, previous=None, now=NOW + timedelta(seconds=25), policy=policy
    )
    unanimous_assertion = classify_window(
        unanimous, previous=None, now=NOW + timedelta(seconds=25), policy=policy
    )

    assert conflicted_assertion.reason == "conflicting_signals"
    assert conflicted_assertion.state != PresenceState.UNKNOWN, (
        "a majority vote should still classify rather than refusing to answer"
    )
    assert conflicted_assertion.confidence < unanimous_assertion.confidence


# --------------------------------------------------------- the stateful engine


def test_engine_reports_changed_only_on_genuine_transitions() -> None:
    engine = PresenceFusionEngine()
    _, changed1 = engine.add_observation(_obs(t=0), now=NOW)
    # still below min_observations/min_window individually, but the engine
    # accumulates a buffer, so the SECOND call already has two observations.
    assertion2, changed2 = engine.add_observation(_obs(t=25), now=NOW + timedelta(seconds=25))
    assertion3, changed3 = engine.add_observation(_obs(t=30), now=NOW + timedelta(seconds=30))

    assert assertion2.state == PresenceState.PRESENT
    assert changed2 is True  # UNKNOWN -> PRESENT
    assert assertion3.state == PresenceState.PRESENT
    assert changed3 is False  # still PRESENT, not a new transition


def test_engine_episodes_track_uninterrupted_runs() -> None:
    engine = PresenceFusionEngine()
    # The very first observation alone cannot classify anything (rule 1), so
    # the engine's own history necessarily opens on an UNKNOWN episode before
    # settling into PRESENT once there is enough evidence.
    engine.add_observation(_obs(t=0), now=NOW)
    engine.add_observation(_obs(t=25), now=NOW + timedelta(seconds=25))
    engine.add_observation(_obs(t=40), now=NOW + timedelta(seconds=40))

    episodes = engine.episodes()
    assert [e.state for e in episodes] == [PresenceState.UNKNOWN, PresenceState.PRESENT]
    # The PRESENT episode was updated in place across the last two calls
    # rather than duplicated (t=25 -> t=40), so its duration reflects both.
    assert episodes[-1].duration_s == 15.0
