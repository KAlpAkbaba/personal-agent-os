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
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.alarms.models import DEFAULT_TIMEZONE, AmbientPolicyRow
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

#: Two owner-initiated darkenings, two holdoffs, because they are not the same act.
#:
#: The device refuses ``desktop.display_off`` while input is recent. That is right for the
#: AUTOMATIC path - the system must not darken a screen someone is working at - and exactly
#: wrong for a command the owner just gave, because giving it IS recent input.
#:
#: ``OWNER_COMMAND_HOLDOFF_S`` is 0: "kapat" means now. The device accepts 0
#: (``MinHoldoffSeconds``) and its guard reads ``idle < holdoff``, so no recency can refuse
#: it - while the separate ``alarm_active`` refusal still stands, which is the one an owner
#: cannot have meant to override. Sending the automatic 120 s here made the owner's own
#: command refusable for two minutes after they issued it: three attempts on 2026-09-09 were
#: refused at 3.8 s, 67.6 s and 88.8 s idle, the screens never went dark, and the only thing
#: the owner could say was "ekran kapama çalışmadı".
#:
#: ``OWNER_TEST_HOLDOFF_S`` stays 5. That path ARMS a darkening for a later tick after warning
#: the owner, so a small guard is deliberate there and its behaviour is already qualified.
OWNER_COMMAND_HOLDOFF_S: Final = 0
OWNER_TEST_HOLDOFF_S: Final = 5
REASON_OWNER_RETURNED: Final = "owner_returned"
#: ADR-0079 §7: "Ekranı açık tut." is in force.
REASON_OWNER_KEEP_ON: Final = "owner_keep_on"
#: ADR-0079 §3: the camera has not delivered inside the grace - degraded perception.
REASON_PERCEPTION_STALE: Final = "perception_stale"

#: B48: the device camera's modes, as the owner may choose them.
CAMERA_MODE_OFF: Final = "off"
CAMERA_MODES: Final[tuple[str, ...]] = ("off", "periodic", "continuous")

