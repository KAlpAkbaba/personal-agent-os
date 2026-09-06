"""Presence fusion: observations in, one honest assertion out
(M18_HOLOGRAPHIC_CORE_SPEC.md §1).

Four rules are non-negotiable and each has a direct unit test in
``tests/unit/test_presence_engine.py``:

1. **No state from a single observation.** ``classify_window`` requires both
   a minimum COUNT of fresh observations and a minimum wall-clock SPAN
   between the earliest and latest of them — two readings a tenth of a
   second apart are still, functionally, one instant.
2. **A state CHANGE requires sustained evidence over a configurable
   duration.** Being classifiable at all (rule 1) is a lower bar than being
   allowed to overwrite the current state — ``FusionPolicy.min_sustain_s`` is
   checked separately and is typically larger than ``min_window_s``.
3. **An observation past its TTL is not evidence about now.** Staleness is
   per-source (a camera reading five minutes old is stale; a task-derived
   reading an hour old may still be informative) — mirrors
   ``app.worldmodel.state.STALE_AFTER`` being per truth-kind rather than one
   global number. An expired observation drops out of the window; if that
   empties the window below the classification threshold, the assertion
   degrades toward UNKNOWN rather than freezing on "still present".
4. **Conflicting signals lower confidence; they never silently pick a
   winner.** A majority vote still produces a state (the alternative —
   refusing to answer at all — is less honest than a low-confidence answer),
   but disagreement multiplies confidence down, and the assertion's
   ``reason`` records that it happened.

Everything above is DATA on :class:`FusionPolicy`, not a magic number buried
in a branch (spec §1: "the duration required is part of the policy, not a
magic number in a branch") — a test can construct a policy with different
thresholds without touching this module's control flow.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Final

from app.presence.observations import Observation
from app.presence.states import PresenceAssertion, PresenceState, unknown_assertion

#: Default per-source trust lifespan (seconds). A camera frame is only
#: evidence about "right now"; a task-derived signal ("an owner task started
#: five minutes ago") stays informative much longer.
DEFAULT_TTL_S: Final[dict[str, float]] = {
    "camera": 90.0,
    "input": 5 * 60.0,
    "voice": 15 * 60.0,
    "task": 60 * 60.0,
}

_REST_LIKE = (PresenceState.RESTING, PresenceState.LIKELY_ASLEEP)


@dataclass(frozen=True, slots=True)
class FusionPolicy:
    """Every threshold the fusion rules above depend on. Inspectable and
    testable — a caller can construct one with tighter thresholds for a test
    without editing ``classify_window`` itself."""

    ttl_s: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TTL_S))
    #: Rule 1: minimum fresh-observation count.
    min_observations: int = 2
    #: Rule 1: minimum wall-clock span the fresh observations must cover.
    min_window_s: float = 20.0
    #: Rule 2: minimum span required before a state CHANGE is accepted.
    min_sustain_s: float = 45.0
    #: How long sustained RESTING must hold before it escalates to
    #: LIKELY_ASLEEP — a longer, harder-to-fake threshold than RESTING
    #: itself, because sitting still for a minute is not sleep.
    likely_asleep_after_s: float = 20 * 60.0
    #: Rule 4: the weighted-disagreement fraction above which signals count
    #: as genuinely conflicting rather than an ordinary noisy majority.
    conflict_disagreement_threshold: float = 0.25
    #: Rule 4: confidence multiplier applied when signals conflict.
    conflict_confidence_multiplier: float = 0.5
    #: A classification whose resulting confidence falls below this is
    #: reported as UNKNOWN rather than a low-confidence guess dressed up as
    #: a state.
    min_confidence: float = 0.35


DEFAULT_POLICY = FusionPolicy()


@dataclass(frozen=True, slots=True)
class PresenceEpisode:
    """One uninterrupted run of a single state.

    ``app.presence.greeting`` needs "how long has RESTING held, and how long
    has AWAKE held since" without re-deriving it from raw assertions each
    time it is asked — this is that pre-computed answer.
    """

    state: PresenceState
    start_at: datetime
    end_at: datetime
    confidence: float

    @property
    def duration_s(self) -> float:
        return max(0.0, (_aware(self.end_at) - _aware(self.start_at)).total_seconds())


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _fresh(
    observations: Iterable[Observation], *, now: datetime, policy: FusionPolicy
) -> list[Observation]:
    out: list[Observation] = []
    for obs in observations:
        ttl = policy.ttl_s.get(obs.source, policy.ttl_s.get("input", 300.0))
        observed = _aware(obs.observed_at)
        if (now - observed).total_seconds() <= ttl:
            out.append(obs)
    return out


def _majority(values: Iterable[str]) -> str:
    return Counter(values).most_common(1)[0][0]


def _presence_vote(fresh: Sequence[Observation], policy: FusionPolicy) -> tuple[float, bool]:
    """Weighted fraction voting ``person_present``, and whether the vote is
    genuinely conflicted (rule 4)."""
    total = sum(o.presence_confidence for o in fresh)
    if total <= 0:
        return 0.0, False
    present_weight = sum(o.presence_confidence for o in fresh if o.person_present)
    fraction = present_weight / total
    majority_present = fraction >= 0.5
    disagreeing_weight = sum(
        o.presence_confidence for o in fresh if o.person_present != majority_present
    )
    conflict = (disagreeing_weight / total) >= policy.conflict_disagreement_threshold
    return fraction, conflict


def _base_confidence(fresh: Sequence[Observation], present_fraction: float) -> float:
    avg_conf = sum(o.presence_confidence for o in fresh) / len(fresh)
    # Distance from the 50/50 line: a unanimous 0.9-confidence vote reads
    # more confident than a bare 0.51 majority carrying the same average.
    decisiveness = abs(present_fraction - 0.5) * 2.0
    return _clamp01(avg_conf * (0.5 + 0.5 * decisiveness))


def _bucket(
    *,
    present_fraction: float,
    activity: str,
    posture: str,
    awake: str,
    previous: PresenceAssertion | None,
    prior_rest_duration_s: float,
    policy: FusionPolicy,
) -> PresenceState:
    present = present_fraction >= 0.5
    prev_state = previous.state if previous is not None else None

    if not present:
        return PresenceState.AWAY

    if prev_state == PresenceState.AWAY:
        # A transitional marker, never skipped straight to PRESENT — the
        # owner asked for "returned" to be observable in its own right
        # (spec §1 state list), not merely inferred after the fact.
        return PresenceState.RETURNED

    resting_evidence = posture == "resting" and awake == "resting" and activity in ("none", "low")
    awake_evidence = awake == "awake" and activity != "none"

    if resting_evidence:
        # LIKELY_ASLEEP requires the RESTING/LIKELY_ASLEEP run to have
        # already held for `likely_asleep_after_s` - not merely "the
        # previous reading also happened to be resting" (that would
        # escalate on the very next classification, however soon it
        # arrived, making the threshold decorative rather than enforced).
        if prev_state in _REST_LIKE and prior_rest_duration_s >= policy.likely_asleep_after_s:
            return PresenceState.LIKELY_ASLEEP
        return PresenceState.RESTING

    if awake_evidence and prev_state in (*_REST_LIKE, PresenceState.AWAKE):
        return PresenceState.AWAKE

    return PresenceState.PRESENT


def classify_window(
    observations: Sequence[Observation],
    *,
    previous: PresenceAssertion | None,
    now: datetime,
    policy: FusionPolicy = DEFAULT_POLICY,
) -> PresenceAssertion:
    """Pure function: the window of recent observations a caller has kept
    (typically :class:`PresenceFusionEngine`'s buffer) plus the previous
    assertion, in; one honest :class:`PresenceAssertion`, out."""
    fresh = sorted(_fresh(observations, now=now, policy=policy), key=lambda o: o.observed_at)

    if len(fresh) < policy.min_observations:
        return _degrade(previous, now=now, reason="insufficient_observations")

    span = (fresh[-1].observed_at - fresh[0].observed_at).total_seconds()
    if span < policy.min_window_s:
        return _degrade(previous, now=now, reason="insufficient_window")

    present_fraction, conflict = _presence_vote(fresh, policy)
    activity = _majority(o.activity_level for o in fresh)
    posture = _majority(o.posture for o in fresh)
    awake = _majority(o.awake_state for o in fresh)

    confidence = _base_confidence(fresh, present_fraction)
    if conflict:
        confidence *= policy.conflict_confidence_multiplier

    prior_rest_duration_s = 0.0
    if previous is not None and previous.state in _REST_LIKE:
        started = _aware(previous.state_started_at)
        prior_rest_duration_s = (fresh[-1].observed_at - started).total_seconds()

    candidate = _bucket(
        present_fraction=present_fraction,
        activity=activity,
        posture=posture,
        awake=awake,
        previous=previous,
        prior_rest_duration_s=prior_rest_duration_s,
        policy=policy,
    )

    if confidence < policy.min_confidence:
        candidate = PresenceState.UNKNOWN

    # Rule 2: a CHANGE additionally requires the fresh window to span at
    # least min_sustain_s — a momentary blip must not flip the picture even
    # though it was enough evidence to be classifiable at all (rule 1). This
    # does NOT apply when the previous assertion was itself UNKNOWN (no
    # evidence yet, or expired): there is no established state to protect
    # from flapping, and rule 1's own window/count check already guards the
    # very first classification. Without this exception, an engine's FIRST
    # ever real reading always reverted back to UNKNOWN whenever
    # min_window_s < min_sustain_s (the sensible default relationship) — an
    # engine that can never form its first opinion.
    if (
        previous is not None
        and previous.state != PresenceState.UNKNOWN
        and candidate != previous.state
        and span < policy.min_sustain_s
    ):
        candidate = previous.state
        confidence = min(confidence, previous.confidence)

    stale_after_s = min(policy.ttl_s.get(o.source, policy.ttl_s.get("input", 300.0)) for o in fresh)

    # The run "started" when THIS candidate's evidence began, unless it is
    # simply reasserting the state that was already current - then the
    # original start carries forward (module docstring on PresenceAssertion
    # .state_started_at: this is what lets LIKELY_ASLEEP's own threshold,
    # above, measure a real elapsed duration instead of resetting every call).
    if previous is not None and candidate == previous.state:
        state_started_at = previous.state_started_at
    else:
        state_started_at = fresh[0].observed_at

    return PresenceAssertion(
        state=candidate,
        confidence=confidence,
        signals=tuple(fresh),
        observed_at=fresh[-1].observed_at,
        state_started_at=state_started_at,
        stale_after_s=stale_after_s,
        reason="conflicting_signals" if conflict else "",
    )


def _degrade(
    previous: PresenceAssertion | None, *, now: datetime, reason: str
) -> PresenceAssertion:
    """Not enough fresh evidence to classify anything right now.

    If a previous assertion exists and has not itself gone stale, a
    momentary gap in observations is not the same as staleness — keep
    reporting it (refreshed with nothing new). Once IT goes stale too, both
    paths converge on UNKNOWN (spec §1: "An expired observation degrades to
    UNKNOWN, never to 'still present'").
    """
    if previous is not None and not previous.is_stale(now=now):
        return previous
    return unknown_assertion(now=now, reason=reason)


class PresenceFusionEngine:
    """The stateful wrapper: keeps a bounded observation buffer and the
    resulting episode history, and calls :func:`classify_window` on every new
    observation. Thread-safe; mirrors ``app.uistate.publisher.UiStatePublisher``
    — bounded, in-process, never durable (the Activity Ledger records
    meaningful transitions; this is only what is happening now)."""

    def __init__(
        self,
        *,
        policy: FusionPolicy = DEFAULT_POLICY,
        buffer_lookback_s: float = 30 * 60.0,
        episode_limit: int = 500,
    ) -> None:
        self._policy = policy
        self._buffer_lookback_s = buffer_lookback_s
        self._episode_limit = episode_limit
        self._lock = threading.Lock()
        self._observations: list[Observation] = []
        self._current: PresenceAssertion | None = None
        self._episodes: list[PresenceEpisode] = []

    @property
    def policy(self) -> FusionPolicy:
        return self._policy

    def current(self) -> PresenceAssertion | None:
        with self._lock:
            return self._current

    def episodes(self, *, limit: int | None = None) -> list[PresenceEpisode]:
        with self._lock:
            items = list(self._episodes)
        return items[-limit:] if limit else items

    def add_observation(
        self, observation: Observation, *, now: datetime | None = None
    ) -> tuple[PresenceAssertion, bool]:
        """Feed one observation; returns ``(assertion, changed)`` where
        ``changed`` is True only when the resulting state differs from what
        was current before this call — the caller's cue for "this is a
        meaningful transition" (spec §5: write ledger events for meaningful
        transitions only, never every observation)."""
        moment = now or datetime.now(UTC)
        with self._lock:
            self._observations.append(observation)
            cutoff = moment - timedelta(seconds=self._buffer_lookback_s)
            self._observations = [o for o in self._observations if _aware(o.observed_at) >= cutoff]

            previous = self._current
            new_assertion = classify_window(
                self._observations, previous=previous, now=moment, policy=self._policy
            )
            # "Changed" means a caller now has something worth acting on. The
            # very first call or two typically classifies as UNKNOWN (rule 1:
            # not enough evidence yet) — that is not itself a meaningful
            # transition (there was no PREVIOUS real state to transition
            # away from), so it must not trigger a ledger write on its own.
            if previous is None:
                changed = new_assertion.state != PresenceState.UNKNOWN
            else:
                changed = previous.state != new_assertion.state
            self._current = new_assertion

            if self._episodes and self._episodes[-1].state == new_assertion.state:
                self._episodes[-1] = replace(
                    self._episodes[-1],
                    end_at=new_assertion.observed_at,
                    confidence=new_assertion.confidence,
                )
            else:
                self._episodes.append(
                    PresenceEpisode(
                        state=new_assertion.state,
                        start_at=new_assertion.observed_at,
                        end_at=new_assertion.observed_at,
                        confidence=new_assertion.confidence,
                    )
                )
                if len(self._episodes) > self._episode_limit:
                    self._episodes = self._episodes[-self._episode_limit :]

            return new_assertion, changed

    def last_observation_at(self, *, source: str | None = None) -> datetime | None:
        """When the most recent buffered observation (optionally of one ``source``) was
        made, or None. The buffer is bounded by ``buffer_lookback_s``, so "None" means
        "nothing in the last half hour", which is what a live-state answer needs to say
        (docs/M18_ACTION_CONTRACT.md §4: ``eye.last_observation_age_s``)."""
        with self._lock:
            candidates = [
                _aware(o.observed_at)
                for o in self._observations
                if source is None or o.source == source
            ]
        return max(candidates) if candidates else None

    def invalidate_source(
        self, source: str, *, now: datetime | None = None, reason: str = "source_invalidated"
    ) -> PresenceAssertion:
        """Drop every buffered observation from ``source`` and degrade the current
        assertion to UNKNOWN with ``reason`` (docs/M18_ACTION_CONTRACT.md §5.4).

        Used when the owner disables the Active Eye: a state fused from camera frames
        the owner just forbade is not allowed to be reported for the rest of its TTL.
        The other sources' observations stay in the buffer; the next observation from
        any of them re-classifies over what remains. Recorded as an UNKNOWN episode so
        the greeting policy sees the gap rather than an unbroken run.
        """
        moment = now or datetime.now(UTC)
        with self._lock:
            self._observations = [o for o in self._observations if o.source != source]
            assertion = unknown_assertion(now=moment, reason=reason)
            self._current = assertion
            if not self._episodes or self._episodes[-1].state != PresenceState.UNKNOWN:
                self._episodes.append(
                    PresenceEpisode(
                        state=PresenceState.UNKNOWN,
                        start_at=moment,
                        end_at=moment,
                        confidence=0.0,
                    )
                )
                if len(self._episodes) > self._episode_limit:
                    self._episodes = self._episodes[-self._episode_limit :]
            return assertion

    def reset(self) -> None:
        with self._lock:
            self._observations.clear()
            self._current = None
            self._episodes.clear()


_engine = PresenceFusionEngine()


def get_engine() -> PresenceFusionEngine:
    return _engine


def set_engine(engine: PresenceFusionEngine) -> None:
    """Tests (and any alternative runtime) swap the process-wide engine."""
    global _engine
    _engine = engine


__all__ = [
    "DEFAULT_POLICY",
    "DEFAULT_TTL_S",
    "FusionPolicy",
    "PresenceEpisode",
    "PresenceFusionEngine",
    "classify_window",
    "get_engine",
    "set_engine",
]
