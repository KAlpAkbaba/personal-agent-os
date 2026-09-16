"""Device activity status from the heartbeat (M18.3 spec §3.6, §5.3).

The Device Service asks its owner-session companion for
``desktop.activity_status`` before each heartbeat and attaches the answer as an OPTIONAL
``status`` object on the heartbeat frame. This module is the cloud side of that: a
last-known status per device, held in memory, and the DIFFS a caller needs to act on.

Why in memory. This is a ~10 s telemetry stream about a machine's current input idleness
and screen power — the definition of ephemeral. Nothing durable is derived from it directly:
what matters (an input-active moment, a display transition, a locally-fired alarm) becomes a
ledger row, a presence observation or an alarm reconciliation through
``app.ambient.ingest``, which is where the durable writes live. PROJECT_CONSTITUTION.md's
"Redis is never the sole source of truth" rule applied one level down — a process restart
loses the last idle counter and the next heartbeat replaces it, ten seconds later.

Validation is LENIENT by design (task contract): the authoritative schema is Track D's, in
``packages/schemas``, and this side must tolerate both a companion that predates the field
and one that adds a field this Cloud Core has never heard of. Unknown keys are ignored,
malformed values fall back to "unknown", and NOTHING here raises on a status object — a
device that sends a strange heartbeat must stay connected.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from app.devices import voice_contract

DISPLAY_ON: Final = "on"
DISPLAY_OFF: Final = "off"
DISPLAY_DIMMED: Final = "dimmed"
DISPLAY_UNKNOWN: Final = "unknown"
DISPLAY_STATES: Final[tuple[str, ...]] = (
    DISPLAY_ON,
    DISPLAY_OFF,
    DISPLAY_DIMMED,
    DISPLAY_UNKNOWN,
)

#: Spec §3.6a: an idle counter at or below this is CURRENT input activity worth a presence
#: observation. Above it, nothing is published — absence of input is not evidence of
#: absence, and inferring "the owner left" from a quiet keyboard is exactly the mistake
#: "uncertain means ON" exists to prevent.
INPUT_ACTIVE_WITHIN_S: Final = 120.0
#: Spec §3.6a: at most one input-sourced presence observation per device per this window.
INPUT_OBSERVATION_THROTTLE_S: Final = 30.0
#: Spec §3.6b: an idle RESET that follows at least this much idleness is a real "the owner
#: came back to the keyboard" moment, not a pause between keystrokes.
INPUT_RESET_AFTER_IDLE_S: Final = 300.0

#: How many devices' statuses are kept. One owner, a handful of machines.
MAX_DEVICES: Final = 32


def _clamp_float(value: Any, *, default: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    if value < 0:
        return default
    return float(value)


def _display_state(raw: Any) -> str:
    """The display state from either shape the device may send: the spec's nested
    ``display: {state, observed_at}`` or the companion's flat ``display_state`` string
    (DEVICE_PROTOCOL.md §6g as shipped). Anything else is unknown."""
    if isinstance(raw, dict):
        state = raw.get("state")
        return state if state in DISPLAY_STATES else DISPLAY_UNKNOWN
    if isinstance(raw, str):
        return raw if raw in DISPLAY_STATES else DISPLAY_UNKNOWN
    return DISPLAY_UNKNOWN


#: B48 (DEVICE_PROTOCOL.md §6p): the device camera's state, as the companion reports it.
CAMERA_MODES: Final[tuple[str, ...]] = ("off", "periodic", "continuous")
CAMERA_STATES: Final[tuple[str, ...]] = (
    "off",
    "idle",
    "capturing",
    "blocked",
    "unavailable",
    "busy",
    "vetoed",
    "error",
)
CAMERA_INDICATORS: Final[tuple[str, ...]] = ("hidden", "armed", "open")
#: How far ahead of this server's clock a device's camera reading may be stamped.
CAMERA_FUTURE_SKEW_S: Final = 300.0
_TOKEN_MAX = 64


def _token(raw: Any, allowed: tuple[str, ...] | None = None) -> str | None:
    if not isinstance(raw, str) or not raw or len(raw) > _TOKEN_MAX:
        return None
    if allowed is not None and raw not in allowed:
        return None
    return raw


def _camera_block(raw: Any) -> dict[str, Any] | None:
    """The companion's ``camera`` object, normalised, or None when it sent none (a device
    with no camera path) or sent something that is not one. An unknown mode or state is
    None inside the block - "this device did not say anything I understand" - never a
    guess."""
    if not isinstance(raw, dict):
        return None
    interval = raw.get("interval_s")
    return {
        "mode": _token(raw.get("mode"), CAMERA_MODES),
        "state": _token(raw.get("state"), CAMERA_STATES),
        "interval_s": interval
        if isinstance(interval, int) and not isinstance(interval, bool) and 0 <= interval <= 3600
        else None,
        "indicator": _token(raw.get("indicator"), CAMERA_INDICATORS),
        "last_check_at": _iso(_parse_dt(raw.get("last_check_at"))),
        "error": _token(raw.get("error")),
    }


def _presence_block(raw: Any) -> dict[str, Any] | None:
    """The companion's derived ``presence`` observation, kept only as a flat object of
    scalars. It is NOT trusted here: ``app.ambient.ingest`` passes it through the presence
    boundary (``app.presence.observations.parse_observation``), which refuses anything that
    is not exactly the seven fields."""
    if not isinstance(raw, dict) or not raw:
        return None
    if any(isinstance(v, dict | list | tuple) for v in raw.values()):
        return None
    return dict(raw)


def _screened_observation(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """The device's observation as the presence boundary accepts it, or None: nothing a
    device sent is repeated to a reader until that boundary has said it is the seven fields."""
    if raw is None:
        return None
    from app.presence.observations import ObservationRejected, parse_observation

    try:
        return parse_observation(raw).as_dict()
    except ObservationRejected:
        return None


def _armed_count(raw: Any) -> int:
    """``armed_alarms`` as the companion reports it: a COUNT (§6g), or an id list in the
    spec's original shape. Never negative, never a bool read as one."""
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return max(0, raw)
    if isinstance(raw, list | tuple):
        return len(_id_list(raw))
    return 0


