"""Wake alarms (M18.3 spec §3): a durable aggregate, a routine trigger, a receipted
device sequence.

The package is deliberately free of any dependency on a realtime voice session, a
microphone, the camera or the Presence Engine — ``tests/unit/test_alarms_structure.py``
asserts that structurally. An alarm must ring when the owner is asleep with the camera
off and no browser tab focused; anything it imports could otherwise become a reason it
did not.
"""

from app.alarms.models import (
    ALARM_STATES,
    ALARM_TERMINAL_STATES,
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
    WakeAlarm,
)

__all__ = [
    "ALARM_STATES",
    "ALARM_TERMINAL_STATES",
    "STATE_ARMED",
    "STATE_CANCELLED",
    "STATE_COMPLETED",
    "STATE_DISPLAY_WAKING",
    "STATE_FAILED",
    "STATE_FIRING",
    "STATE_GREETING",
    "STATE_MEDIA_STARTING",
    "STATE_PLAYING",
    "STATE_SCHEDULED",
    "STATE_SNOOZED",
    "STATE_STOPPED",
    "WakeAlarm",
]
