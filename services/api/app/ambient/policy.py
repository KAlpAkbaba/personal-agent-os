"""The ambient display decision (M18.3 spec §3.9) — pure, and exhaustively tested.

One function, :func:`decide`, and it is the only place in this system that may conclude
"turn the owner's screens off". Everything about it is shaped by one rule from spec §1.4:

    **Uncertain means ON.** UNKNOWN, stale, low confidence, the eye disabled, the camera
    failed, the display state unreadable — none of these produces a display-off. An
    unwanted screen left on is the accepted failure mode; a screen that goes dark because
    the camera broke is not.

The decision table, in the order the checks run (spec §3.9):

    display already off / unknown state            -> none (display_not_on)
    alarm ringing/playing/greeting, or armed < 15m  -> none (alarm_context)
    any holdoff active                              -> none (holdoff:<source>)
    policy.auto_off_enabled false                   -> none (policy_disabled)
    presence UNKNOWN or stale                       -> none (uncertain)
    eye disabled                                    -> none (no_perception)
    AWAY held >= away_after_s, off_when_away         -> display.off (owner_away)
    LIKELY_ASLEEP held >= asleep_after_s and
        confidence >= asleep_min_confidence,
        off_when_asleep                             -> display.off (owner_likely_asleep)
    otherwise                                       -> none (no_condition_met)

The order matters and is not arbitrary: the cheapest, most certain reasons to do nothing
come first, so the reason a decision reports is the FIRST true one — which is the one an
owner asking "why did/didn't my screens go off?" actually wants.

Nothing here reads the database, a device or a clock. The caller assembles
:class:`AmbientInputs` from what it read and passes ``now`` explicitly, so every branch is
reachable from a plain unit test with no fixtures at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from app.alarms.models import AmbientPolicyRow
from app.ambient.holdoff import Holdoff
from app.presence.states import PresenceState

ACTION_DISPLAY_OFF: Final = "display.off"
ACTION_NONE: Final = "none"

REASON_DISPLAY_NOT_ON: Final = "display_not_on"
REASON_ALARM_CONTEXT: Final = "alarm_context"
REASON_POLICY_DISABLED: Final = "policy_disabled"
REASON_UNCERTAIN: Final = "uncertain"
REASON_NO_PERCEPTION: Final = "no_perception"
REASON_OWNER_AWAY: Final = "owner_away"
REASON_OWNER_LIKELY_ASLEEP: Final = "owner_likely_asleep"
REASON_NOT_HELD_LONG_ENOUGH: Final = "not_held_long_enough"
REASON_NO_CONDITION_MET: Final = "no_condition_met"
REASON_OWNER_TEST: Final = "owner_test"
REASON_OWNER_RETURNED: Final = "owner_returned"

#: Spec §3.9: an alarm ARMED to fire within this window is "alarm context" too — darkening
#: the screens ninety seconds before a wake alarm would be undone by the alarm itself.
ALARM_ARMED_CONTEXT_S: Final = 15 * 60


@dataclass(frozen=True, slots=True)
class AmbientPolicy:
    """The owner's settings, detached from the ORM row so :func:`decide` stays pure."""

    auto_off_enabled: bool = False
    off_when_away: bool = True
    off_when_asleep: bool = True
    wake_on_return: bool = True
    away_after_s: int = 900
    asleep_after_s: int = 600
    asleep_min_confidence: float = 0.7
    input_holdoff_s: int = 600
    command_holdoff_s: int = 900
    alarm_holdoff_s: int = 1800
    return_holdoff_s: int = 600
    quiet_hours: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: AmbientPolicyRow) -> AmbientPolicy:
        return cls(
            auto_off_enabled=bool(row.auto_off_enabled),
            off_when_away=bool(row.off_when_away),
            off_when_asleep=bool(row.off_when_asleep),
            wake_on_return=bool(row.wake_on_return),
            away_after_s=int(row.away_after_s),
            asleep_after_s=int(row.asleep_after_s),
            asleep_min_confidence=float(row.asleep_min_confidence),
            input_holdoff_s=int(row.input_holdoff_s),
            command_holdoff_s=int(row.command_holdoff_s),
            alarm_holdoff_s=int(row.alarm_holdoff_s),
            return_holdoff_s=int(row.return_holdoff_s),
            quiet_hours=row.quiet_hours,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "auto_off_enabled": self.auto_off_enabled,
            "off_when_away": self.off_when_away,
            "off_when_asleep": self.off_when_asleep,
            "wake_on_return": self.wake_on_return,
            "away_after_s": self.away_after_s,
            "asleep_after_s": self.asleep_after_s,
            "asleep_min_confidence": self.asleep_min_confidence,
            "input_holdoff_s": self.input_holdoff_s,
            "command_holdoff_s": self.command_holdoff_s,
            "alarm_holdoff_s": self.alarm_holdoff_s,
            "return_holdoff_s": self.return_holdoff_s,
            "quiet_hours": self.quiet_hours,
        }


