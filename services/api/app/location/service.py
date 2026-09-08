"""``LocationService.resolve``: the ONE resolution order (docs/DECISIONS.md ADR-0090,
task brief §1) every location-consuming capability goes through — weather first, anything
else later, always through this same order, never a second one:

    1. a location named in the request        (explicit_owner_request)
    2. a fresh trusted device location         (windows_location / mobile_gps, tier 2)
    3. the owner's configured default          (owner_default)
    4. a sufficiently fresh previously-trusted location (recent_trusted_location, tier 4)
    5. coarse IP, LOW_CONFIDENCE only           (ip_coarse)
    6. unresolved — say so and ask, never guess

Home/default (tier 3) and current-observed (tiers 1/2/4) are different concepts and are
never collapsed: tier 3 is read ONLY after a fresh device capture already failed, and a
default row can never BECOME "the owner is currently there" evidence — see
``app.location.models`` module docstring.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.ledger import service as ledger_service
from app.ledger.vocabulary import EVENT_TYPE_LOCATION_DEFAULT_SET, SUBSYSTEM_LOCATION
from app.location.models import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    FRESHNESS_S,
    PERMISSION_SCOPE_WEATHER,
    SOURCE_EXPLICIT_OWNER_REQUEST,
    SOURCE_IP_COARSE,
    SOURCE_MOBILE_GPS,
    SOURCE_OWNER_DEFAULT,
    SOURCE_RECENT_TRUSTED_LOCATION,
    SOURCE_WINDOWS_LOCATION,
    LocationContextRow,
)
from app.location.providers import (
    IpCoarseLocationProvider,
    WindowsLocationProvider,
)
from app.logging import get_logger

logger = get_logger("app.location.service")

_DEVICE_SOURCES: tuple[str, ...] = (SOURCE_WINDOWS_LOCATION, SOURCE_MOBILE_GPS)

REASON_EXPLICIT = "explicit_owner_request"
REASON_FRESH_DEVICE = "fresh_device_location"
REASON_OWNER_DEFAULT = "owner_default"
REASON_RECENT_TRUSTED = "recent_trusted_location"
REASON_IP_COARSE = "ip_coarse"
REASON_UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class LocationContext:
    """The portable, in-memory shape every consumer (``app.weather`` first) reads —
    never a Windows/IP call, never an ORM row (task brief: "the weather resolver
    consumes a LocationContext, never Windows directly")."""

    source: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    accuracy_m: float | None = None
    captured_at: datetime | None = None
    timezone: str | None = None
    device_id: uuid.UUID | None = None
    confidence: str = CONFIDENCE_MEDIUM
    is_default: bool = False
    permission_scope: str = PERMISSION_SCOPE_WEATHER
    location_id: uuid.UUID | None = None  # set only when backed by a stored row

    @classmethod
    def from_row(cls, row: LocationContextRow) -> LocationContext:
        return cls(
            source=row.source,
            city=row.city,
            region=row.region,
            country=row.country,
            latitude=row.latitude,
            longitude=row.longitude,
            accuracy_m=row.accuracy_m,
            captured_at=row.captured_at,
            timezone=row.timezone,
            device_id=row.device_id,
            confidence=row.confidence,
            is_default=row.is_default,
            permission_scope=row.permission_scope,
            location_id=row.location_id,
        )

    @property
    def label(self) -> str:
        """The best short place name available, for a spoken sentence."""
        return self.city or self.region or self.country or "bilinmeyen bir yer"


@dataclass(frozen=True, slots=True)
class LocationResolution:
    resolved: bool
    context: LocationContext | None
    reason: str  # one of the REASON_* constants above
    explanation: str  # Turkish sentence answering "nereden biliyorsun" truthfully


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _age_s(captured_at: datetime, now: datetime) -> float:
    return (now - _aware(captured_at)).total_seconds()


def _explain(ctx: LocationContext, *, now: datetime) -> str:
    place = ctx.label
    if ctx.source == SOURCE_EXPLICIT_OWNER_REQUEST:
        return f"Sorduğunuzda {place} dediniz, onu kullanıyorum."
    if ctx.source == SOURCE_OWNER_DEFAULT:
        return f"Varsayılan hava durumu konumunuz {place} olarak ayarlı."
    if ctx.source in (SOURCE_WINDOWS_LOCATION, SOURCE_MOBILE_GPS):
        age_min = int(_age_s(ctx.captured_at, now) / 60) if ctx.captured_at else None
        age = f"{age_min} dakika önce" if age_min is not None else "yakın zamanda"
        return f"Cihazınızın {age} bildirdiği konuma göre {place} kullanıyorum."
    if ctx.source == SOURCE_RECENT_TRUSTED_LOCATION:
        age_min = int(_age_s(ctx.captured_at, now) / 60) if ctx.captured_at else None
        age = f"{age_min} dakika önce" if age_min is not None else "bir süre önce"
        return (
            f"Cihazınızdan {age} alınan, artık taze sayılmayan {place} konumunu "
            "kullanıyorum; güncel değil."
        )
    if ctx.source == SOURCE_IP_COARSE:
        return f"Yalnızca IP adresinizden yaklaşık olarak {place} tahmin ediyorum; kesin değil."
    return f"{place} konumunu kullanıyorum."


class LocationService:
    def __init__(
        self,
        *,
        windows_provider: WindowsLocationProvider | None = None,
        ip_provider: IpCoarseLocationProvider | None = None,
    ) -> None:
        self._windows_provider = windows_provider
        self._ip_provider = ip_provider

    # ------------------------------------------------------------------ resolution

    def _latest_device_row(
        self,
        session: Session,
        *,
        device_id: uuid.UUID | None,
        now: datetime,
        fresh: bool,
    ) -> LocationContextRow | None:
        """The newest ``windows_location``/``mobile_gps`` row within the fresh window
        (``fresh=True``, tier 2) or within the recent-but-not-fresh window
        (``fresh=False``, tier 4) — per-source thresholds (``FRESHNESS_S``), since a
        phone and a desktop age differently."""
        stmt = (
            select(LocationContextRow)
            .where(LocationContextRow.source.in_(_DEVICE_SOURCES))
            .order_by(LocationContextRow.captured_at.desc())
        )
        if device_id is not None:
            stmt = stmt.where(LocationContextRow.device_id == device_id)
        for row in session.execute(stmt).scalars().all():
            fresh_s, recent_s = FRESHNESS_S.get(row.source, (0.0, 0.0))
            age = _age_s(row.captured_at, now)
            if fresh and age <= fresh_s:
                return row
            if not fresh and fresh_s < age <= recent_s:
                return row
        return None

    def resolve(
        self,
        session: Session,
        *,
        requested_place: str | None = None,
        device_id: uuid.UUID | None = None,
        capability: str = PERMISSION_SCOPE_WEATHER,
        now: datetime | None = None,
    ) -> LocationResolution:
        now = now or datetime.now(UTC)

        # tier 1 - a place named in the request always wins, whatever the device says.
        place = (requested_place or "").strip()
        if place:
            ctx = LocationContext(
                source=SOURCE_EXPLICIT_OWNER_REQUEST,
                city=place,
                captured_at=now,
                confidence=CONFIDENCE_HIGH,
                permission_scope=capability,
            )
            return LocationResolution(True, ctx, REASON_EXPLICIT, _explain(ctx, now=now))

        # tier 2 - a fresh trusted device location. Try a live pull first (inert today -
        # UnavailableWindowsLocationProvider always answers None, see app.location.
        # providers module docstring); either way, fall back to the latest stored capture
        # still inside its fresh window.
        if self._windows_provider is not None:
            sample = self._windows_provider.current(device_id=device_id)
            if sample is not None:
                self.record_observation(
                    session,
                    source=SOURCE_WINDOWS_LOCATION,
                    city=sample.city,
                    region=sample.region,
                    country=sample.country,
                    latitude=sample.latitude,
                    longitude=sample.longitude,
                    accuracy_m=sample.accuracy_m,
                    timezone=sample.timezone,
                    device_id=device_id,
                    captured_at=sample.captured_at,
                    permission_scope=capability,
                )
        row = self._latest_device_row(session, device_id=device_id, now=now, fresh=True)
        if row is not None:
            ctx = LocationContext.from_row(row)
            return LocationResolution(True, ctx, REASON_FRESH_DEVICE, _explain(ctx, now=now))

        # tier 3 - the owner's configured default.
        default_row = self.get_default(session, capability=capability)
        if default_row is not None:
            ctx = LocationContext.from_row(default_row)
            return LocationResolution(True, ctx, REASON_OWNER_DEFAULT, _explain(ctx, now=now))

        # tier 4 - a sufficiently fresh PREVIOUSLY-trusted location (aged past tier 2's
        # window but not past its recent one). Never returned as source=windows_location/
        # mobile_gps: relabeled so a "how do you know" answer says it is aging.
        row = self._latest_device_row(session, device_id=device_id, now=now, fresh=False)
        if row is not None:
            ctx = replace(
                LocationContext.from_row(row),
                source=SOURCE_RECENT_TRUSTED_LOCATION,
                confidence=CONFIDENCE_MEDIUM,
            )
            return LocationResolution(True, ctx, REASON_RECENT_TRUSTED, _explain(ctx, now=now))

        # tier 5 - coarse IP, LOW_CONFIDENCE only, never persisted (module docstring:
        # "do not build a coordinate history by default") and never reached while any
        # trusted location (tiers 2-4) exists — VPNs make an IP lookup wrong, and it may
        # never overwrite a trusted location (task brief).
        if self._ip_provider is not None:
            sample = self._ip_provider.locate()
            if sample is not None and (sample.city or sample.country):
                ctx = LocationContext(
                    source=SOURCE_IP_COARSE,
                    city=sample.city,
                    region=sample.region,
                    country=sample.country,
                    latitude=sample.latitude,
                    longitude=sample.longitude,
                    captured_at=now,
                    timezone=sample.timezone,
                    confidence=CONFIDENCE_LOW,
                    permission_scope=capability,
                )
                return LocationResolution(True, ctx, REASON_IP_COARSE, _explain(ctx, now=now))

        # tier 6 - unresolved. The honest failure, never a guess (task brief).
        return LocationResolution(
            False,
            None,
            REASON_UNRESOLVED,
            "Şu an neredesiniz bilmiyorum efendim; söyler misiniz, ya da varsayılan bir "
            "hava durumu konumu ayarlayabiliriz.",
        )

    # -------------------------------------------------------------------- default

    def get_default(
        self, session: Session, *, capability: str = PERMISSION_SCOPE_WEATHER
    ) -> LocationContextRow | None:
        stmt = (
            select(LocationContextRow)
            .where(
                LocationContextRow.is_default.is_(True),
                LocationContextRow.permission_scope == capability,
            )
            .order_by(LocationContextRow.captured_at.desc())
            .limit(1)
        )
        return session.execute(stmt).scalars().first()

    def set_default(
        self,
        session: Session,
        *,
        city: str,
        region: str | None = None,
        country: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        timezone: str | None = None,
        capability: str = PERMISSION_SCOPE_WEATHER,
        now: datetime | None = None,
    ) -> LocationContextRow:
        """The ONLY writer of a durable default (task brief: "Do NOT invent one").
        Demotes any previous default for this capability in the same transaction it
        inserts the new row, so ``get_default`` never sees two."""
        now = now or datetime.now(UTC)
        session.execute(
            sa_update(LocationContextRow)
            .where(
                LocationContextRow.is_default.is_(True),
                LocationContextRow.permission_scope == capability,
            )
            .values(is_default=False)
        )
        row = LocationContextRow(
            location_id=uuid.uuid4(),
            source=SOURCE_OWNER_DEFAULT,
            city=city.strip(),
            region=region,
            country=country,
            latitude=latitude,
            longitude=longitude,
            accuracy_m=None,
            captured_at=now,
            expires_at=None,  # a default never expires by staleness - only by being replaced
            timezone=timezone,
            device_id=None,
            confidence=CONFIDENCE_HIGH,
            is_default=True,
            permission_scope=capability,
        )
        session.add(row)
        session.commit()
        try:
            ledger_service.record(
                session,
                ledger_service.ActivityEvent(
                    event_type=EVENT_TYPE_LOCATION_DEFAULT_SET,
                    subsystem=SUBSYSTEM_LOCATION,
                    action="location.set_default",
                    factual_summary=f"Varsayılan {capability} konumu {row.city} olarak ayarlandı.",
                    occurred_at=now,
                    detail_json={"capability": capability, "city": row.city},
                    source="live",
                    source_ref=f"location_context:{row.location_id}:default_set",
                ),
            )
        except Exception:  # noqa: BLE001 - evidence, never a dependency of the write
            logger.warning("location_default_ledger_failed")
        return row

    # -------------------------------------------------------------------- observation

    def record_observation(
        self,
        session: Session,
        *,
        source: str,
        city: str | None = None,
        region: str | None = None,
        country: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        accuracy_m: float | None = None,
        timezone: str | None = None,
        device_id: uuid.UUID | None = None,
        captured_at: datetime | None = None,
        permission_scope: str = PERMISSION_SCOPE_WEATHER,
    ) -> LocationContextRow:
        """Durable capture from a device (``windows_location``/``mobile_gps``) — the
        write side of the seam ``app.location.providers`` documents. No live caller
        exists in production yet (no device advertises the capability), but the path is
        exercised directly by tests so the resolver's tiers 2/4 are proven against real
        rows now, not only against a mock."""
        if source not in _DEVICE_SOURCES:
            raise ValueError(f"record_observation source must be a device source, got {source!r}")
        captured_at = captured_at or datetime.now(UTC)
        fresh_s, _ = FRESHNESS_S[source]
        row = LocationContextRow(
            location_id=uuid.uuid4(),
            source=source,
            city=city,
            region=region,
            country=country,
            latitude=latitude,
            longitude=longitude,
            accuracy_m=accuracy_m,
            captured_at=captured_at,
            expires_at=_aware(captured_at) + timedelta(seconds=fresh_s),
            timezone=timezone,
            device_id=device_id,
            confidence=CONFIDENCE_HIGH,
            is_default=False,
            permission_scope=permission_scope,
        )
        session.add(row)
        session.commit()
        return row

    # ------------------------------------------------------------------------ explain

    def is_fresh(self, ctx: LocationContext, *, now: datetime | None = None) -> bool | None:
        """``True``/``False`` for a device-sourced context, ``None`` when freshness does
        not apply (explicit/default/ip_coarse) — "Konumum güncel mi?" reads this."""
        if ctx.source not in _DEVICE_SOURCES or ctx.captured_at is None:
            return None
        now = now or datetime.now(UTC)
        fresh_s, _ = FRESHNESS_S.get(ctx.source, (0.0, 0.0))
        return _age_s(ctx.captured_at, now) <= fresh_s


__all__ = [
    "REASON_EXPLICIT",
    "REASON_FRESH_DEVICE",
    "REASON_IP_COARSE",
    "REASON_OWNER_DEFAULT",
    "REASON_RECENT_TRUSTED",
    "REASON_UNRESOLVED",
    "LocationContext",
    "LocationResolution",
    "LocationService",
]
