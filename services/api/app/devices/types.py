"""Shared devices-layer view types (avoids service.py <-> selection.py cycles)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.devices.health import DeviceHealth


@dataclass(frozen=True, slots=True)
class DeviceView:
    """The composed, owner-facing view of one enrolled device."""

    id: uuid.UUID
    name: str
    platform: str
    status: str  # broker status: enrolled | revoked
    presence: str  # online | stale | offline
    capabilities: tuple[str, ...]
    enrolled_at: datetime
    last_seen_at: datetime | None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    labels: tuple[str, ...] = field(default_factory=tuple)
    policy: dict[str, Any] = field(default_factory=dict)
    health: DeviceHealth | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": str(self.id),
            "name": self.name,
            "platform": self.platform,
            "status": self.status,
            "presence": self.presence,
            "capabilities": list(self.capabilities),
            "aliases": list(self.aliases),
            "labels": list(self.labels),
            "policy": dict(self.policy),
            "health": self.health.as_dict() if self.health else None,
        }


__all__ = ["DeviceView"]
