"""The wake alarm state machine (M18.3 spec §3.2), mirroring ``app.routines.state``.

    SCHEDULED       -> ARMED, FIRING, SNOOZED, CANCELLED, FAILED
    ARMED           -> FIRING, SCHEDULED, SNOOZED, CANCELLED, FAILED
    FIRING          -> DISPLAY_WAKING, MEDIA_STARTING, PLAYING, SNOOZED, STOPPED,
                       COMPLETED, FAILED
    DISPLAY_WAKING  -> MEDIA_STARTING, PLAYING, SNOOZED, STOPPED, FAILED
    MEDIA_STARTING  -> PLAYING, SNOOZED, STOPPED, FAILED
    PLAYING         -> GREETING, SNOOZED, STOPPED, COMPLETED, FAILED
    GREETING        -> PLAYING, SNOOZED, STOPPED, COMPLETED, FAILED
    SNOOZED         -> ARMED, SCHEDULED, FIRING, CANCELLED, FAILED
    STOPPED / COMPLETED / CANCELLED / FAILED -> (terminal)

Two rules the table encodes and the tests pin:

* **Re-asserting the current state is always legal.** The sequence writes ``PLAYING``
  again after a greeting, and the tick may re-enter a state it is already in; neither is a
  defect, and treating it as one would make the clock's own idempotency a source of errors.
* **A terminal state is terminal.** ``STOPPED`` cannot go back to ``PLAYING``, and a
  ``CANCELLED`` alarm cannot be resurrected by a late tick that still had it in memory.
  This is the second line (after ``RoutineFiring``'s uniqueness and the alarm's own
  ``last_firing_id``) against a stopped alarm starting to ring again.
"""

from __future__ import annotations

from app.alarms.models import (
    ALARM_STATES,
    STATE_ARMED,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_DISPLAY_WAKING,
    STATE_FAILED,
    STATE_FIRING,
    STATE_GREETING,
    STATE_MEDIA_STARTING,
    STATE_PLAYING,
    STATE_SCHEDULED,
    STATE_SNOOZED,
    STATE_STOPPED,
)

#: SCHEDULED/ARMED -> STOPPED covers the alarm whose moment passed while the process was
#: down for longer than ``app.alarms.service.MAX_LATE_FIRE_S``. It never rang, so it is not
#: COMPLETED; the owner did not ask, so it is not CANCELLED. STOPPED with
#: ``terminal_reason="expired_while_down"`` is the truthful third answer, and it releases
#: the device arm like every other terminal state.
_EDGES: dict[str, frozenset[str]] = {
    STATE_SCHEDULED: frozenset(
        {STATE_ARMED, STATE_FIRING, STATE_SNOOZED, STATE_CANCELLED, STATE_STOPPED, STATE_FAILED}
    ),
    STATE_ARMED: frozenset(
        {
            STATE_FIRING,
            STATE_SCHEDULED,
            STATE_SNOOZED,
            STATE_CANCELLED,
            STATE_STOPPED,
            STATE_FAILED,
        }
    ),
    STATE_FIRING: frozenset(
        {
            STATE_DISPLAY_WAKING,
            STATE_MEDIA_STARTING,
            STATE_PLAYING,
            STATE_SNOOZED,
            STATE_STOPPED,
            STATE_COMPLETED,
            STATE_FAILED,
        }
    ),
    STATE_DISPLAY_WAKING: frozenset(
        {STATE_MEDIA_STARTING, STATE_PLAYING, STATE_SNOOZED, STATE_STOPPED, STATE_FAILED}
    ),
    STATE_MEDIA_STARTING: frozenset(
        {STATE_PLAYING, STATE_SNOOZED, STATE_STOPPED, STATE_FAILED}
    ),
    STATE_PLAYING: frozenset(
        {STATE_GREETING, STATE_SNOOZED, STATE_STOPPED, STATE_COMPLETED, STATE_FAILED}
    ),
    STATE_GREETING: frozenset(
        {STATE_PLAYING, STATE_SNOOZED, STATE_STOPPED, STATE_COMPLETED, STATE_FAILED}
    ),
    STATE_SNOOZED: frozenset(
        {STATE_ARMED, STATE_SCHEDULED, STATE_FIRING, STATE_CANCELLED, STATE_FAILED}
    ),
    STATE_STOPPED: frozenset(),
    STATE_COMPLETED: frozenset(),
    STATE_CANCELLED: frozenset(),
    STATE_FAILED: frozenset(),
}

assert set(_EDGES) == set(ALARM_STATES)  # every state is a node in the table


class IllegalAlarmTransition(ValueError):
    """Raised when an alarm state transition is not permitted by the machine."""


def can_transition_alarm(current: str, new: str) -> bool:
    if current not in _EDGES:
        raise ValueError(f"unknown alarm state: {current!r}")
    if new not in _EDGES:
        raise ValueError(f"unknown alarm state: {new!r}")
    if new == current:
        return True  # idempotent re-assert (module docstring)
    return new in _EDGES[current]


def assert_alarm_transition(current: str, new: str) -> str:
    if not can_transition_alarm(current, new):
        raise IllegalAlarmTransition(f"illegal alarm transition {current} -> {new}")
    return new


__all__ = ["IllegalAlarmTransition", "assert_alarm_transition", "can_transition_alarm"]
