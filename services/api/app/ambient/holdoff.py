"""Holdoffs: windows during which nothing may automatically darken the owner's screens
(M18.3 spec §2, §3.9).

Four sources, each starting its own window:

* ``input`` — real keyboard/mouse activity (spec §1.3: physical owner input outranks
  passive inference). Started from the heartbeat's input-idle RESET, and independently
  enforced on the device itself, which refuses ``desktop.display_off`` inside its own
  recent-input window. Neither side depends on the other, on purpose: the cloud can be
  unreachable and the device still protects the owner, and the device can be old and the
  cloud still does.
* ``owner_command`` — the owner just said something about the display or the policy. A
  spoken "ekranı aç" followed 30 seconds later by an automatic off would be the system
  arguing with them.
* ``alarm_wake`` — an alarm just fired. The owner is being woken; the screens stay.
* ``owner_return`` — ``owner.returned``.

In memory, like ``app.devices.status``: a holdoff is a short window about right now, and
losing it on restart fails SAFE — the next decision simply has one fewer reason to say no,
and every other guard (uncertainty, staleness, the device's own refusal) still applies.
Durability would buy nothing a restarted process could act on faster than the next tick.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

SOURCE_INPUT: Final = "input"
SOURCE_OWNER_COMMAND: Final = "owner_command"
SOURCE_ALARM_WAKE: Final = "alarm_wake"
SOURCE_OWNER_RETURN: Final = "owner_return"

HOLDOFF_SOURCES: Final[tuple[str, ...]] = (
    SOURCE_INPUT,
    SOURCE_OWNER_COMMAND,
    SOURCE_ALARM_WAKE,
    SOURCE_OWNER_RETURN,
)

#: Spec §2's defaults, in seconds. The ambient policy row may override each.
DEFAULT_DURATIONS: Final[dict[str, int]] = {
    SOURCE_INPUT: 600,
    SOURCE_OWNER_COMMAND: 900,
    SOURCE_ALARM_WAKE: 1800,
    SOURCE_OWNER_RETURN: 600,
}


@dataclass(frozen=True, slots=True)
class Holdoff:
    source: str
    until: datetime
    reason: str = ""

    def active(self, now: datetime) -> bool:
        return now < self.until


class HoldoffRegistry:
    """The active holdoffs. Thread-safe: started from the heartbeat path (event loop
    thread pool) and read from the routine clock's worker thread."""

    def __init__(self) -> None:
        self._holdoffs: dict[str, Holdoff] = {}
        self._lock = threading.Lock()

    def start(
        self,
        source: str,
        *,
        seconds: int | None = None,
        now: datetime | None = None,
        reason: str = "",
    ) -> Holdoff:
        if source not in HOLDOFF_SOURCES:
            raise ValueError(f"unknown holdoff source: {source!r}")
        moment = now or datetime.now(UTC)
        span = int(seconds if seconds is not None else DEFAULT_DURATIONS[source])
        holdoff = Holdoff(
            source=source, until=moment + timedelta(seconds=max(0, span)), reason=reason
        )
        with self._lock:
            existing = self._holdoffs.get(source)
            # Extend, never shorten: a second reason to keep the screens on inside an
            # existing window must not be able to end that window early.
            if existing is None or holdoff.until > existing.until:
                self._holdoffs[source] = holdoff
            else:
                holdoff = existing
        return holdoff

    def active(self, *, now: datetime | None = None) -> tuple[Holdoff, ...]:
        moment = now or datetime.now(UTC)
        with self._lock:
            expired = [s for s, h in self._holdoffs.items() if not h.active(moment)]
            for source in expired:
                self._holdoffs.pop(source, None)
            return tuple(sorted(self._holdoffs.values(), key=lambda h: h.source))

    def first_active(self, *, now: datetime | None = None) -> Holdoff | None:
        holdoffs = self.active(now=now)
        return holdoffs[0] if holdoffs else None

    def is_active(self, source: str, *, now: datetime | None = None) -> bool:
        return any(h.source == source for h in self.active(now=now))

    def clear(self, source: str | None = None) -> None:
        with self._lock:
            if source is None:
                self._holdoffs.clear()
            else:
                self._holdoffs.pop(source, None)

    def as_dict(self, *, now: datetime | None = None) -> dict[str, str]:
        return {
            h.source: h.until.astimezone(UTC).isoformat().replace("+00:00", "Z")
            for h in self.active(now=now)
        }


_registry = HoldoffRegistry()


def get_holdoffs() -> HoldoffRegistry:
    return _registry


def set_holdoffs(registry: HoldoffRegistry) -> None:
    """Tests and alternative runtimes swap the process-wide registry."""
    global _registry
    _registry = registry


__all__ = [
    "DEFAULT_DURATIONS",
    "HOLDOFF_SOURCES",
    "SOURCE_ALARM_WAKE",
    "SOURCE_INPUT",
    "SOURCE_OWNER_COMMAND",
    "SOURCE_OWNER_RETURN",
    "Holdoff",
    "HoldoffRegistry",
    "get_holdoffs",
    "set_holdoffs",
]
