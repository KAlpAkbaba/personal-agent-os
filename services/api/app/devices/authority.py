"""B05 req 663/246: the server decides whether a device command may be created.

Until 2026-09-12 nothing on this side checked anything. ``create_command`` inserted whatever
capability string it was handed, and the only thing standing between a command and the
owner's machine was the DEVICE's own refusal once the command arrived. That is one half of a
boundary, held by the half that is furthest away and easiest to replace.

Two questions are asked here, both answerable from durable server-side state and neither of
them from anything the caller sent:

* is this device one we enrolled, and still trusted? (a revoked device is refused outright)
* does it ADVERTISE the capability being asked of it? (matched by
  ``app.devices.capabilities.has_capability``, so a family marker still counts)

**Mode.** The gate ships in ``shadow``: it evaluates, records and counts, and lets everything
through. That is deliberate and is the roadmap's own rollback plan - a gate that starts by
blocking is a gate whose first production appearance is an outage. ``enforce`` is fully
implemented and tested; turning it on is the owner's call once a day of production shadow
counting says how many real calls it would have refused.

**What this is NOT.** It is not authentication - the caller already holds an owner session -
and it is not the speaker gate. Voice identity never decides anything here; see
``app.security.step_up`` for the sensitive-action path where a speaker verdict is consumed.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Final

from app.devices.capabilities import has_capability
from app.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.broker.models import Device

logger = get_logger("app.devices.authority")

GATE_MODE_SHADOW: Final[str] = "shadow"
GATE_MODE_ENFORCE: Final[str] = "enforce"
GATE_MODES: Final[tuple[str, ...]] = (GATE_MODE_SHADOW, GATE_MODE_ENFORCE)

#: Refusal reasons. Stable strings: they are counted, audited and read back by the owner
#: when deciding whether enforcement is safe to turn on.
REASON_UNKNOWN_DEVICE: Final[str] = "unknown_device"
REASON_DEVICE_REVOKED: Final[str] = "device_revoked"
REASON_CAPABILITY_NOT_ADVERTISED: Final[str] = "capability_not_advertised"


@dataclasses.dataclass(frozen=True, slots=True)
class GateDecision:
    """What the gate concluded, and what actually happened as a result.

    ``allowed`` is the outcome after the mode is applied; ``would_refuse`` is the conclusion
    itself. In shadow mode a refused command has ``allowed=True`` and ``would_refuse=True``,
    which is the whole point of shadow mode: the answer is recorded without acting on it.
    """

    allowed: bool
    would_refuse: bool
    reason: str | None
    mode: str
    capability: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "would_refuse": self.would_refuse,
            "reason": self.reason,
            "mode": self.mode,
            "capability": self.capability,
        }


class DeviceCommandRefused(PermissionError):
    """Raised by the gate in ``enforce`` mode. Carries the reason, never the payload."""

    def __init__(self, decision: GateDecision) -> None:
        super().__init__(f"device command refused: {decision.reason}")
        self.decision = decision


#: The process-wide mode. One policy for one process, resolved at startup by
#: ``create_app`` - NOT a per-call argument, because a per-call argument is a per-call
#: opportunity to forget: the four places that build a DeviceCommandClient would each have
#: had to remember to pass it, and any that did not would have been silently exempt from
#: enforcement. Same shape as ``register_broker_runtime``, for the same reason.
_MODE: str = GATE_MODE_SHADOW


def set_mode(mode: str) -> str:
    """Set the process-wide gate mode. An unknown mode falls back to shadow, loudly."""
    global _MODE
    if mode not in GATE_MODES:
        logger.warning("device_command_gate_unknown_mode", requested=mode, using=GATE_MODE_SHADOW)
        _MODE = GATE_MODE_SHADOW
    else:
        _MODE = mode
    return _MODE


def current_mode() -> str:
    return _MODE


def _conclude(capability: str, mode: str, reason: str | None) -> GateDecision:
    would_refuse = reason is not None
    return GateDecision(
        allowed=not (would_refuse and mode == GATE_MODE_ENFORCE),
        would_refuse=would_refuse,
        reason=reason,
        mode=mode,
        capability=capability,
    )


def evaluate(
    device: Device | None, capability: str, *, mode: str | None = None
) -> GateDecision:
    """Decide whether ``capability`` may be sent to ``device``. Never raises."""
    from app.broker.models import DEVICE_STATUS_REVOKED

    mode = mode if mode is not None else _MODE
    if mode not in GATE_MODES:  # a misconfigured mode must not silently disable the gate
        mode = GATE_MODE_SHADOW
    if device is None:
        return _conclude(capability, mode, REASON_UNKNOWN_DEVICE)
    if device.status == DEVICE_STATUS_REVOKED:
        return _conclude(capability, mode, REASON_DEVICE_REVOKED)
    advertised = list(device.capabilities_json or [])
    if not has_capability(advertised, capability):
        return _conclude(capability, mode, REASON_CAPABILITY_NOT_ADVERTISED)
    return _conclude(capability, mode, None)


def record(decision: GateDecision, *, device_id: object) -> None:
    """Log every refusal the gate reached, in either mode.

    The shadow-mode line is the entire product of shadow mode: without it the owner has
    nothing to read when deciding whether enforcement is safe.
    """
    if not decision.would_refuse:
        return
    logger.warning(
        "device_command_gate_refusal",
        device_id=str(device_id),
        capability=decision.capability,
        reason=decision.reason,
        mode=decision.mode,
        blocked=not decision.allowed,
    )


__all__ = [
    "GATE_MODES",
    "GATE_MODE_ENFORCE",
    "GATE_MODE_SHADOW",
    "REASON_CAPABILITY_NOT_ADVERTISED",
    "REASON_DEVICE_REVOKED",
    "REASON_UNKNOWN_DEVICE",
    "DeviceCommandRefused",
    "GateDecision",
    "current_mode",
    "evaluate",
    "record",
    "set_mode",
]
