"""Device/IP location providers behind Protocols (module discipline shared with
``app.calendar.providers`` / ``app.mail.providers``: ``app.location.service`` never
imports a transport directly, only these interfaces).

``WindowsLocationProvider`` is the seam a future capable device plugs into.
MEASURED for this task (2026-09-08): the deployed Windows agent advertises 29
capabilities — the browser family plus ``desktop.alarm_start``/``alarm_stop``/
``open_application``/``open_artifact`` — and NOTHING location-related. So
``UnavailableWindowsLocationProvider`` is registered today: it always answers "no
location", with the reason recorded, never a fake fix. The day a device gains a location
capability, a real provider implements the same Protocol and ``build_windows_location_
provider`` swaps to it — nothing in ``app.location.service`` or ``app.weather`` changes.

``IpCoarseLocationProvider`` stays unconfigured by default on the SAME principle
``app.mail.providers``/``app.calendar.providers`` already apply to a real account: no
signup, no invented key/vendor (CLAUDE.md's "Asking the owner" + "never invent a
credential"). Generic IP-geolocation vendors commonly gate anything beyond trivial testing
behind registration/ToS in ways the keyless weather provider (``app.weather.providers``,
Open-Meteo — genuinely no signup required) does not, so this one stays an owner-configured
URL rather than a hardcoded default vendor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx

from app.logging import get_logger

logger = get_logger("app.location.providers")


@dataclass(frozen=True, slots=True)
class DeviceLocationSample:
    """What a device-side location fix carries, before it becomes a durable
    ``LocationContextRow`` (``app.location.service.LocationService.record_observation``)."""

    city: str | None
    region: str | None
    country: str | None
    latitude: float | None
    longitude: float | None
    accuracy_m: float | None
    timezone: str | None
    captured_at: datetime


class WindowsLocationProvider(Protocol):
    def current(self, *, device_id: uuid.UUID | None) -> DeviceLocationSample | None: ...


class UnavailableWindowsLocationProvider:
    """Always ``None`` — see module docstring. ``reason`` is read by
    ``LocationService`` so a caller can say plainly WHY, never guess a location instead."""

    reason: str = "no_location_capability_on_device_agent"

    def current(self, *, device_id: uuid.UUID | None = None) -> DeviceLocationSample | None:
        del device_id
        return None


def build_windows_location_provider(settings: Any) -> WindowsLocationProvider:
    del settings  # no setting can turn this on; a real capability has to exist first
    return UnavailableWindowsLocationProvider()


class IpCoarseLocationProvider(Protocol):
    def locate(self) -> DeviceLocationSample | None: ...


class HttpIpCoarseLocationProvider:
    """A GET to an owner-configured IP-geolocation endpoint returning JSON with (at
    least) ``city``/``country``/``lat`` or ``latitude``/``lon`` or ``longitude`` — field
    names kept loose (``_first``) because the owner may point this at any of several
    common shapes; a response this cannot parse is treated as unavailable, never a
    fabricated fix."""

    def __init__(self, *, base_url: str, timeout_s: float = 5.0) -> None:
        self._base_url = base_url
        self._timeout_s = timeout_s

    @staticmethod
    def _first(data: dict[str, Any], *keys: str) -> Any | None:
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]
        return None

    def locate(self) -> DeviceLocationSample | None:
        try:
            response = httpx.get(self._base_url, timeout=self._timeout_s)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001 - a failed lookup is "no location", not a crash
            logger.info("ip_coarse_location_failed", error=f"{type(exc).__name__}: {exc}")
            return None
        lat = self._first(data, "lat", "latitude")
        lon = self._first(data, "lon", "longitude")
        try:
            latitude = float(lat) if lat is not None else None
            longitude = float(lon) if lon is not None else None
        except (TypeError, ValueError):
            latitude = longitude = None
        return DeviceLocationSample(
            city=self._first(data, "city"),
            region=self._first(data, "region", "regionName"),
            country=self._first(data, "country", "country_name"),
            latitude=latitude,
            longitude=longitude,
            accuracy_m=None,  # IP geolocation carries no meaningful accuracy figure
            timezone=self._first(data, "timezone"),
            captured_at=_now(),
        )


def _now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


def build_ip_coarse_location_provider(settings: Any) -> IpCoarseLocationProvider | None:
    """``None`` (dependency_unavailable at tier 5) until the owner sets
    ``PAGENTOS_LOCATION_IP_GEO_URL`` — see module docstring."""
    url = getattr(settings, "location_ip_geo_url", "") or ""
    if not url:
        return None
    timeout_s = float(getattr(settings, "location_ip_geo_timeout_s", 5.0) or 5.0)
    return HttpIpCoarseLocationProvider(base_url=url, timeout_s=timeout_s)


__all__ = [
    "DeviceLocationSample",
    "HttpIpCoarseLocationProvider",
    "IpCoarseLocationProvider",
    "UnavailableWindowsLocationProvider",
    "WindowsLocationProvider",
    "build_ip_coarse_location_provider",
    "build_windows_location_provider",
]