QUIET_HOURS_UNSET: Final = "unset"
QUIET_HOURS_INSIDE: Final = "inside"
QUIET_HOURS_OUTSIDE: Final = "outside"

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
    #: ADR-0079 §7. "Ekranı açık tut.": an explicit preference that outranks every
    #: inference, every holdoff and the policy's own switches until the owner lifts it.
    keep_on: bool = False
    #: ADR-0079 §8. LIKELY_ASLEEP must hold this long OUTSIDE the quiet hours; inside
    #: them ``asleep_after_s`` applies. With no quiet hours configured, ``asleep_after_s``
    #: applies everywhere, exactly as before.
    asleep_after_outside_quiet_s: int = 1800
    #: ADR-0079 §3. The newest camera observation behind the assertion may be at most
    #: this old for an off decision. Older is ``perception_stale``: a camera that stopped
    #: delivering is a degraded perception, never an owner who left or fell asleep.
    camera_unknown_grace_s: int = 120
    #: B48: the owner's device-camera mode (``CAMERA_MODES``). Not an input to ``decide``:
    #: the decision reads what the camera DELIVERED (``perception_age_s``), never what the
    #: owner asked it to do.
    camera_mode: str = "off"

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
            keep_on=bool(getattr(row, "keep_on", False)),
            asleep_after_outside_quiet_s=int(
                getattr(row, "asleep_after_outside_quiet_s", 1800) or 1800
            ),
            camera_unknown_grace_s=int(getattr(row, "camera_unknown_grace_s", 120) or 120),
            camera_mode=str(getattr(row, "camera_mode", None) or "off"),
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
            "keep_on": self.keep_on,
            "asleep_after_outside_quiet_s": self.asleep_after_outside_quiet_s,
            "camera_unknown_grace_s": self.camera_unknown_grace_s,
            "camera_mode": self.camera_mode,
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
    #: ADR-0079 §3: seconds since the newest CAMERA observation the fusion engine holds,
    #: or None when it holds none. An assertion can still read AWAY on old frames; this is
    #: what says whether the camera is actually delivering right now.
    perception_age_s: float | None = None


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
        "perception_age_s": (
            round(inputs.perception_age_s, 1) if inputs.perception_age_s is not None else None
        ),
        "quiet_hours": quiet_hours_state(policy, now),
    }

    # 1. The display must be known to be ON. `None` (nobody reported) is not "on".
    if inputs.display_on is not True:
        return Decision(ACTION_NONE, REASON_DISPLAY_NOT_ON, evidence)

    # 1b. "Ekranı açık tut." - the owner's explicit preference outranks every inference,
    #     every holdoff and the policy's own switches (ADR-0079 §7). Only the owner lifts it.
    if policy.keep_on:
        return Decision(ACTION_NONE, REASON_OWNER_KEEP_ON, {**evidence, "keep_on": True})

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

    # 6b. The camera must have DELIVERED recently (ADR-0079 §3). The assertion may still
    #     read AWAY or LIKELY_ASLEEP on the frames it last had; an eye that is enabled but
    #     silent - permission lost, process dead, no usable frame - is a degraded
    #     perception, and a degraded perception darkens nothing.
    if (
        inputs.perception_age_s is None
        or inputs.perception_age_s > policy.camera_unknown_grace_s
    ):
        return Decision(
            ACTION_NONE,
            REASON_PERCEPTION_STALE,
            {**evidence, "grace_s": policy.camera_unknown_grace_s},
        )

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
    #    ADR-0079 §8: the sustain needed depends on the owner's quiet hours - the normal
    #    threshold inside them, the longer one outside them, the normal one everywhere
    #    when none are configured.
    if inputs.presence_state is PresenceState.LIKELY_ASLEEP and policy.off_when_asleep:
        needed = asleep_needed_s(policy, now)
        if (
            inputs.presence_held_s >= needed
            and inputs.presence_confidence >= policy.asleep_min_confidence
        ):
            return Decision(
                ACTION_DISPLAY_OFF, REASON_OWNER_LIKELY_ASLEEP, {**evidence, "needed_s": needed}
            )
        return Decision(
            ACTION_NONE,
            REASON_NOT_HELD_LONG_ENOUGH,
            {
                **evidence,
                "needed_s": needed,
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


def _parse_hhmm(text: Any) -> int:
    """"23:30" -> minutes since local midnight; anything else is a ValueError."""
    raw = str(text).strip()
    hours, _, minutes = raw.partition(":")
    if not hours.isdigit() or not minutes.isdigit():
        raise ValueError(f"not an HH:MM time: {text!r}")
    hour, minute = int(hours), int(minutes)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"not an HH:MM time: {text!r}")
    return hour * 60 + minute


def validate_quiet_hours(value: Any) -> dict[str, Any] | None:
    """The one accepted shape (ADR-0079 §8): ``{"start": "HH:MM", "end": "HH:MM",
    "timezone"?: IANA}`` -> normalised dict; ``None`` / ``{}`` -> None (no window);
    anything else raises ``ValueError`` so no caller can persist a window the decision
    would then silently ignore."""
    if value is None or value == {}:
        return None
    if not isinstance(value, dict):
        raise ValueError("quiet_hours must be an object with start and end")
    start = _parse_hhmm(value.get("start"))
    end = _parse_hhmm(value.get("end"))
    if start == end:
        raise ValueError("quiet_hours start and end must differ")
    timezone = str(value.get("timezone") or DEFAULT_TIMEZONE)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone: {timezone!r}") from exc
    return {
        "start": f"{start // 60:02d}:{start % 60:02d}",
        "end": f"{end // 60:02d}:{end % 60:02d}",
        "timezone": timezone,
    }


def quiet_hours_state(policy: AmbientPolicy, now: datetime) -> str:
    """unset | inside | outside, in the window's own timezone (ADR-0079 §8).

    A window that cannot be read is UNSET, never INSIDE: a malformed setting must not
    lower a threshold. Windows may cross midnight ("23:30"-"07:30").
    """
    window = policy.quiet_hours
    if not isinstance(window, dict) or not window.get("start") or not window.get("end"):
        return QUIET_HOURS_UNSET
    try:
        start = _parse_hhmm(window["start"])
        end = _parse_hhmm(window["end"])
        zone = ZoneInfo(str(window.get("timezone") or DEFAULT_TIMEZONE))
    except (ValueError, KeyError, TypeError, ZoneInfoNotFoundError):
        return QUIET_HOURS_UNSET
    if start == end:
        return QUIET_HOURS_UNSET
    aware = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    local = aware.astimezone(zone)
    minute = local.hour * 60 + local.minute
    inside = start <= minute < end if start < end else (minute >= start or minute < end)
    return QUIET_HOURS_INSIDE if inside else QUIET_HOURS_OUTSIDE


def asleep_needed_s(policy: AmbientPolicy, now: datetime) -> int:
    """How long LIKELY_ASLEEP must hold right now (ADR-0079 §8): the normal threshold
    inside the quiet hours or with none configured, the longer one outside them."""
    if quiet_hours_state(policy, now) == QUIET_HOURS_OUTSIDE:
        return max(int(policy.asleep_after_s), int(policy.asleep_after_outside_quiet_s))
    return int(policy.asleep_after_s)


def seconds_until(moment: datetime, now: datetime) -> float:
    return (moment - now).total_seconds()


def within(moment: datetime, now: datetime, seconds: int) -> bool:
    return timedelta(seconds=0) <= (moment - now) <= timedelta(seconds=seconds)


__all__ = [
    "CAMERA_MODES",
    "CAMERA_MODE_OFF",
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
    "QUIET_HOURS_INSIDE",
    "QUIET_HOURS_OUTSIDE",
    "QUIET_HOURS_UNSET",
    "REASON_OWNER_KEEP_ON",
    "REASON_OWNER_RETURNED",
    "REASON_OWNER_TEST",
    "OWNER_COMMAND_HOLDOFF_S",
    "OWNER_TEST_HOLDOFF_S",
    "REASON_PERCEPTION_STALE",
    "REASON_POLICY_DISABLED",
    "REASON_UNCERTAIN",
    "AmbientInputs",
    "AmbientPolicy",
    "Decision",
    "asleep_needed_s",
    "decide",
    "holdoff_seconds",
    "quiet_hours_state",
    "seconds_until",
    "validate_quiet_hours",
    "within",
]
