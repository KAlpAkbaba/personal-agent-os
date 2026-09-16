"""Acting on a device's activity status (M18.3 spec §3.6).

``app.devices.status`` stores the report and says what CHANGED. This module is the durable
half: the presence observation, the ``owner.input_active`` ledger row and the input holdoff,
the ``display.on|off`` bus publish, and the reconciliation of an alarm the device rang on
its own. It lives in ``app.ambient`` rather than in ``app.devices`` because every one of
those is a consequence of ambient policy or of an alarm, and the devices layer's job ends
at "here is what the machine says".

Called from the broker's heartbeat path. Everything here is best effort in the strict
sense: a failure writes a log line and returns, because a device whose heartbeat handling
raised would be disconnected, and a disconnected device is one that cannot ring an alarm.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.ambient.holdoff import SOURCE_INPUT, HoldoffRegistry, get_holdoffs
from app.devices.status import (
    DISPLAY_OFF,
    DISPLAY_ON,
    DeviceStatusRegistry,
    StatusChange,
    get_status_registry,
)
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_OWNER_INPUT_ACTIVE,
    SEVERITY_INFO,
    SUBSYSTEM_AMBIENT,
)
from app.logging import get_logger
from app.uistate.contract import UiState
from app.uistate.publisher import publish

logger = get_logger("app.ambient.ingest")

#: Spec §3.6a: the presence observation an input-active status produces. ``person_present``
#: is the only thing keyboard activity actually proves, and it proves it well; the awake
#: state follows from that, and the confidence is high but not 1.0 because the observation
#: is about a MACHINE being used, which is very strong evidence about a person and not a
#: certainty about one.
INPUT_OBSERVATION_CONFIDENCE = 0.85
#: Below this idle reading the owner is actively typing/moving rather than merely present.
INPUT_HIGH_ACTIVITY_IDLE_S = 30.0


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What the status actually caused. Every field is a fact, not an attempt."""

    observed: bool = False
    input_active_recorded: bool = False
    holdoff_started: bool = False
    display_published: str | None = None
    reconciled_alarms: tuple[str, ...] = field(default_factory=tuple)
    #: B48: the device camera's derived observation entered the presence model.
    camera_observed: bool = False
    #: B48: why a camera observation was NOT used, when one arrived and was not.
    camera_refused: str | None = None
    #: B48: the camera mode this heartbeat caused to be sent to the device.
    camera_mode_sent: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "camera_observed": self.camera_observed,
            "camera_refused": self.camera_refused,
            "camera_mode_sent": self.camera_mode_sent,
            "observed": self.observed,
            "input_active_recorded": self.input_active_recorded,
            "holdoff_started": self.holdoff_started,
            "display_published": self.display_published,
            "reconciled_alarms": list(self.reconciled_alarms),
        }


def input_observation(change: StatusChange, *, now: datetime) -> dict[str, Any]:
    """The seven-field structured observation of spec §2, from an input idle counter.

    Nothing here is derived from a frame, an image or any content — the source is a tick
    count from ``GetLastInputInfo`` and this observation carries no more than that.
    """
    idle = change.status.input_idle_s or 0.0
    return {
        "person_present": True,
        "presence_confidence": INPUT_OBSERVATION_CONFIDENCE,
        "activity_level": "high" if idle <= INPUT_HIGH_ACTIVITY_IDLE_S else "low",
        "posture": "unknown",
        "awake_state": "awake",
        "observed_at": (change.status.observed_at or now).astimezone(UTC).isoformat(),
        "source": "input",
    }


