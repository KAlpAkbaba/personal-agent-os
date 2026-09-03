"""DeviceHealth: last hello, software_version, heartbeat age, recent outcomes.

Composed read-only view over ``app.broker`` rows — no new persistence. "Last
hello" is ``devices.last_seen_at`` (touched on every handshake and every
frame, DEVICE_PROTOCOL.md §4); ``software_version``/``capabilities_json`` are
refreshed from every ``hello`` (``app.broker.service.apply_hello``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.broker.models import Device, DeviceCommand
from app.devices.presence import heartbeat_age_s


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    command_id: str
    capability: str
    status: str
    error_class: str | None
    terminal_at: str | None


@dataclass(frozen=True, slots=True)
class DeviceHealth:
    last_hello_at: str | None
    software_version: str | None
    heartbeat_age_s: float | None
    recent_outcomes: tuple[CommandOutcome, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "last_hello_at": self.last_hello_at,
            "software_version": self.software_version,
            "heartbeat_age_s": self.heartbeat_age_s,
            "recent_outcomes": [
                {
                    "command_id": o.command_id,
                    "capability": o.capability,
                    "status": o.status,
                    "error_class": o.error_class,
                    "terminal_at": o.terminal_at,
                }
                for o in self.recent_outcomes
            ],
        }

    @property
    def recent_failure_count(self) -> int:
        return sum(1 for o in self.recent_outcomes if o.status != "succeeded")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _iso(dt: datetime | None) -> str | None:
    dt = _aware(dt)
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def build_health(
    device: Device, outcomes: list[DeviceCommand], *, now: datetime | None = None
) -> DeviceHealth:
    now = now or datetime.now(UTC)
    return DeviceHealth(
        last_hello_at=_iso(device.last_seen_at),
        software_version=device.software_version,
        heartbeat_age_s=heartbeat_age_s(_aware(device.last_seen_at), now),
        recent_outcomes=tuple(
            CommandOutcome(
                command_id=str(c.id),
                capability=c.capability,
                status=c.status,
                error_class=c.error_class,
                terminal_at=_iso(c.terminal_at),
            )
            for c in outcomes
        ),
    )


__all__ = ["CommandOutcome", "DeviceHealth", "build_health"]
