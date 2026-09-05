"""The morning/return greeting policy (M18_HOLOGRAPHIC_CORE_SPEC.md §1).

A greeting is a TRANSITION, never a detection: a prior sustained
RESTING/LIKELY_ASLEEP period, then sustained AWAKE evidence, a plausible time
context, and no greeting already delivered in the cooldown window — all four,
every time. Data, not scattered conditionals: :class:`GreetingPolicy` is a
plain dataclass and :func:`evaluate_greeting` is one pure function, so a test
can flip a single threshold and see exactly which gate failed, and so
``app.routines`` (M18 item 3, not built by this change — see the milestone's
dependency order in the spec) has one well-tested decision to call rather
than reimplementing "is this actually a morning" itself.

The defining negative case, stated in the spec verbatim: **a brief movement
at 03:00 must NOT produce a morning greeting.** Two gates below catch it
independently:

* the sustained-AWAKE-duration gate — a "brief" movement is by definition too
  short to satisfy ``min_awake_duration_s``;
* the plausible-hour gate — 03:00 is outside any reasonable greeting window,
  regardless of how long the movement lasted.

Either gate alone is enough; both existing means the rule survives even if
one threshold is later retuned in isolation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final
from zoneinfo import ZoneInfo

from app.presence.engine import PresenceEpisode
from app.presence.states import PresenceState

#: Product invariant default (CLAUDE.md: tr-TR first-class; app.models.Owner's
#: own default timezone). Greeting policy only needs SOME timezone to judge
#: "plausible" — presence events themselves are UTC by construction
#: (app.presence.observations.parse_observation always normalizes to UTC). A
#: caller with the owner's actual configured timezone may pass its own
#: GreetingPolicy(timezone=...) once app.config exposes one for this
#: subsystem; this default keeps the policy usable and testable today.
DEFAULT_TIMEZONE: Final[ZoneInfo] = ZoneInfo("Europe/Istanbul")

#: Episode states that count as "the owner was resting" for the purpose of
#: the greeting's prior-rest gate.
REST_STATES: Final[frozenset[PresenceState]] = frozenset(
    {PresenceState.RESTING, PresenceState.LIKELY_ASLEEP}
)


def _default_plausible_hours() -> frozenset[int]:
    # 00:00-04:59 excluded on purpose: the spec's own counterexample is a
    # movement at 03:00. Every other hour is left plausible for a "welcome
    # back" greeting, not only a strict morning window, per spec §1 calling
    # this a "morning/return greeting".
    return frozenset(range(5, 24))


@dataclass(frozen=True, slots=True)
class GreetingPolicy:
    """Every threshold :func:`evaluate_greeting` depends on."""

    #: The prior rest episode must have held at least this long to count as
    #: "a sustained RESTING/LIKELY_ASLEEP period" (spec §1).
    min_rest_duration_s: float = 20 * 60.0
    #: The AWAKE episode must hold at least this long before it is trusted as
    #: real waking rather than the spec's own "brief movement" example.
    min_awake_duration_s: float = 90.0
    #: No second greeting within this window of the last one.
    cooldown_s: float = 4 * 60 * 60.0
    #: Local hours (in ``timezone``) during which a greeting is plausible at
    #: all.
    plausible_hours: frozenset[int] = field(default_factory=_default_plausible_hours)
    timezone: ZoneInfo = DEFAULT_TIMEZONE


DEFAULT_GREETING_POLICY = GreetingPolicy()


@dataclass(frozen=True, slots=True)
class GreetingDecision:
    """Why, not just whether — every refusal names the gate that stopped it,
    so this is directly testable and directly explainable to the owner
    ("neden şimdi değil?") without re-deriving the reasoning."""

    should_greet: bool
    reason: str
    rest_episode: PresenceEpisode | None = None
    wake_episode: PresenceEpisode | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "should_greet": self.should_greet,
            "reason": self.reason,
            "rest_duration_s": self.rest_episode.duration_s if self.rest_episode else None,
            "wake_duration_s": self.wake_episode.duration_s if self.wake_episode else None,
        }


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def evaluate_greeting(
    episodes: Sequence[PresenceEpisode],
    *,
    now: datetime,
    last_greeted_at: datetime | None = None,
    policy: GreetingPolicy = DEFAULT_GREETING_POLICY,
) -> GreetingDecision:
    """``episodes`` is the presence engine's episode history, oldest first
    (``PresenceFusionEngine.episodes()``). Never raises; an empty or
    malformed history simply fails a gate honestly."""
    if last_greeted_at is not None:
        elapsed = (now - _aware(last_greeted_at)).total_seconds()
        if elapsed < policy.cooldown_s:
            return GreetingDecision(False, "cooldown_active")

    local_hour = now.astimezone(policy.timezone).hour
    if local_hour not in policy.plausible_hours:
        return GreetingDecision(False, "implausible_time")

    if not episodes:
        return GreetingDecision(False, "no_history")

    wake_episode = episodes[-1]
    if wake_episode.state != PresenceState.AWAKE:
        return GreetingDecision(False, "not_currently_awake")
    if wake_episode.duration_s < policy.min_awake_duration_s:
        return GreetingDecision(False, "wake_not_sustained", wake_episode=wake_episode)

    if len(episodes) < 2:
        return GreetingDecision(False, "no_prior_rest", wake_episode=wake_episode)
    rest_episode = episodes[-2]
    if rest_episode.state not in REST_STATES:
        return GreetingDecision(False, "no_prior_rest", wake_episode=wake_episode)
    if rest_episode.duration_s < policy.min_rest_duration_s:
        return GreetingDecision(
            False, "rest_not_sustained", rest_episode=rest_episode, wake_episode=wake_episode
        )

    return GreetingDecision(
        True, "sustained_wake_after_rest", rest_episode=rest_episode, wake_episode=wake_episode
    )


__all__ = [
    "DEFAULT_GREETING_POLICY",
    "DEFAULT_TIMEZONE",
    "GreetingDecision",
    "GreetingPolicy",
    "REST_STATES",
    "evaluate_greeting",
]