def ingest_status(
    session: Session,
    device_id: uuid.UUID,
    raw: Any,
    *,
    statuses: DeviceStatusRegistry | None = None,
    holdoffs: HoldoffRegistry | None = None,
    now: datetime | None = None,
) -> IngestResult:
    """Record one heartbeat status and act on what changed (spec §3.6 a-e)."""
    moment = now or datetime.now(UTC)
    registry = statuses or get_status_registry()
    change = registry.record(device_id, raw, now=moment)
    if change is None:
        return IngestResult()

    observed = False
    input_recorded = False
    holdoff_started = False
    display_published: str | None = None

    # (a) A throttled presence observation while the owner is actually using the machine.
    # Long idle produces NOTHING: absence of input is not evidence of absence (spec §3.6a).
    if change.should_observe_input:
        try:
            from app.presence import service as presence_service

            presence_service.ingest_observation(
                session, input_observation(change, now=moment), now=moment
            )
            observed = True
        except Exception as exc:  # noqa: BLE001 - a heartbeat must never fail on this
            logger.warning(
                "input_observation_failed", device=str(device_id), error=type(exc).__name__
            )

    # (a') B48 (rows 326, 327): the device camera's DERIVED observation - never a frame -
    # through the same presence boundary as every other observation.
    camera_observed, camera_refused = _ingest_camera(session, device_id, change, now=moment)

    # (b) A real input reset: the ledger row AND the holdoff (spec §1.3, §3.6b).
    if change.input_reset:
        input_recorded = _record_input_active(session, change, now=moment)
        try:
            from app.ambient.service import get_policy

            seconds = get_policy(session).input_holdoff_s
        except Exception:  # noqa: BLE001 - the default is the safe one either way
            seconds = None
        (holdoffs or get_holdoffs()).start(
            SOURCE_INPUT, seconds=seconds, now=moment, reason="input_idle_reset"
        )
        holdoff_started = True

    # (c) The display's OBSERVED power state changed (spec §3.6c, §7).
    if change.display_changed and change.display_state in (DISPLAY_ON, DISPLAY_OFF):
        publish(
            UiState.DISPLAY_ON if change.display_state == DISPLAY_ON else UiState.DISPLAY_OFF,
            subsystem=SUBSYSTEM_AMBIENT,
            status=change.display_state,
            label=change.display_state,
            metadata={
                "device_id": str(device_id),
                "monitors": change.status.monitors,
                "ttl_s": 24 * 3600,
            },
        )
        display_published = change.display_state

    # (d) The device rang its own armed fallback while the cloud was unreachable.
    reconciled: tuple[str, ...] = ()
    if change.newly_fired_alarms:
        try:
            from app.alarms import service as alarms_service

            touched = alarms_service.reconcile_local_fired(
                session, list(change.newly_fired_alarms), now=moment
            )
            reconciled = tuple(str(a.id) for a in touched)
        except Exception as exc:  # noqa: BLE001 - see module docstring
            logger.warning(
                "local_alarm_reconcile_failed", device=str(device_id), error=type(exc).__name__
            )

    # (e) B48: the device camera follows the owner's mode (app.ambient.camera).
    camera_mode_sent: str | None = None
    try:
        from app.ambient import camera as ambient_camera

        camera_mode_sent = ambient_camera.reconcile(session, device_id, change.status, now=moment)
    except Exception as exc:  # noqa: BLE001 - see module docstring
        logger.warning("camera_reconcile_failed", device=str(device_id), error=type(exc).__name__)

    return IngestResult(
        observed=observed,
        input_active_recorded=input_recorded,
        holdoff_started=holdoff_started,
        display_published=display_published,
        reconciled_alarms=reconciled,
        camera_observed=camera_observed,
        camera_refused=camera_refused,
        camera_mode_sent=camera_mode_sent,
    )


#: Why a device camera observation was not used (``IngestResult.camera_refused``).
CAMERA_REFUSED_NOT_CAMERA = "not_a_camera_observation"
CAMERA_REFUSED_MODE_OFF = "owner_mode_off"
CAMERA_REFUSED_EYE_DISABLED = "eye_disabled"
CAMERA_REFUSED_INVALID = "invalid_observation"
CAMERA_REFUSED_ERROR = "ingest_failed"


