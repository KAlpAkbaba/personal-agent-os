"""``LocationService.resolve`` against the exact deterministic matrix the owner named
(docs/DECISIONS.md ADR-0090, task brief §5):

    explicit Ankara + current İstanbul -> Ankara
    no explicit + fresh GPS Antalya -> Antalya
    no GPS + default İstanbul -> İstanbul
    STALE GPS + default -> İstanbul
    Windows permission denied + default -> İstanbul
    nothing at all -> unresolved/clarification, never a guess
    IP says Germany + trusted İstanbul -> İstanbul
    IP only -> approximate and never silently authoritative

Plus the freshness/recent-trusted tier boundary and the default-write path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger.models import ActivityEventRow
from app.location.models import (
    CONFIDENCE_LOW,
    FRESHNESS_S,
    SOURCE_IP_COARSE,
    SOURCE_MOBILE_GPS,
    SOURCE_OWNER_DEFAULT,
    SOURCE_RECENT_TRUSTED_LOCATION,
    SOURCE_WINDOWS_LOCATION,
    LocationContextRow,
)
from app.location.providers import DeviceLocationSample
from app.location.service import (
    REASON_EXPLICIT,
    REASON_FRESH_DEVICE,
    REASON_IP_COARSE,
    REASON_OWNER_DEFAULT,
    REASON_RECENT_TRUSTED,
    REASON_UNRESOLVED,
    LocationService,
)

NOW = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
DEVICE_ID = uuid.uuid4()


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (LocationContextRow.__table__, ActivityEventRow.__table__):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


class _FakeIpProvider:
    def __init__(self, sample: DeviceLocationSample | None) -> None:
        self._sample = sample

    def locate(self) -> DeviceLocationSample | None:
        return self._sample


def _germany_sample() -> DeviceLocationSample:
    return DeviceLocationSample(
        city="Berlin",
        region=None,
        country="Germany",
        latitude=52.5,
        longitude=13.4,
        accuracy_m=None,
        timezone="Europe/Berlin",
        captured_at=NOW,
    )


def test_tier1_explicit_place_wins_over_a_fresh_trusted_device_location(db) -> None:
    """explicit Ankara + current İstanbul -> Ankara."""
    service = LocationService()
    service.record_observation(
        db, source=SOURCE_MOBILE_GPS, city="İstanbul", captured_at=NOW, device_id=DEVICE_ID
    )
    resolution = service.resolve(db, requested_place="Ankara", device_id=DEVICE_ID, now=NOW)
    assert resolution.resolved
    assert resolution.reason == REASON_EXPLICIT
    assert resolution.context.city == "Ankara"


def test_tier2_fresh_gps_wins_with_no_explicit_place(db) -> None:
    """no explicit + fresh GPS Antalya -> Antalya."""
    service = LocationService()
    service.record_observation(
        db,
        source=SOURCE_MOBILE_GPS,
        city="Antalya",
        captured_at=NOW - timedelta(minutes=5),
        device_id=DEVICE_ID,
    )
    resolution = service.resolve(db, device_id=DEVICE_ID, now=NOW)
    assert resolution.resolved
    assert resolution.reason == REASON_FRESH_DEVICE
    assert resolution.context.city == "Antalya"
    assert resolution.context.source == SOURCE_MOBILE_GPS


def test_tier3_owner_default_when_no_gps_at_all(db) -> None:
    """no GPS + default İstanbul -> İstanbul."""
    service = LocationService()
    service.set_default(db, city="İstanbul", now=NOW)
    resolution = service.resolve(db, now=NOW)
    assert resolution.resolved
    assert resolution.reason == REASON_OWNER_DEFAULT
    assert resolution.context.city == "İstanbul"
    assert resolution.context.source == SOURCE_OWNER_DEFAULT


def test_tier3_beats_tier2_a_stale_gps_falls_through_to_the_default(db) -> None:
    """STALE GPS (past BOTH the fresh and recent windows) + default -> İstanbul, never
    the stale fix."""
    service = LocationService()
    fresh_s, recent_s = FRESHNESS_S[SOURCE_MOBILE_GPS]
    stale_captured_at = NOW - timedelta(seconds=recent_s + 3600)  # well past "recent" too
    service.record_observation(
        db,
        source=SOURCE_MOBILE_GPS,
        city="Antalya",
        captured_at=stale_captured_at,
        device_id=DEVICE_ID,
    )
    service.set_default(db, city="İstanbul", now=NOW)
    resolution = service.resolve(db, device_id=DEVICE_ID, now=NOW)
    assert resolution.resolved
    assert resolution.reason == REASON_OWNER_DEFAULT
    assert resolution.context.city == "İstanbul"


def test_windows_permission_denied_falls_through_to_the_default(db) -> None:
    """Windows Location denied/unavailable (the provider answers None, exactly what
    ``UnavailableWindowsLocationProvider`` always does today) + default -> İstanbul."""

    class _DeniedWindowsProvider:
        def current(self, *, device_id=None):
            return None

    service = LocationService(windows_provider=_DeniedWindowsProvider())
    service.set_default(db, city="İstanbul", now=NOW)
    resolution = service.resolve(db, device_id=DEVICE_ID, now=NOW)
    assert resolution.resolved
    assert resolution.reason == REASON_OWNER_DEFAULT
    assert resolution.context.city == "İstanbul"


def test_nothing_at_all_is_unresolved_never_a_guess(db) -> None:
    """nothing at all -> unresolved/clarification, never a guess."""
    service = LocationService()
    resolution = service.resolve(db, now=NOW)
    assert resolution.resolved is False
    assert resolution.reason == REASON_UNRESOLVED
    assert resolution.context is None
    assert "bilmiyorum" in resolution.explanation


def test_ip_says_germany_but_a_trusted_istanbul_location_wins(db) -> None:
    """IP says Germany + trusted İstanbul -> İstanbul (order alone enforces this: IP is
    tier 5, only ever reached once tiers 1-4 have all failed)."""
    service = LocationService(ip_provider=_FakeIpProvider(_germany_sample()))
    service.record_observation(
        db,
        source=SOURCE_MOBILE_GPS,
        city="İstanbul",
        captured_at=NOW - timedelta(minutes=1),
        device_id=DEVICE_ID,
    )
    resolution = service.resolve(db, device_id=DEVICE_ID, now=NOW)
    assert resolution.resolved
    assert resolution.reason == REASON_FRESH_DEVICE
    assert resolution.context.city == "İstanbul"


def test_ip_only_is_approximate_and_never_silently_authoritative(db) -> None:
    """IP only -> approximate (LOW confidence) and the sentence says so."""
    service = LocationService(ip_provider=_FakeIpProvider(_germany_sample()))
    resolution = service.resolve(db, now=NOW)
    assert resolution.resolved
    assert resolution.reason == REASON_IP_COARSE
    assert resolution.context.source == SOURCE_IP_COARSE
    assert resolution.context.confidence == CONFIDENCE_LOW
    assert "yaklaşık" in resolution.explanation


def test_recent_trusted_tier_relabels_an_aged_but_still_usable_fix(db) -> None:
    """Past the fresh window but still inside the recent one -> tier 4, relabeled
    ``recent_trusted_location`` (never reported as a fresh windows_location fix)."""
    service = LocationService()
    fresh_s, recent_s = FRESHNESS_S[SOURCE_WINDOWS_LOCATION]
    aged = NOW - timedelta(seconds=fresh_s + 60)  # past fresh, still within recent
    service.record_observation(
        db, source=SOURCE_WINDOWS_LOCATION, city="Bursa", captured_at=aged, device_id=DEVICE_ID
    )
    resolution = service.resolve(db, device_id=DEVICE_ID, now=NOW)
    assert resolution.resolved
    assert resolution.reason == REASON_RECENT_TRUSTED
    assert resolution.context.source == SOURCE_RECENT_TRUSTED_LOCATION
    assert resolution.context.city == "Bursa"
    assert "güncel değil" in resolution.explanation


def test_set_default_demotes_the_previous_default(db) -> None:
    service = LocationService()
    service.set_default(db, city="İstanbul", now=NOW)
    service.set_default(db, city="Ankara", now=NOW + timedelta(seconds=1))
    row = service.get_default(db)
    assert row is not None
    assert row.city == "Ankara"
    assert row.source == SOURCE_OWNER_DEFAULT


def test_default_and_current_observation_never_collapse(db) -> None:
    """A default row is NEVER evidence of where the owner currently is (task brief §1):
    with only a default set and no device fix, tier 2 must still be empty."""
    service = LocationService()
    service.set_default(db, city="İstanbul", now=NOW)
    resolution = service.resolve(db, device_id=DEVICE_ID, now=NOW)
    assert resolution.reason == REASON_OWNER_DEFAULT  # tier 3, not tier 2
