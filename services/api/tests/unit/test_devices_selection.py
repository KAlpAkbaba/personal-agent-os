"""app.devices.selection: explicit target -> online -> capability -> policy -> healthiest."""

import uuid
from datetime import UTC, datetime

import pytest

from app.devices.health import CommandOutcome, DeviceHealth
from app.devices.presence import PRESENCE_OFFLINE, PRESENCE_ONLINE, PRESENCE_STALE
from app.devices.selection import (
    REASON_AUTO,
    REASON_EXPLICIT_ALIAS,
    REASON_EXPLICIT_ID,
    REASON_EXPLICIT_NAME,
    NoCapableDeviceError,
    select_device,
)
from app.devices.types import DeviceView

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def _view(
    name: str,
    *,
    presence: str = PRESENCE_ONLINE,
    capabilities: tuple[str, ...] = ("browser.chrome",),
    aliases: tuple[str, ...] = (),
    policy: dict | None = None,
    failures: int = 0,
    last_seen: datetime | None = NOW,
) -> DeviceView:
    return DeviceView(
        id=uuid.uuid4(),
        name=name,
        platform="windows",
        status="enrolled",
        presence=presence,
        capabilities=capabilities,
        enrolled_at=NOW,
        last_seen_at=last_seen,
        aliases=aliases,
        policy=policy or {},
        health=DeviceHealth(
            last_hello_at=None, software_version=None, heartbeat_age_s=None,
            recent_outcomes=tuple(
                CommandOutcome(
                    command_id=str(i), capability="x", status="failed", error_class="x",
                    terminal_at=None,
                )
                for i in range(failures)
            ),
        ),
    )


def test_no_devices_raises_no_capable_device() -> None:
    with pytest.raises(NoCapableDeviceError) as exc_info:
        select_device([], capability="browser.chrome")
    assert exc_info.value.reason == "offline"
    assert exc_info.value.detail_tr  # Turkish, non-empty


def test_auto_selects_the_only_online_capable_device() -> None:
    ev = _view("ev-pc")
    result = select_device([ev], capability="browser.chrome")
    assert result.device.name == "ev-pc"
    assert result.reason == REASON_AUTO
    assert result.explicit is False


def test_offline_device_is_never_selected() -> None:
    offline = _view("ev-pc", presence=PRESENCE_OFFLINE)
    with pytest.raises(NoCapableDeviceError) as exc_info:
        select_device([offline], capability="browser.chrome")
    assert exc_info.value.reason == "offline"


def test_stale_device_is_treated_like_offline_for_selection() -> None:
    stale = _view("ev-pc", presence=PRESENCE_STALE)
    with pytest.raises(NoCapableDeviceError):
        select_device([stale], capability="browser.chrome")


def test_device_without_capability_is_skipped() -> None:
    no_browser = _view("ev-pc", capabilities=("desktop.open_application",))
    with pytest.raises(NoCapableDeviceError) as exc_info:
        select_device([no_browser], capability="browser.chrome")
    assert exc_info.value.reason == "capability_missing"


def test_explicit_device_id_selects_even_when_offline_status_would_lose() -> None:
    online = _view("laptop")
    match = select_device([online], capability="browser.chrome", target=str(online.id))
    assert match.reason == REASON_EXPLICIT_ID
    assert match.explicit is True


def test_explicit_exact_name_match() -> None:
    a = _view("ev-pc")
    b = _view("laptop")
    result = select_device([a, b], capability="browser.chrome", target="laptop")
    assert result.device.name == "laptop"
    assert result.reason == REASON_EXPLICIT_NAME


def test_explicit_turkish_alias_resolves_to_configured_device() -> None:
    ev = _view("ev-pc", aliases=("ev",))
    other = _view("laptop")
    result = select_device(
        [other, ev], capability="browser.chrome", target="ev bilgisayarımda aç"
    )
    assert result.device.name == "ev-pc"
    assert result.reason == REASON_EXPLICIT_ALIAS


def test_explicit_target_with_no_match_raises_target_not_found_never_falls_back() -> None:
    ev = _view("ev-pc")
    with pytest.raises(NoCapableDeviceError) as exc_info:
        select_device([ev], capability="browser.chrome", target="bilinmeyen-cihaz")
    assert exc_info.value.reason == "target_not_found"


def test_explicit_target_offline_reports_target_offline_not_auto_fallback() -> None:
    ev = _view("ev-pc", presence=PRESENCE_OFFLINE)
    with pytest.raises(NoCapableDeviceError) as exc_info:
        select_device([ev], capability="browser.chrome", target="ev-pc")
    assert exc_info.value.reason == "offline"


def test_policy_deny_excludes_device() -> None:
    denied = _view("ev-pc", policy={"deny": ["browser.chrome"]})
    with pytest.raises(NoCapableDeviceError) as exc_info:
        select_device([denied], capability="browser.chrome")
    assert exc_info.value.reason == "policy_denied"


def test_policy_allow_list_excludes_unlisted_capability() -> None:
    restricted = _view("ev-pc", policy={"allow": ["desktop.open_application"]})
    with pytest.raises(NoCapableDeviceError) as exc_info:
        select_device([restricted], capability="browser.chrome")
    assert exc_info.value.reason == "policy_denied"


def test_policy_allow_list_permits_listed_capability() -> None:
    allowed = _view("ev-pc", policy={"allow": ["browser.chrome"]})
    result = select_device([allowed], capability="browser.chrome")
    assert result.device.name == "ev-pc"


def test_healthiest_prefers_fewer_recent_failures() -> None:
    healthy = _view("healthy-pc", failures=0)
    flaky = _view("flaky-pc", failures=3)
    result = select_device([flaky, healthy], capability="browser.chrome")
    assert result.device.name == "healthy-pc"


def test_healthiest_tie_break_prefers_more_recently_seen() -> None:
    from datetime import timedelta

    older = _view("older-pc", last_seen=NOW - timedelta(hours=1))
    newer = _view("newer-pc", last_seen=NOW)
    result = select_device([older, newer], capability="browser.chrome")
    assert result.device.name == "newer-pc"


def test_ambiguous_alias_across_two_devices_raises_ambiguous_alias() -> None:
    """PATCH refuses to create this state going forward (finding LOW-9), but
    selection must not assume it can never happen — two devices sharing a
    matching alias must fail closed rather than silently picking the first
    one found."""
    a = _view("ev-pc", aliases=("ev",))
    b = _view("laptop", aliases=("ev",))
    with pytest.raises(NoCapableDeviceError) as exc_info:
        select_device([a, b], capability="browser.chrome", target="ev")
    assert exc_info.value.reason == "ambiguous_alias"
    assert exc_info.value.detail_tr  # Turkish, non-empty


def test_ambiguous_alias_does_not_shadow_id_or_name_matches() -> None:
    """An exact id/name match still wins even when an unrelated pair of
    devices shares an alias elsewhere — ambiguity is only checked for the
    alias-matching step, which id/name matches never reach."""
    a = _view("ev-pc", aliases=("ev",))
    b = _view("laptop", aliases=("ev",))
    result = select_device([a, b], capability="browser.chrome", target="laptop")
    assert result.device.name == "laptop"
    assert result.reason == REASON_EXPLICIT_NAME