def _ingest_camera(
    session: Session, device_id: uuid.UUID, change: StatusChange, *, now: datetime
) -> tuple[bool, str | None]:
    payload = change.new_camera_observation
    if payload is None:
        return False, None
    if payload.get("source") != "camera":
        # This path carries the camera and nothing else: a device cannot use it to speak for
        # the keyboard, the voice or a task.
        return False, CAMERA_REFUSED_NOT_CAMERA
    try:
        from app.ambient import camera as ambient_camera

        if ambient_camera.desired_mode(session) == "off":
            # The owner's "off" (or a disabled eye) outranks a reading that was already on
            # its way: consent withdrawn is consent withdrawn, even for the last frame.
            return False, CAMERA_REFUSED_MODE_OFF
    except Exception:  # noqa: BLE001 - an unreadable mode is "off" (desired_mode says so)
        return False, CAMERA_REFUSED_MODE_OFF
    try:
        from app.presence import service as presence_service
        from app.presence.observations import ObservationRejected

        try:
            presence_service.ingest_observation(session, payload, now=now)
        except presence_service.EyeDisabledError:
            return False, CAMERA_REFUSED_EYE_DISABLED
        except ObservationRejected:
            logger.warning("camera_observation_rejected", device=str(device_id))
            return False, CAMERA_REFUSED_INVALID
        return True, None
    except Exception as exc:  # noqa: BLE001 - a heartbeat must never fail on this
        logger.warning(
            "camera_observation_failed", device=str(device_id), error=type(exc).__name__
        )
        return False, CAMERA_REFUSED_ERROR


def _record_input_active(session: Session, change: StatusChange, *, now: datetime) -> bool:
    try:
        ledger_service.record(
            session,
            ledger_service.ActivityEvent(
                event_type=EVENT_TYPE_OWNER_INPUT_ACTIVE,
                subsystem=SUBSYSTEM_AMBIENT,
                action="owner_input_active",
                severity=SEVERITY_INFO,
                factual_summary="Sahip klavye/fare kullandı; ekran otomasyonu beklemede.",
                source="live",
                source_ref=f"ambient:input_active:{change.status.device_id}:{int(now.timestamp())}",
                occurred_at=now,
                detail_json={
                    "device_id": str(change.status.device_id),
                    "input_idle_s": change.status.input_idle_s,
                    "display_state": change.status.display_state,
                    "previous_idle_s": (
                        change.previous.input_idle_s if change.previous else None
                    ),
                },
            ),
        )
        return True
    except Exception as exc:  # noqa: BLE001 - the ledger is evidence, not a dependency
        logger.warning("owner_input_active_ledger_failed", error=type(exc).__name__)
        return False


def world_model_facts(*, statuses: DeviceStatusRegistry | None = None) -> list[dict[str, Any]]:
    """``device.display_state`` and ``device.input_idle_s`` per device (spec §3.6e).

    Returned as plain dicts rather than ``app.worldmodel.state.Fact`` objects so this module
    needs no import from the World Model — the collector there turns them into facts with
    its own ``RUNTIME`` truth kind, which is the honest one: these come from a live report,
    not from a record.
    """
    registry = statuses or get_status_registry()
    out: list[dict[str, Any]] = []
    for device_id, status in registry.all().items():
        out.append(
            {
                "key": f"device.display_state.{device_id}",
                "value": status.display_state,
                "observed_at": status.display_observed_at or status.observed_at,
                "device_id": str(device_id),
            }
        )
        if status.input_idle_s is not None:
            out.append(
                {
                    "key": f"device.input_idle_s.{device_id}",
                    "value": round(status.input_idle_s, 1),
                    "observed_at": status.observed_at,
                    "device_id": str(device_id),
                }
            )
    return out


__all__ = [
    "INPUT_HIGH_ACTIVITY_IDLE_S",
    "INPUT_OBSERVATION_CONFIDENCE",
    "IngestResult",
    "ingest_status",
    "input_observation",
    "world_model_facts",
]