def _snooze_list(raw: Any) -> tuple[tuple[str, datetime], ...]:
    """B47 ``local_alarm_snoozed``: ``{"alarm_id", "until"}`` objects, bounded; anything
    malformed is dropped rather than guessed at."""
    if not isinstance(raw, list | tuple):
        return ()
    out: list[tuple[str, datetime]] = []
    limit = voice_contract.local_snooze_max_entries()
    for item in raw:
        if len(out) >= limit:
            break
        if not isinstance(item, dict):
            continue
        alarm_id = item.get("alarm_id")
        until = _parse_dt(item.get("until"))
        if isinstance(alarm_id, str) and alarm_id.strip() and until is not None:
            out.append((alarm_id.strip()[:64], until))
    return tuple(out)


def _id_list(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list | tuple):
        return ()
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:64])
        if len(out) >= 32:
            break
    return tuple(out)


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    """One companion activity report, normalised (spec §5.3).

    Every field has a defined value for a status object that omitted it, so a caller never
    has to distinguish "absent" from "malformed" — both mean "this device is not telling me,
    so I know nothing", which is the only safe reading for a policy that can darken screens.
    """

    device_id: uuid.UUID
    observed_at: datetime
    input_idle_s: float | None = None
    display_state: str = DISPLAY_UNKNOWN
    display_observed_at: datetime | None = None
    alarm_ringing: bool = False
    ringing_alarm_id: str | None = None
    armed_alarms: tuple[str, ...] = ()
    #: How many alarms the companion holds armed (the count it reports, or the list's length).
    armed_alarm_count: int = 0
    local_alarm_fired: tuple[str, ...] = ()
    holdoff_until: datetime | None = None
    next_alarm_at: datetime | None = None
    monitors: int | None = None
    #: B47: alarms the device snoozed on its own, with the instant it will ring again.
    local_alarm_snoozed: tuple[tuple[str, datetime], ...] = ()
    #: B47 rows 250-252: the device voice service's health, on the contract's key set.
    voice: dict[str, Any] | None = None
    raw_keys: tuple[str, ...] = field(default_factory=tuple)
    #: B48: the device camera's state (``_camera_block``), None for a device without one.
    camera: dict[str, Any] | None = None
    #: B48: the device's latest derived camera observation, unscreened (see ``_presence_block``).
    presence: dict[str, Any] | None = None

    @property
    def input_active(self) -> bool:
        """The owner touched something within the last :data:`INPUT_ACTIVE_WITHIN_S`."""
        return self.input_idle_s is not None and self.input_idle_s <= INPUT_ACTIVE_WITHIN_S

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": str(self.device_id),
            "observed_at": _iso(self.observed_at),
            "input_idle_s": self.input_idle_s,
            "input_active": self.input_active,
            "display": {
                "state": self.display_state,
                "observed_at": _iso(self.display_observed_at),
            },
            # The flat spellings beside the nested block, so a reader written against either
            # shape (the web cockpit, the owner harnesses, the companion's own document) finds
            # the display state where it looks for it.
            "display_state": self.display_state,
            "display_observed_at": _iso(self.display_observed_at),
            "alarm_ringing": self.alarm_ringing,
            "ringing_alarm_id": self.ringing_alarm_id,
            "armed_alarms": list(self.armed_alarms),
            "armed_alarm_count": self.armed_alarm_count,
            "local_alarm_fired": list(self.local_alarm_fired),
            "local_alarm_snoozed": [
                {"alarm_id": alarm_id, "until": _iso(until)}
                for alarm_id, until in self.local_alarm_snoozed
            ],
            "voice": dict(self.voice) if self.voice is not None else None,
            "holdoff_until": _iso(self.holdoff_until),
            "next_alarm_at": _iso(self.next_alarm_at),
            "monitors": self.monitors,
            # B48: what the device camera is doing - mode, state, indicator - so the owner's
            # panel can say "blocked by Windows" rather than go quiet, and the latest DERIVED
            # observation it produced (the seven fields; never a frame), as the device sent it.
            "camera": dict(self.camera) if self.camera is not None else None,
            "camera_observation": _screened_observation(self.presence),
        }


