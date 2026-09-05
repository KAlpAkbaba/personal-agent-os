"""Presence and wake states (M18_HOLOGRAPHIC_CORE_SPEC.md §1).

Two axes collapse into one flat vocabulary on purpose: ``PRESENT``/``AWAY``/
``RETURNED`` track whether the owner is physically here, ``AWAKE``/
``RESTING``/``LIKELY_ASLEEP`` track whether they are alert once they are
here. Only one can be "current" at a time because a single Presence Engine
(``app.presence.engine``) owns one timeline — the same shape choice as
``app.uistate.contract.UiState`` being one flat enum rather than a cross
product of independent axes.

Nothing here is ever asserted as certainty (spec §1: "the system says
LIKELY_ASLEEP confidence=0.86, never OWNER_IS_ASLEEP"). A ``PresenceAssertion``
is deliberately shaped like ``app.worldmodel.state.Fact``: a value, a
confidence, the evidence behind it, and staleness computed lazily at read
time rather than revisited on a timer (that module's docstring explains why
lazy computation is the fix, not an optimisation — a flag only ever written
once goes stale in exactly the way this module must not).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.presence.observations import Observation


class PresenceState(StrEnum):
    """The closed vocabulary. Extending this is a contract change — the same
    discipline as ``app.uistate.contract.UiState``."""

    PRESENT = "present"
    AWAY = "away"
    RETURNED = "returned"
    AWAKE = "awake"
    RESTING = "resting"
    LIKELY_ASLEEP = "likely_asleep"
    UNKNOWN = "unknown"


PRESENCE_STATES: tuple[str, ...] = tuple(s.value for s in PresenceState)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class PresenceAssertion:
    """One inference, never a fact.

    ``confidence`` is clamped to 0..1 on construction so a caller can never
    forward an out-of-range number into a spoken sentence or a UI event.
    ``signals`` are the fresh observations that produced this assertion (see
    ``app.presence.engine.classify_window``), so "why does it think that?" is
    always answerable from the assertion itself, never a second lookup.
    """

    state: PresenceState
    confidence: float
    signals: tuple[Observation, ...]
    observed_at: datetime
    #: When the CURRENT UNINTERRUPTED run of ``state`` began — carried
    #: forward across calls that reassert the same state, reset whenever the
    #: state actually changes. This is what lets ``app.presence.engine``
    #: answer "how long has this held?" (e.g. RESTING escalating to
    #: LIKELY_ASLEEP only once it has held long enough) without a caller
    #: re-deriving it from raw observation history every time.
    state_started_at: datetime
    #: How long this assertion may be read as current before a caller must
    #: degrade it toward UNKNOWN itself. Set per-assertion (not a single
    #: global constant) because the fresh evidence behind one assertion may
    #: mix sources with different trustworthy lifespans — see
    #: ``app.presence.engine.FusionPolicy.ttl_s``.
    stale_after_s: float
    #: A short machine token naming why this particular state/confidence was
    #: chosen (e.g. "conflicting_signals", "insufficient_window") — never
    #: prose, never spoken verbatim (mirrors app.uistate metadata being
    #: content-free tokens, not sentences).
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _clamp01(self.confidence))

    def is_stale(self, *, now: datetime | None = None) -> bool:
        """True once this assertion is too old to be reported as current.

        Staleness degrades toward UNKNOWN, never toward "still present" (spec
        §1: "An expired observation degrades to UNKNOWN, never to 'still
        present'") — enforced here so every reader (routes, world model,
        greeting policy) gets the same answer rather than reimplementing the
        age check.
        """
        moment = now or datetime.now(UTC)
        observed = self.observed_at if self.observed_at.tzinfo else self.observed_at.replace(
            tzinfo=UTC
        )
        return (moment - observed).total_seconds() > self.stale_after_s

    def effective_state(self, *, now: datetime | None = None) -> PresenceState:
        """What a reader should actually show: the asserted state, or
        ``UNKNOWN`` once it has gone stale."""
        return PresenceState.UNKNOWN if self.is_stale(now=now) else self.state

    def effective_confidence(self, *, now: datetime | None = None) -> float:
        return 0.0 if self.is_stale(now=now) else self.confidence

    def held_for_s(self, *, now: datetime | None = None) -> float:
        """How long the current run of ``state`` has held, as of ``now``."""
        moment = now or datetime.now(UTC)
        started = self.state_started_at if self.state_started_at.tzinfo else (
            self.state_started_at.replace(tzinfo=UTC)
        )
        return max(0.0, (moment - started).total_seconds())

    def as_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        return {
            "state": self.effective_state(now=now).value,
            "raw_state": self.state.value,
            "confidence": self.effective_confidence(now=now),
            "raw_confidence": self.confidence,
            "stale": self.is_stale(now=now),
            "observed_at": _iso(self.observed_at),
            "state_started_at": _iso(self.state_started_at),
            "held_for_s": self.held_for_s(now=now),
            "signal_count": len(self.signals),
            "sources": sorted({s.source for s in self.signals}),
            "reason": self.reason,
        }


def unknown_assertion(
    *, now: datetime | None = None, reason: str = "no_evidence"
) -> PresenceAssertion:
    """The honest default: no evidence yet, so no state — never guessed."""
    moment = now or datetime.now(UTC)
    return PresenceAssertion(
        state=PresenceState.UNKNOWN,
        confidence=0.0,
        signals=(),
        observed_at=moment,
        state_started_at=moment,
        stale_after_s=0.0,
        reason=reason,
    )


__all__ = [
    "PRESENCE_STATES",
    "PresenceAssertion",
    "PresenceState",
    "unknown_assertion",
]
