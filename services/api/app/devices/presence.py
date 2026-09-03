"""DevicePresence: online / stale / offline (DEVICE_PROTOCOL.md §4, §8).

Pure function of (live WS connection, last heartbeat age, a configurable
stale threshold) — never a machine literal, never guessed from anything but
the broker's own connection table and ``devices.last_seen_at``.

- ``online``: the broker holds a live WebSocket connection for the device
  right now (``BrokerRuntime.is_online``).
- ``stale``: not currently connected, but ``last_seen_at`` is within
  ``stale_after_s`` — a brief reconnect gap (network blip, agent restart)
  rather than a device that has actually gone away. Selection (§8) treats
  ``stale`` the same as ``offline`` (only ``online`` devices are selectable);
  the distinction exists for the owner-facing inventory view.
- ``offline``: no connection and either no heartbeat ever recorded, or the
  last one is older than ``stale_after_s``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

PRESENCE_ONLINE = "online"
PRESENCE_STALE = "stale"
PRESENCE_OFFLINE = "offline"
PRESENCE_STATES = (PRESENCE_ONLINE, PRESENCE_STALE, PRESENCE_OFFLINE)

#: Default gap after the last heartbeat before a disconnected device reads as
#: fully offline rather than merely stale (app.config.Settings can override).
DEFAULT_STALE_AFTER_S = 30.0


def compute_presence(
    *,
    is_connected: bool,
    last_seen_at: datetime | None,
    now: datetime,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
) -> str:
    if is_connected:
        return PRESENCE_ONLINE
    if last_seen_at is None:
        return PRESENCE_OFFLINE
    age = now - last_seen_at
    if age <= timedelta(seconds=max(0.0, stale_after_s)):
        return PRESENCE_STALE
    return PRESENCE_OFFLINE


def heartbeat_age_s(last_seen_at: datetime | None, now: datetime) -> float | None:
    if last_seen_at is None:
        return None
    return max(0.0, (now - last_seen_at).total_seconds())


__all__ = [
    "DEFAULT_STALE_AFTER_S",
    "PRESENCE_OFFLINE",
    "PRESENCE_ONLINE",
    "PRESENCE_STALE",
    "PRESENCE_STATES",
    "compute_presence",
    "heartbeat_age_s",
]