@dataclass(frozen=True, slots=True)
class StatusChange:
    """What is NEW about this report — the only thing a caller should act on.

    A heartbeat arrives every ~10 s and almost always says the same thing; acting on the
    status rather than on the change would write a ledger row, a presence observation and a
    bus event six times a minute forever.
    """

    status: DeviceStatus
    previous: DeviceStatus | None
    display_changed: bool
    input_reset: bool
    should_observe_input: bool
    newly_fired_alarms: tuple[str, ...]
    #: B47: local snoozes this report carries that the previous one did not.
    newly_snoozed_alarms: tuple[tuple[str, datetime], ...] = ()
    #: B48: a camera observation this registry has not handed out before (by ``observed_at``,
    #: per device), or None. A heartbeat repeats the latest observation until a newer one
    #: exists; only the first sighting is evidence.
    new_camera_observation: dict[str, Any] | None = None

    @property
    def display_state(self) -> str:
        return self.status.display_state


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_dt(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def parse_status(
    device_id: uuid.UUID, raw: Any, *, now: datetime | None = None
) -> DeviceStatus | None:
    """Normalise a heartbeat ``status`` object. ``None`` when there is nothing usable.

    Never raises (module docstring): an unparseable status is the same as no status.
    """
    if not isinstance(raw, dict) or not raw:
        return None
    moment = now or datetime.now(UTC)
    # Either shape: the spec's nested `display` block, or the companion's flat
    # `display_state` / `display_observed_at` (DEVICE_PROTOCOL.md §6g as shipped).
    display = raw.get("display")
    if display is None:
        display = raw.get("display_state")
    display_observed = (
        display.get("observed_at") if isinstance(display, dict) else raw.get("display_observed_at")
    )
    return DeviceStatus(
        device_id=device_id,
        observed_at=_parse_dt(raw.get("observed_at")) or moment,
        input_idle_s=_clamp_float(raw.get("input_idle_s")),
        display_state=_display_state(display),
        display_observed_at=_parse_dt(display_observed),
        alarm_ringing=bool(raw.get("alarm_ringing")),
        ringing_alarm_id=(
            str(raw["ringing_alarm_id"])[:64]
            if isinstance(raw.get("ringing_alarm_id"), str)
            else None
        ),
        armed_alarms=_id_list(raw.get("armed_alarms")),
        armed_alarm_count=_armed_count(raw.get("armed_alarms")),
        local_alarm_fired=_id_list(raw.get("local_alarm_fired")),
        local_alarm_snoozed=_snooze_list(raw.get(voice_contract.local_snooze_field())),
        voice=voice_contract.normalise_voice(raw.get(voice_contract.heartbeat_field())),
        holdoff_until=_parse_dt(raw.get("holdoff_until")),
        next_alarm_at=_parse_dt(raw.get("next_alarm_at")),
        monitors=(
            int(raw["monitors"])
            if isinstance(raw.get("monitors"), int) and not isinstance(raw.get("monitors"), bool)
            else None
        ),
        raw_keys=tuple(sorted(str(k)[:32] for k in raw)),
        camera=_camera_block(raw.get("camera")),
        presence=_presence_block(raw.get("presence")),
    )


class DeviceStatusRegistry:
    """Last-known status per device, plus what changed (spec §3.6)."""

    def __init__(self, *, max_devices: int = MAX_DEVICES) -> None:
        self._max_devices = max_devices
        self._by_device: dict[uuid.UUID, DeviceStatus] = {}
        self._last_input_observation: dict[uuid.UUID, datetime] = {}
        #: B48: device -> the ``observed_at`` of the newest camera observation handed out.
        self._last_camera_observed_at: dict[uuid.UUID, datetime] = {}
        #: device -> the broker origin it dialled (insertion order = recency).
        self._dial_origins: dict[uuid.UUID, str] = {}
        self._lock = threading.Lock()

    def record(
        self, device_id: uuid.UUID, raw: Any, *, now: datetime | None = None
    ) -> StatusChange | None:
        """Store a report and return what is new about it, or ``None`` for no usable status."""
        status = parse_status(device_id, raw, now=now)
        if status is None:
            return None
        moment = now or datetime.now(UTC)
        with self._lock:
            previous = self._by_device.get(device_id)
            if len(self._by_device) >= self._max_devices and device_id not in self._by_device:
                self._by_device.pop(next(iter(self._by_device)), None)
            self._by_device[device_id] = status

            display_changed = (
                previous is None or previous.display_state != status.display_state
            ) and status.display_state != DISPLAY_UNKNOWN

            # Spec §3.6b: a reset after a long idle, OR any input while the display is off
            # (the owner pressing a key on a dark screen is exactly the wake we must never
            # fight, and its idle counter may be well under 300 s by the time we see it).
            input_reset = False
            if status.input_active:
                if previous is not None and previous.input_idle_s is not None:
                    was_long_idle = previous.input_idle_s >= INPUT_RESET_AFTER_IDLE_S
                    dropped = status.input_idle_s is not None and (
                        status.input_idle_s < previous.input_idle_s
                    )
                    display_was_off = previous.display_state == DISPLAY_OFF
                    input_reset = (was_long_idle and dropped) or (display_was_off and dropped)
                elif previous is None:
                    # First report from a device whose screen is off and whose keyboard was
                    # just touched: the owner is there. A first report on a lit screen is
                    # not a reset — nothing changed, we simply had not been watching.
                    input_reset = status.display_state == DISPLAY_OFF

            should_observe = False
            if status.input_active:
                last = self._last_input_observation.get(device_id)
                if last is None or (moment - last).total_seconds() >= INPUT_OBSERVATION_THROTTLE_S:
                    should_observe = True
                    self._last_input_observation[device_id] = moment

            seen = set(previous.local_alarm_fired) if previous else set()
            newly_fired = tuple(a for a in status.local_alarm_fired if a not in seen)
            # The device drains its list, so a snooze normally appears once; the diff keeps a
            # retransmitted report from snoozing the same alarm twice.
            seen_snoozes = set(previous.local_alarm_snoozed) if previous else set()
            newly_snoozed = tuple(e for e in status.local_alarm_snoozed if e not in seen_snoozes)

            new_camera: dict[str, Any] | None = None
            if status.presence is not None:
                observed = _parse_dt(status.presence.get("observed_at"))
                last = self._last_camera_observed_at.get(device_id)
                # A reading stamped far in the future is a device clock problem, not evidence -
                # and recorded as "newest" it would silence every honest reading after it.
                plausible = observed is not None and (
                    (observed - moment).total_seconds() <= CAMERA_FUTURE_SKEW_S
                )
                if plausible and observed is not None and (last is None or observed > last):
                    self._last_camera_observed_at[device_id] = observed
                    new_camera = dict(status.presence)

        return StatusChange(
            status=status,
            previous=previous,
            display_changed=display_changed,
            input_reset=input_reset,
            should_observe_input=should_observe,
            newly_fired_alarms=newly_fired,
            newly_snoozed_alarms=newly_snoozed,
            new_camera_observation=new_camera,
        )

    def get(self, device_id: uuid.UUID) -> DeviceStatus | None:
        with self._lock:
            return self._by_device.get(device_id)

    def all(self) -> dict[uuid.UUID, DeviceStatus]:
        with self._lock:
            return dict(self._by_device)

    def any_alarm_ringing(self) -> bool:
        with self._lock:
            return any(s.alarm_ringing for s in self._by_device.values())

    def displays_on(self) -> bool | None:
        """True when at least one device reports its display ON, False when every device
        that reports at all says OFF, ``None`` when nobody knows. ``None`` is the answer the
        ambient policy refuses to act on — uncertain means ON stays on (spec §1.4)."""
        with self._lock:
            states = [
                s.display_state
                for s in self._by_device.values()
                if s.display_state != DISPLAY_UNKNOWN
            ]
        if not states:
            return None
        return any(state in (DISPLAY_ON, DISPLAY_DIMMED) for state in states)

    def record_dial_origin(self, device_id: uuid.UUID, origin: str) -> None:
        """The origin THIS device dialled the broker at (scheme + host[:port] of its
        WebSocket handshake). The companion fetches greeting audio only from the origin its
        Device Service is configured with (DEVICE_PROTOCOL.md §6h), so an absolute audio URL
        must be built from what the device actually used - never from what the cloud
        believes its own name to be."""
        origin = (origin or "").strip().rstrip("/")
        if not origin:
            return
        with self._lock:
            # Re-insert so dict order is recency (a reconnect moves the device to the end).
            self._dial_origins.pop(device_id, None)
            self._dial_origins[device_id] = origin

    def dial_origin(self, device_id: uuid.UUID) -> str | None:
        with self._lock:
            return self._dial_origins.get(device_id)

    def latest_dial_origin(self) -> str | None:
        """The most recently recorded dial origin across devices (a single-owner system
        normally has one), or ``None`` when no device has connected since startup."""
        with self._lock:
            if not self._dial_origins:
                return None
            return next(reversed(self._dial_origins.values()))

    def clear(self) -> None:
        with self._lock:
            self._by_device.clear()
            self._last_input_observation.clear()
            self._last_camera_observed_at.clear()


#: Process-wide registry, the same shape as ``app.devices.commands``'s broker registry.
_registry = DeviceStatusRegistry()


def lowest_idle_seconds(registry: DeviceStatusRegistry | None = None) -> float | None:
    """B14 req 294/299: how long since the OWNER last touched any of their machines.

    The LOWEST across the devices that reported one, because "the owner is idle" is a claim
    about the owner and not about a machine: a laptop shut since Friday says nothing about
    somebody who is at their desktop right now, and taking the highest would call them idle
    while they typed.

    ``None`` when nothing reported - which a condition trigger reads as "not met", never as
    "the owner has gone" (``app.routines.triggers.check_condition_due``).
    """
    reg = registry or get_status_registry()
    try:
        idles = [
            status.input_idle_s
            for status in reg.all().values()
            if status.input_idle_s is not None
        ]
    except Exception:  # noqa: BLE001 - a missing registry is "unknown", never a fault
        return None
    return min(idles) if idles else None


def get_status_registry() -> DeviceStatusRegistry:
    return _registry


def set_status_registry(registry: DeviceStatusRegistry) -> None:
    """Tests and alternative runtimes swap the process-wide registry."""
    global _registry
    _registry = registry


__all__ = [
    "CAMERA_FUTURE_SKEW_S",
    "CAMERA_INDICATORS",
    "CAMERA_MODES",
    "CAMERA_STATES",
    "DISPLAY_DIMMED",
    "DISPLAY_OFF",
    "DISPLAY_ON",
    "DISPLAY_STATES",
    "DISPLAY_UNKNOWN",
    "INPUT_ACTIVE_WITHIN_S",
    "INPUT_OBSERVATION_THROTTLE_S",
    "INPUT_RESET_AFTER_IDLE_S",
    "MAX_DEVICES",
    "DeviceStatus",
    "DeviceStatusRegistry",
    "StatusChange",
    "get_status_registry",
    "lowest_idle_seconds",
    "parse_status",
    "set_status_registry",
]
