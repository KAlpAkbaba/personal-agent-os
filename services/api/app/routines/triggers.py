"""Trigger vocabulary, validation and "is this due?" evaluation (M18 task brief §Triggers).

Three trigger kinds, one rule that binds them: a trigger is data, evaluated explicitly by
``app.routines.service.evaluate_due`` at some caller-chosen ``now`` — nothing here schedules
a wakeup or runs on a clock.

- ``at``: one absolute UTC instant. Due once ``now`` reaches it; the single occurrence key
  is the constant ``"once"``.
- ``schedule``: a weekday set + a local wall-clock time + an explicit IANA timezone. The
  owner is in Europe/Istanbul, and the task brief is explicit that DST must never silently
  shift an alarm — so "due" is always computed by converting ``now`` into the trigger's OWN
  timezone via :mod:`zoneinfo` and comparing wall-clock fields, never by pre-computing a
  fixed UTC offset once and reusing it. A timezone that observes DST (Istanbul currently
  does not; many owner-relevant integrations still will) shifts its UTC offset across the
  year, and a fixed-offset implementation would fire an hour early or late right after the
  transition without ever raising an error — see ``tests/unit/test_routines_triggers.py``'s
  DST-boundary cases, which fail under a fixed-offset implementation and pass under this
  one. The occurrence key is the local calendar date, so a routine fires at most once per
  local day regardless of how many times ``evaluate_due`` is called inside the grace window.
- ``presence``: fires from an event another track (``app/presence``, being written in
  parallel) publishes through ``app.uistate``. This module depends ONLY on the event NAME
  strings (``PRESENCE_TRIGGER_EVENTS`` below) and on ``app.uistate.publisher``'s public tail
  API — never on any ``app.presence`` internals, which do not exist in this checkout yet.
  That is the narrow seam the task brief asks for: whatever shape the presence track ends
  up publishing, as long as it publishes an event whose ``state`` string-compares equal to
  one of these three names, a presence-triggered routine will see it. The occurrence key is
  the source event's own ``event_id`` (never a timestamp), so the same underlying presence
  event can never double-fire a routine even across repeated ``evaluate_due`` calls; the
  caller additionally advances a per-routine watermark (``Routine.last_presence_sequence``)
  so old events are never re-scanned at all.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Names another track publishes through app.uistate (module docstring). Not enum members of
#: app.uistate.contract.UiState — deliberately: adding them there would make this package
#: depend on (and possibly collide with) a vocabulary owned by the presence track.
PRESENCE_EVENT_OWNER_RETURNED = "owner.returned"
PRESENCE_EVENT_OWNER_AWAKE = "owner.awake"
PRESENCE_EVENT_OWNER_LIKELY_ASLEEP = "owner.likely_asleep"
#: The spec's trigger list ends with "presence changes", and these two are the ones an
#: owner reaches for first - "when I leave, pause the music", "when I am here again, tell
#: me what happened". They were left out while the presence track was still being written;
#: app/presence publishes both, and a trigger vocabulary narrower than the published
#: vocabulary is a gap, not a safety property.
PRESENCE_EVENT_OWNER_PRESENT = "owner.present"
PRESENCE_EVENT_OWNER_AWAY = "owner.away"

PRESENCE_TRIGGER_EVENTS: tuple[str, ...] = (
    PRESENCE_EVENT_OWNER_RETURNED,
    PRESENCE_EVENT_OWNER_AWAKE,
    PRESENCE_EVENT_OWNER_LIKELY_ASLEEP,
    PRESENCE_EVENT_OWNER_PRESENT,
    PRESENCE_EVENT_OWNER_AWAY,
)

#: A once-only occurrence key for an "at" trigger — there is exactly one moment to resolve.
ONCE_OCCURRENCE_KEY = "once"

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

#: Default catch-up window for a schedule trigger: how long after the scheduled wall-clock
#: moment an explicit evaluate_due call still counts as "on time" (task brief has no fixed
#: polling cadence — evaluation is an explicit call, so some tolerance is required or a
#: caller running every few minutes could miss every alarm).
DEFAULT_GRACE_MINUTES = 5
MAX_GRACE_MINUTES = 24 * 60


class InvalidTrigger(ValueError):
    """A trigger descriptor is malformed or outside the closed vocabulary."""


class UiStateEventLike(Protocol):
    """The three fields presence matching needs from a published event — structurally
    satisfied by ``app.uistate.contract.UiStateEvent`` without importing it, so tests may
    pass a plain namespace instead of constructing the real publisher."""

    state: Any
    sequence: int
    event_id: str


# ------------------------------------------------------------------ validation


def _parse_iso(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise InvalidTrigger("'at' must be a non-empty ISO-8601 datetime string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidTrigger(f"'at' is not a valid ISO-8601 datetime: {value!r}") from exc
    if parsed.tzinfo is None:
        raise InvalidTrigger(
            f"'at' must carry an explicit UTC offset, got a naive datetime: {value!r}"
        )
    return parsed


def validate_at_trigger(raw: dict[str, Any]) -> dict[str, Any]:
    at = _parse_iso(raw.get("at"))
    return {"at": at.isoformat()}


def validate_schedule_trigger(raw: dict[str, Any]) -> dict[str, Any]:
    weekdays_raw = raw.get("weekdays")
    if not isinstance(weekdays_raw, list) or not weekdays_raw:
        raise InvalidTrigger("'weekdays' must be a non-empty list of 0 (Mon) .. 6 (Sun)")
    weekdays: set[int] = set()
    for value in weekdays_raw:
        if not isinstance(value, int) or isinstance(value, bool) or not (0 <= value <= 6):
            raise InvalidTrigger(f"'weekdays' entries must be ints 0..6, got {value!r}")
        weekdays.add(value)

    time_str = raw.get("time")
    if not isinstance(time_str, str) or not _TIME_RE.match(time_str):
        raise InvalidTrigger(f"'time' must be 'HH:MM' 24h, got {time_str!r}")

    tz_name = raw.get("timezone")
    if not isinstance(tz_name, str) or not tz_name:
        raise InvalidTrigger("'timezone' must be an explicit IANA zone name, e.g. Europe/Istanbul")
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise InvalidTrigger(f"unknown IANA timezone: {tz_name!r}") from exc

    grace_minutes = raw.get("grace_minutes", DEFAULT_GRACE_MINUTES)
    if (
        not isinstance(grace_minutes, int)
        or isinstance(grace_minutes, bool)
        or not (0 < grace_minutes <= MAX_GRACE_MINUTES)
    ):
        raise InvalidTrigger(f"'grace_minutes' must be an int in 1..{MAX_GRACE_MINUTES}")

    return {
        "weekdays": sorted(weekdays),
        "time": time_str,
        "timezone": tz_name,
        "grace_minutes": grace_minutes,
    }


def validate_presence_trigger(raw: dict[str, Any]) -> dict[str, Any]:
    event = raw.get("event")
    if event not in PRESENCE_TRIGGER_EVENTS:
        raise InvalidTrigger(
            f"unknown presence trigger event: {event!r}; must be one of {PRESENCE_TRIGGER_EVENTS}"
        )
    return {"event": event}


def validate_trigger(trigger_kind: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    if trigger_kind == "at":
        return validate_at_trigger(raw)
    if trigger_kind == "schedule":
        return validate_schedule_trigger(raw)
    if trigger_kind == "presence":
        return validate_presence_trigger(raw)
    raise InvalidTrigger(f"unknown trigger_kind: {trigger_kind!r}")


# --------------------------------------------------------------------- due checks


def check_at_due(trigger_json: dict[str, Any], now: datetime) -> tuple[bool, str | None]:
    """Due once, the instant ``now`` reaches the target — never before, forever after
    (idempotency against re-firing is the caller's job, via RoutineFiring)."""
    at = _parse_iso(trigger_json.get("at"))
    if now >= at:
        return True, ONCE_OCCURRENCE_KEY
    return False, None


def check_schedule_due(trigger_json: dict[str, Any], now: datetime) -> tuple[bool, str | None]:
    """Converts ``now`` into the trigger's own timezone and compares wall-clock fields —
    see the module docstring for why this, and not a cached UTC offset, is DST-safe."""
    tz = ZoneInfo(trigger_json["timezone"])
    local_now = now.astimezone(tz)
    if local_now.weekday() not in trigger_json["weekdays"]:
        return False, None
    hour, minute = (int(part) for part in trigger_json["time"].split(":"))
    scheduled = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    grace = timedelta(minutes=trigger_json.get("grace_minutes", DEFAULT_GRACE_MINUTES))
    if scheduled <= local_now < scheduled + grace:
        return True, local_now.date().isoformat()
    return False, None


def check_presence_due(
    trigger_json: dict[str, Any],
    tail_events: list[UiStateEventLike],
    *,
    after_sequence: int,
) -> tuple[bool, str | None, int]:
    """Scans ``tail_events`` (assumed already filtered to ``sequence > after_sequence``, in
    ascending sequence order) for the first event matching the trigger's target name.

    Returns ``(due, occurrence_key, new_watermark)``. The watermark advances past every
    scanned event regardless of a match, so a routine that is not due this call never
    re-examines the same events on the next one; a match beyond the first in the batch is
    intentionally left for the NEXT evaluate_due call rather than fired twice in one pass —
    a burst of presence events collapses to a single firing, which is the safer default for
    something that may ring an alarm or speak to the owner.
    """
    target = trigger_json.get("event")
    new_watermark = after_sequence
    matched_key: str | None = None
    for event in tail_events:
        sequence = getattr(event, "sequence", after_sequence)
        if sequence > new_watermark:
            new_watermark = sequence
        if matched_key is not None:
            continue
        state_value = getattr(event.state, "value", event.state)
        if state_value == target:
            matched_key = str(getattr(event, "event_id", sequence))
    if matched_key is not None:
        return True, matched_key, new_watermark
    return False, None, new_watermark


__all__ = [
    "DEFAULT_GRACE_MINUTES",
    "MAX_GRACE_MINUTES",
    "ONCE_OCCURRENCE_KEY",
    "PRESENCE_EVENT_OWNER_AWAKE",
    "PRESENCE_EVENT_OWNER_LIKELY_ASLEEP",
    "PRESENCE_EVENT_OWNER_AWAY",
    "PRESENCE_EVENT_OWNER_PRESENT",
    "PRESENCE_EVENT_OWNER_RETURNED",
    "PRESENCE_TRIGGER_EVENTS",
    "InvalidTrigger",
    "check_at_due",
    "check_presence_due",
    "check_schedule_due",
    "validate_at_trigger",
    "validate_presence_trigger",
    "validate_schedule_trigger",
    "validate_trigger",
]
