"""Shared devices-layer view types (avoids service.py <-> selection.py cycles)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.devices.health import DeviceHealth

#: The CANONICAL device-identity keys on a ``GET /v1/devices`` row.
#:
#: 2026-09-08 incident: the Windows installer's staged-update verifier
#: (``scripts/lib/AgentUpdate.ps1``, ``Test-AgentHeartbeatOnCore``) read
#: ``row["software_version"]`` to decide whether Cloud Core could SEE the
#: candidate. No such key had ever existed on the row — the version was
#: reachable only at ``row["health"]["software_version"]`` — so the verifier
#: read ``""`` for a candidate that was in fact live and correct, failed after
#: 92.6 s, and the deployment engine rolled a good 0.6.0 back. The candidate
#: was never the problem; the row shape was.
#:
#: Consequence: the identity a candidate must expose lives HERE, at the top
#: level of the row, in one place both halves name. ``health.software_version``
#: stays (the Cockpit reads it) and carries the same value — this list is what
#: an installer, a qualification run or a release gate is entitled to read.
#: ``tests/unit/test_device_identity_contract.py`` asserts the PowerShell
#: verifier reads exactly these names, so neither half can drift alone.
DEVICE_IDENTITY_KEYS: tuple[str, ...] = (
    "device_id",
    "presence",
    "software_version",
    "build_id",
    "capabilities",
    "capability_count",
    "last_seen_at",
)


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
    #: ``hello.software_version`` as last reported by the connected agent
    #: (``app.broker.service.apply_hello``). ``None`` when no agent has ever
    #: said hello — which is a truthful "unknown", never an empty string.
    software_version: str | None = None
    #: ``hello.build_id`` as last reported (ADR-0118). ``software_version`` is the PRODUCT
    #: release and is meant to stay still across builds, so it cannot prove a staged update
    #: took; this is what the verifier compares. ``None`` when the agent announced none —
    #: an agent older than ADR-0118, and never a match.
    build_id: str | None = None
    #: The commit the SDK stamped into the binary. Provenance for a human, never compared.
    source_revision: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": str(self.id),
            "name": self.name,
            "platform": self.platform,
            "status": self.status,
            "presence": self.presence,
            # The canonical identity keys (DEVICE_IDENTITY_KEYS). A device that
            # has never said hello reports None, not "" — "I do not know" and
            # "it announced an empty version" are different failures and the
            # installer must be able to tell them apart.
            "software_version": self.software_version,
            "build_id": self.build_id,
            "source_revision": self.source_revision,
            "capabilities": list(self.capabilities),
            "capability_count": len(self.capabilities),
            "last_seen_at": self.last_seen_at.isoformat().replace("+00:00", "Z")
            if self.last_seen_at
            else None,
            "aliases": list(self.aliases),
            "labels": list(self.labels),
            "policy": dict(self.policy),
            "health": self.health.as_dict() if self.health else None,
        }


__all__ = ["DEVICE_IDENTITY_KEYS", "DeviceView"]