@dataclass(frozen=True, slots=True)
class AmbientInputs:
    """Everything the decision may look at. Assembled by ``app.ambient.service.tick``.

    ``display_on`` is deliberately ``bool | None``: ``None`` means no device reported a
    display state at all, and that is NOT permission to turn something off.
    """

    #: True / False / None (nobody knows) — ``app.devices.status.DeviceStatusRegistry``.
    display_on: bool | None = None
    #: The presence assertion's EFFECTIVE state (already degraded to UNKNOWN if stale).
    presence_state: PresenceState = PresenceState.UNKNOWN
    presence_confidence: float = 0.0
    #: How long the current run of ``presence_state`` has held, in seconds.
    presence_held_s: float = 0.0
    presence_stale: bool = False
    eye_enabled: bool = False
    #: An alarm is FIRING / PLAYING / GREETING right now, or the device says it is ringing.
    alarm_active: bool = False
    #: When the soonest ARMED alarm is due, if any.
    next_alarm_at: datetime | None = None
    holdoffs: tuple[Holdoff, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Decision:
    """What to do, and the FIRST true reason for it (module docstring)."""

    action: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def acts(self) -> bool:
        return self.action != ACTION_NONE

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action, "reason": self.reason, "evidence": dict(self.evidence)}


def decide(inputs: AmbientInputs, policy: AmbientPolicy, now: datetime) -> Decision:
    """The whole ambient display policy (module docstring's table).

    Returns ``display.off`` only from a sustained, confident, camera-backed inference with
    every holdoff clear and the display known to be on. Every other path returns ``none``
    with the reason.
    """
    evidence: dict[str, Any] = {
        "presence": inputs.presence_state.value,
        "confidence": round(inputs.presence_confidence, 3),
        "held_s": round(inputs.presence_held_s, 1),
        "eye_enabled": inputs.eye_enabled,
        "display_on": inputs.display_on,
    }

    # 1. The display must be known to be ON. `None` (nobody reported) is not "on".
    if inputs.display_on is not True:
        return Decision(ACTION_NONE, REASON_DISPLAY_NOT_ON, evidence)

    # 2. Alarm context: something is waking the owner, or is about to.
    if inputs.alarm_active:
        return Decision(ACTION_NONE, REASON_ALARM_CONTEXT, {**evidence, "alarm": "active"})
    if inputs.next_alarm_at is not None:
        due_in = (inputs.next_alarm_at - now).total_seconds()
        if 0 <= due_in <= ALARM_ARMED_CONTEXT_S:
            return Decision(
                ACTION_NONE, REASON_ALARM_CONTEXT, {**evidence, "alarm_due_in_s": round(due_in)}
            )

    # 3. Holdoffs — physical input and owner commands outrank every inference (spec §1.3).
    if inputs.holdoffs:
        first = inputs.holdoffs[0]
        return Decision(
            ACTION_NONE,
            f"holdoff:{first.source}",
            {**evidence, "holdoff_until": first.until.isoformat()},
        )

    # 4. The owner has not turned automatic display-off on.
    if not policy.auto_off_enabled:
        return Decision(ACTION_NONE, REASON_POLICY_DISABLED, evidence)

    # 5. Uncertainty never darkens a screen (spec §1.4).
    if inputs.presence_stale or inputs.presence_state is PresenceState.UNKNOWN:
        return Decision(ACTION_NONE, REASON_UNCERTAIN, evidence)

    # 6. No camera means no inference. A failed or disabled eye is not a sleeping owner.
    if not inputs.eye_enabled:
        return Decision(ACTION_NONE, REASON_NO_PERCEPTION, evidence)

    # 7. Sustained AWAY.
    if inputs.presence_state is PresenceState.AWAY and policy.off_when_away:
        if inputs.presence_held_s >= policy.away_after_s:
            return Decision(ACTION_DISPLAY_OFF, REASON_OWNER_AWAY, evidence)
        return Decision(
            ACTION_NONE,
            REASON_NOT_HELD_LONG_ENOUGH,
            {**evidence, "needed_s": policy.away_after_s},
        )

    # 8. Sustained AND confident LIKELY_ASLEEP. Both, because "likely" is the whole point:
    #    the system says LIKELY_ASLEEP with a confidence, never OWNER_IS_ASLEEP.
    if inputs.presence_state is PresenceState.LIKELY_ASLEEP and policy.off_when_asleep:
        if (
            inputs.presence_held_s >= policy.asleep_after_s
            and inputs.presence_confidence >= policy.asleep_min_confidence
        ):
            return Decision(ACTION_DISPLAY_OFF, REASON_OWNER_LIKELY_ASLEEP, evidence)
        return Decision(
            ACTION_NONE,
            REASON_NOT_HELD_LONG_ENOUGH,
            {
                **evidence,
                "needed_s": policy.asleep_after_s,
                "needed_confidence": policy.asleep_min_confidence,
            },
        )

    return Decision(ACTION_NONE, REASON_NO_CONDITION_MET, evidence)


