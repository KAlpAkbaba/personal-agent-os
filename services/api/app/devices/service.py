"""DeviceInventory: compose broker rows + live connection state into DeviceView.

The single place that turns ``app.broker`` persistence (Device,
DeviceCommand) plus ``BrokerRuntime`` live connection state into the
owner-facing ``DeviceView`` (presence/capabilities/health/aliases/labels/
policy) the REST surface and ``app.devices.selection`` both consume.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.broker import service as broker_service
from app.broker.models import Device
from app.broker.runtime import BrokerRuntime
from app.devices import aliases as alias_module
from app.devices.health import build_health
from app.devices.presence import DEFAULT_STALE_AFTER_S, compute_presence
from app.devices.types import DeviceView

RECENT_OUTCOME_LIMIT = 5


class AliasConflictError(Exception):
    """An owner PATCH tried to set an alias already used by another
    enrolled device (finding LOW-9). ``alias`` is the raw (un-normalized)
    alias the caller submitted, for a readable Turkish error message."""

    def __init__(self, alias: str) -> None:
        super().__init__(f"alias {alias!r} is already used by another enrolled device")
        self.alias = alias


def find_alias_conflict(
    session: Session, *, device_id: uuid.UUID, aliases: list[str]
) -> str | None:
    """The first alias in ``aliases`` already configured on a DIFFERENT,
    still-enrolled (not revoked) device, or ``None``. Comparison is
    normalized (``app.devices.aliases.normalize`` — casefolded, stripped)
    so ``"Ev"`` and ``"ev "`` are recognized as the same alias."""
    wanted = {alias_module.normalize(a): a for a in aliases if a.strip()}
    if not wanted:
        return None
    for device in broker_service.list_devices(session):
        if device.id == device_id or device.revoked_at is not None:
            continue
        existing = {
            alias_module.normalize(a) for a in (device.metadata_json or {}).get("aliases") or []
        }
        conflict = set(wanted) & existing
        if conflict:
            return wanted[sorted(conflict)[0]]
    return None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def to_view(
    session: Session,
    runtime: BrokerRuntime,
    device: Device,
    *,
    now: datetime | None = None,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
) -> DeviceView:
    now = now or datetime.now(UTC)
    metadata = device.metadata_json or {}
    outcomes = broker_service.recent_command_outcomes(
        session, device.id, limit=RECENT_OUTCOME_LIMIT
    )
    presence = compute_presence(
        is_connected=runtime.is_online(device.id),
        last_seen_at=_aware(device.last_seen_at),
        now=now,
        stale_after_s=stale_after_s,
    )
    return DeviceView(
        id=device.id,
        name=device.name,
        platform=device.platform,
        status=device.status,
        presence=presence,
        capabilities=tuple(device.capabilities_json or []),
        enrolled_at=_aware(device.enrolled_at) or now,
        last_seen_at=_aware(device.last_seen_at),
        aliases=tuple(metadata.get("aliases") or []),
        labels=tuple(metadata.get("labels") or []),
        policy=dict(metadata.get("policy") or {}),
        health=build_health(device, list(outcomes), now=now),
    )


def list_device_views(
    session: Session,
    runtime: BrokerRuntime,
    *,
    now: datetime | None = None,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
) -> list[DeviceView]:
    now = now or datetime.now(UTC)
    devices = broker_service.list_devices(session)
    return [
        to_view(session, runtime, d, now=now, stale_after_s=stale_after_s) for d in devices
    ]


def get_device_view(
    session: Session,
    runtime: BrokerRuntime,
    device_id: uuid.UUID,
    *,
    now: datetime | None = None,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
) -> DeviceView | None:
    device = broker_service.get_device(session, device_id)
    if device is None:
        return None
    return to_view(session, runtime, device, now=now, stale_after_s=stale_after_s)


def update_metadata(
    session: Session,
    device_id: uuid.UUID,
    *,
    aliases: list[str] | None = None,
    labels: list[str] | None = None,
    policy: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> Device | None:
    return broker_service.update_device_metadata(
        session,
        device_id,
        aliases=aliases,
        labels=labels,
        policy=policy,
        trace_id=trace_id,
    )


__all__ = [
    "RECENT_OUTCOME_LIMIT",
    "AliasConflictError",
    "find_alias_conflict",
    "get_device_view",
    "list_device_views",
    "to_view",
    "update_metadata",
]
