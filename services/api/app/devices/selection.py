"""DeviceSelection: explicit target -> online -> capability -> policy -> healthiest.

BROWSER_CAPABILITIES.md §8 / M13 spec §4/§8: given a requested capability and
an optional owner-supplied target (device id, exact name, or a Turkish alias
phrase — ``app.devices.aliases``), pick exactly one device or fail with a
typed, Turkish-explained ``NoCapableDeviceError``. The algorithm never falls
back silently past an explicit owner target: if the owner named a device and
it does not resolve, that is reported as its own reason rather than quietly
auto-selecting a different machine.

Policy (``devices.metadata_json.policy``) is intentionally minimal and
additive, since neither spec document freezes its shape: an optional
``allow`` list and an optional ``deny`` list of capability names/families
(matched with the same ``app.devices.capabilities.has_capability`` rule). A
capability is policy-allowed when it is not in ``deny`` AND (``allow`` is
absent/empty OR the capability is in ``allow``). This is a documented
implementation choice, not part of the frozen contract — see the M13 report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.devices import aliases
from app.devices.capabilities import has_capability
from app.devices.presence import PRESENCE_ONLINE
from app.devices.types import DeviceView

REASON_EXPLICIT_ID = "explicit_id"
REASON_EXPLICIT_NAME = "explicit_name"
REASON_EXPLICIT_ALIAS = "explicit_alias"
REASON_AUTO = "auto"


class NoCapableDeviceError(Exception):
    """No device satisfies the request. ``detail_tr`` is owner-facing Turkish."""

    def __init__(
        self,
        detail_tr: str,
        *,
        capability: str,
        target: str | None = None,
        reason: str = "no_match",
    ) -> None:
        super().__init__(detail_tr)
        self.detail_tr = detail_tr
        self.capability = capability
        self.target = target
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SelectionResult:
    device: DeviceView
    reason: str
    explicit: bool


def _policy_allows(policy: dict[str, Any], capability: str) -> bool:
    deny = policy.get("deny") or []
    allow = policy.get("allow") or []
    if any(has_capability([capability], d) or has_capability([d], capability) for d in deny):
        return False
    if not allow:
        return True
    return any(has_capability([capability], a) or has_capability([a], capability) for a in allow)


def _match_explicit(
    devices: list[DeviceView], target: str, *, capability: str
) -> tuple[DeviceView, str] | None:
    stripped = target.strip()
    # 1) device id (exact)
    for d in devices:
        if str(d.id) == stripped:
            return d, REASON_EXPLICIT_ID
    # 2) exact name (case-insensitive)
    lowered = stripped.casefold()
    for d in devices:
        if d.name.casefold() == lowered:
            return d, REASON_EXPLICIT_NAME
    # 3) alias (owner-configured, matched via Turkish phrase extraction).
    # Collected in full (not "first match wins") so two devices sharing a
    # misconfigured alias raise ambiguous_alias instead of one silently
    # shadowing the other (finding LOW-9; PATCH already refuses to create
    # this state going forward, but selection must not trust that it never
    # happened by some other path).
    alias_matches = [d for d in devices if aliases.alias_matches(list(d.aliases), stripped)]
    if len(alias_matches) > 1:
        raise NoCapableDeviceError(
            f"'{target}' takma adı birden fazla cihazda tanımlı; lütfen cihaz adını "
            "veya kimliğini kullanın ya da takma adları güncelleyin.",
            capability=capability,
            target=target,
            reason="ambiguous_alias",
        )
    if alias_matches:
        return alias_matches[0], REASON_EXPLICIT_ALIAS
    return None


def select_device(
    devices: list[DeviceView],
    *,
    capability: str,
    target: str | None = None,
    now: datetime | None = None,
) -> SelectionResult:
    del now  # presence is already resolved on each DeviceView by the caller
    candidates = devices
    explicit = False
    reason = REASON_AUTO

    if target and target.strip():
        match = _match_explicit(devices, target, capability=capability)
        if match is None:
            raise NoCapableDeviceError(
                f"'{target}' adında veya takma adında kayıtlı bir cihaz bulunamadı.",
                capability=capability,
                target=target,
                reason="target_not_found",
            )
        matched_device, reason = match
        candidates = [matched_device]
        explicit = True

    online = [d for d in candidates if d.presence == PRESENCE_ONLINE]
    if not online:
        detail = (
            f"'{target}' cihazı şu anda çevrimiçi değil."
            if explicit
            else "Şu anda çevrimiçi bir cihaz bulunamadı."
        )
        raise NoCapableDeviceError(
            detail, capability=capability, target=target, reason="offline"
        )

    capable = [d for d in online if has_capability(d.capabilities, capability)]
    if not capable:
        detail = (
            f"'{target}' cihazı '{capability}' yeteneğine sahip değil."
            if explicit
            else f"'{capability}' yeteneğine sahip çevrimiçi bir cihaz bulunamadı."
        )
        raise NoCapableDeviceError(
            detail, capability=capability, target=target, reason="capability_missing"
        )

    allowed = [d for d in capable if _policy_allows(d.policy, capability)]
    if not allowed:
        detail = (
            f"Politika '{target}' cihazında bu işleme izin vermiyor."
            if explicit
            else "Politika hiçbir cihazda bu işleme izin vermiyor."
        )
        raise NoCapableDeviceError(
            detail, capability=capability, target=target, reason="policy_denied"
        )

    best = _healthiest(allowed)
    return SelectionResult(device=best, reason=reason, explicit=explicit)


def _healthiest(devices: list[DeviceView]) -> DeviceView:
    """Fewest recent command failures, then most recently seen, then name
    (deterministic tie-break — never device order, never a hardcoded id)."""

    def key(d: DeviceView) -> tuple[int, str, str]:
        failures = d.health.recent_failure_count if d.health else 0
        last_seen = d.last_seen_at.isoformat() if d.last_seen_at else ""
        return (failures, "" if last_seen else "z", d.name)

    ranked = sorted(devices, key=key)
    # Prefer the most recently seen among the least-failing tier.
    least_failures = ranked[0].health.recent_failure_count if ranked[0].health else 0
    tier = [
        d
        for d in devices
        if (d.health.recent_failure_count if d.health else 0) == least_failures
    ]
    tier.sort(key=lambda d: d.last_seen_at or d.enrolled_at, reverse=True)
    return tier[0]


__all__ = [
    "NoCapableDeviceError",
    "REASON_AUTO",
    "REASON_EXPLICIT_ALIAS",
    "REASON_EXPLICIT_ID",
    "REASON_EXPLICIT_NAME",
    "SelectionResult",
    "select_device",
]