def holdoff_seconds(policy: AmbientPolicy) -> dict[str, int]:
    """The policy's own holdoff durations, keyed by ``app.ambient.holdoff``'s sources."""
    from app.ambient.holdoff import (
        SOURCE_ALARM_WAKE,
        SOURCE_INPUT,
        SOURCE_OWNER_COMMAND,
        SOURCE_OWNER_RETURN,
    )

    return {
        SOURCE_INPUT: policy.input_holdoff_s,
        SOURCE_OWNER_COMMAND: policy.command_holdoff_s,
        SOURCE_ALARM_WAKE: policy.alarm_holdoff_s,
        SOURCE_OWNER_RETURN: policy.return_holdoff_s,
    }


def seconds_until(moment: datetime, now: datetime) -> float:
    return (moment - now).total_seconds()


def within(moment: datetime, now: datetime, seconds: int) -> bool:
    return timedelta(seconds=0) <= (moment - now) <= timedelta(seconds=seconds)


__all__ = [
    "ACTION_DISPLAY_OFF",
    "ACTION_NONE",
    "ALARM_ARMED_CONTEXT_S",
    "REASON_ALARM_CONTEXT",
    "REASON_DISPLAY_NOT_ON",
    "REASON_NOT_HELD_LONG_ENOUGH",
    "REASON_NO_CONDITION_MET",
    "REASON_NO_PERCEPTION",
    "REASON_OWNER_AWAY",
    "REASON_OWNER_LIKELY_ASLEEP",
    "REASON_OWNER_RETURNED",
    "REASON_OWNER_TEST",
    "REASON_POLICY_DISABLED",
    "REASON_UNCERTAIN",
    "AmbientInputs",
    "AmbientPolicy",
    "Decision",
    "decide",
    "holdoff_seconds",
    "seconds_until",
    "within",
]
