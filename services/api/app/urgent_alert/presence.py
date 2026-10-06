"""Is the owner at a desk right now? The night trap's one question (urgent-alert-rung ADR, 4).

A toast "shown" means Windows put it on a screen, not that anyone looked: the home PC is on
at 03:00 and an important row's ladder would end at the toast while the phone never rings.
So for important rows the toast counts only when the companion's input idle time says
somebody touched a machine within :data:`PRESENT_WITHIN_S`.

The idle counter is read from the device status registry the companions' heartbeats fill
(``app.devices.status``), aged by how old the heartbeat is, and only from a heartbeat newer
than :data:`FRESH_WITHIN_S`. Nothing reported - no device, no idle counter, a stale report -
is "not known", and not known is "not present": a loud phone beats a silent miss.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from app.devices.status import DeviceStatusRegistry, get_status_registry

PRESENT_WITHIN_S: Final[float] = 120.0
#: Heartbeats arrive about every 10 s; past this a report says nothing about now.
FRESH_WITHIN_S: Final[float] = 90.0


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def owner_idle_seconds(
    *, now: datetime | None = None, registry: DeviceStatusRegistry | None = None
) -> float | None:
    moment = now or datetime.now(UTC)
    reg = registry or get_status_registry()
    try:
        statuses = list(reg.all().values())
    except Exception:  # noqa: BLE001 - an unreadable registry is "not known"
        return None
    idles: list[float] = []
    for status in statuses:
        if status.input_idle_s is None:
            continue
        age = (moment - _aware(status.observed_at)).total_seconds()
        if abs(age) > FRESH_WITHIN_S:
            continue
        idles.append(status.input_idle_s + max(age, 0.0))
    return min(idles) if idles else None


def owner_present(
    *, now: datetime | None = None, registry: DeviceStatusRegistry | None = None
) -> bool:
    idle = owner_idle_seconds(now=now, registry=registry)
    return idle is not None and idle < PRESENT_WITHIN_S


__all__ = ["FRESH_WITHIN_S", "PRESENT_WITHIN_S", "owner_idle_seconds", "owner_present"]
