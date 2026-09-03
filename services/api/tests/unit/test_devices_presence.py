"""app.devices.presence: online / stale / offline."""

from datetime import UTC, datetime, timedelta

from app.devices.presence import (
    PRESENCE_OFFLINE,
    PRESENCE_ONLINE,
    PRESENCE_STALE,
    compute_presence,
    heartbeat_age_s,
)

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def test_connected_device_is_online_regardless_of_last_seen() -> None:
    assert compute_presence(is_connected=True, last_seen_at=None, now=NOW) == PRESENCE_ONLINE


def test_disconnected_with_no_last_seen_is_offline() -> None:
    assert compute_presence(is_connected=False, last_seen_at=None, now=NOW) == PRESENCE_OFFLINE


def test_disconnected_recently_seen_is_stale() -> None:
    last_seen = NOW - timedelta(seconds=10)
    result = compute_presence(is_connected=False, last_seen_at=last_seen, now=NOW, stale_after_s=30)
    assert result == PRESENCE_STALE


def test_disconnected_seen_long_ago_is_offline() -> None:
    last_seen = NOW - timedelta(seconds=120)
    result = compute_presence(is_connected=False, last_seen_at=last_seen, now=NOW, stale_after_s=30)
    assert result == PRESENCE_OFFLINE


def test_heartbeat_age_s_none_when_never_seen() -> None:
    assert heartbeat_age_s(None, NOW) is None


def test_heartbeat_age_s_computes_seconds() -> None:
    last_seen = NOW - timedelta(seconds=42)
    assert heartbeat_age_s(last_seen, NOW) == 42.0
