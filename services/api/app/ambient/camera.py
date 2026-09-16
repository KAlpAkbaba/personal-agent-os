"""The owner's device-camera mode, relayed to the devices (B48, DEVICE_PROTOCOL.md §6o).

The camera runs on the owner's machine, in the owner's session. Cloud Core never sees a
frame; what it holds is the owner's CHOICE (``ambient_policy.camera_mode``) and what each
device says its camera is doing (the heartbeat's ``camera`` block). This module keeps the two
in step:

* **What the device should be doing** is the owner's mode - but only while the Active Eye is
  enabled. "Gözünü kapat" / "Kamerayı kapat" therefore closes the device camera too, not
  only the server's intake; and "Gözü aç" opens nothing unless the owner has also chosen a
  mode. A read failure answers ``off``: no perception is the safe default, never the reverse.
* **When they differ**, ``desktop.camera_mode`` is sent - at most once a minute per device and
  mode, as a durable command row the broker delivers. The device's next heartbeat is the
  read-back.
* **The owner's own veto on the device outranks the cloud.** A camera the owner closed from
  the tray reports ``vetoed``; nothing here tries to reopen it.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy.orm import Session

from app.ambient.policy import CAMERA_MODE_OFF, CAMERA_MODES
from app.devices.status import DeviceStatus, DeviceStatusRegistry, get_status_registry
from app.logging import get_logger
from app.routines.dispatch import CAPABILITY_DESKTOP_CAMERA_MODE

logger = get_logger("app.ambient.camera")

CAPABILITY: Final[str] = CAPABILITY_DESKTOP_CAMERA_MODE
#: How often the same mode may be re-sent to the same device.
RESEND_AFTER_S: Final = 60.0
#: A command the device has not picked up in this long has expired; the next mismatch resends.
COMMAND_TIMEOUT_S: Final = 60.0
STATE_VETOED: Final = "vetoed"

Sender = Callable[[Session, uuid.UUID, str, datetime], None]

_lock = threading.Lock()
_last_sent: dict[uuid.UUID, tuple[str, datetime]] = {}


def desired_mode(session: Session) -> str:
    """The mode every camera-capable device should be in right now (module docstring)."""
    try:
        from app.ambient.service import get_policy
        from app.presence.eye import is_eye_enabled

        mode = get_policy(session).camera_mode
        if mode not in CAMERA_MODES:
            return CAMERA_MODE_OFF
        if mode != CAMERA_MODE_OFF and not is_eye_enabled(session):
            return CAMERA_MODE_OFF
        return mode
    except Exception as exc:  # noqa: BLE001 - see docstring: unknown means off
        logger.warning("camera_desired_mode_unreadable", error=type(exc).__name__)
        return CAMERA_MODE_OFF


def _send_command(session: Session, device_id: uuid.UUID, mode: str, now: datetime) -> None:
    from app.broker import service as broker_service

    bucket = int(now.timestamp() // RESEND_AFTER_S)
    broker_service.create_command(
        session,
        device_id=device_id,
        capability=CAPABILITY,
        payload={"mode": mode, "reason": "owner_policy"},
        idempotency_key=f"camera-mode:{device_id}:{mode}:{bucket}",
        timeout_s=COMMAND_TIMEOUT_S,
        trace_id=f"camera-mode-{uuid.uuid4().hex[:16]}",
    )


def reconcile(
    session: Session,
    device_id: uuid.UUID,
    status: DeviceStatus,
    *,
    now: datetime | None = None,
    send: Sender | None = None,
    force: bool = False,
    desired: str | None = None,
) -> str | None:
    """Send ``desktop.camera_mode`` when this device's camera is not in the owner's mode.

    Returns the mode sent, or None. Never raises: a heartbeat must not fail on this.
    """
    camera = status.camera
    if camera is None:
        # A device with no camera path: nothing to relay, and nothing to ask it.
        return None
    moment = now or datetime.now(UTC)
    wanted = desired if desired is not None else desired_mode(session)
    reported = camera.get("mode")
    if reported == wanted:
        return None
    if camera.get("state") == STATE_VETOED and wanted != CAMERA_MODE_OFF:
        # The owner closed the camera on the device itself; that outranks this relay.
        return None
    with _lock:
        last = _last_sent.get(device_id)
        if (
            not force
            and last is not None
            and last[0] == wanted
            and (moment - last[1]).total_seconds() < RESEND_AFTER_S
        ):
            return None
        _last_sent[device_id] = (wanted, moment)
    try:
        (send or _send_command)(session, device_id, wanted, moment)
    except Exception as exc:  # noqa: BLE001 - see docstring
        # The caller's session is the heartbeat's (or the policy write's): a half-written
        # command must not leave it unusable for what the caller does next.
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning("camera_mode_send_failed", device=str(device_id), error=type(exc).__name__)
        with _lock:
            _last_sent.pop(device_id, None)
        return None
    logger.info("camera_mode_sent", device=str(device_id), mode=wanted, reported=reported)
    return wanted


def push_now(
    session: Session,
    *,
    now: datetime | None = None,
    statuses: DeviceStatusRegistry | None = None,
    send: Sender | None = None,
) -> dict[str, str]:
    """The owner just changed the mode, or the eye flag: tell every camera-capable device
    now, skipping the resend throttle. Returns device id -> mode sent."""
    registry = statuses or get_status_registry()
    wanted = desired_mode(session)
    sent: dict[str, str] = {}
    for device_id, status in registry.all().items():
        mode = reconcile(session, device_id, status, now=now, send=send, force=True, desired=wanted)
        if mode is not None:
            sent[str(device_id)] = mode
    return sent


def reset() -> None:
    """Tests only: forget what was sent."""
    with _lock:
        _last_sent.clear()


def summary(status: DeviceStatus | None) -> dict[str, Any] | None:
    """The camera block of one device, for an explanation."""
    return dict(status.camera) if status is not None and status.camera is not None else None


__all__ = [
    "CAPABILITY",
    "COMMAND_TIMEOUT_S",
    "RESEND_AFTER_S",
    "desired_mode",
    "push_now",
    "reconcile",
    "reset",
    "summary",
]
