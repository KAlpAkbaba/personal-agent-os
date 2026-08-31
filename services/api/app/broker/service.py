"""Broker persistence operations (synchronous, one transaction per call).

All functions take an open SQLAlchemy Session and commit before returning,
so callers (REST routes, the WS handler, the sweeper) run them via
`asyncio.to_thread` without sharing sessions across the event loop.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.broker import security
from app.broker.audit import (
    CATEGORY_COMMAND,
    CATEGORY_DEVICE,
    CATEGORY_SESSION,
    record_audit_event,
)
from app.broker.models import (
    COMMAND_STATUS_CANCELLED,
    COMMAND_STATUS_DELIVERED,
    COMMAND_STATUS_EXPIRED,
    COMMAND_STATUS_FAILED,
    COMMAND_STATUS_PENDING,
    COMMAND_STATUS_SUCCEEDED,
    DEVICE_STATUS_ENROLLED,
    DEVICE_STATUS_REVOKED,
    Device,
    DeviceCommand,
    DeviceSession,
    EnrollmentToken,
)
from app.broker.state import TERMINAL_STATUSES, TransitionDecision, classify_transition


def utcnow() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ enrollment


def issue_enrollment_token(
    session: Session, *, ttl_s: int, trace_id: str | None
) -> tuple[str, datetime]:
    token = security.generate_enrollment_token()
    expires_at = utcnow() + timedelta(seconds=ttl_s)
    session.add(
        EnrollmentToken(token_hash=security.hash_enrollment_token(token), expires_at=expires_at)
    )
    record_audit_event(
        session,
        category=CATEGORY_DEVICE,
        action="enrollment_token_issued",
        trace_id=trace_id,
        metadata={"ttl_s": ttl_s},
    )
    session.commit()
    return token, expires_at


def consume_enrollment_token(session: Session, token: str) -> bool:
    """Atomically mark the token used. False if unknown, expired or already used."""
    now = utcnow()
    result = session.execute(
        update(EnrollmentToken)
        .where(
            EnrollmentToken.token_hash == security.hash_enrollment_token(token),
            EnrollmentToken.used_at.is_(None),
            EnrollmentToken.expires_at > now,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    session.expire_all()
    return result.rowcount == 1


def enroll_device(
    session: Session,
    *,
    name: str,
    platform: str,
    public_key_spki_b64: str,
    capabilities: list[str],
    trace_id: str | None,
) -> Device:
    """Create an enrolled device. Raises ValueError for a bad public key."""
    security.load_p256_public_key(public_key_spki_b64)  # validate; raises ValueError
    device = Device(
        name=name,
        platform=platform,
        public_key_spki_b64=public_key_spki_b64,
        capabilities_json=capabilities,
        status=DEVICE_STATUS_ENROLLED,
    )
    session.add(device)
    session.flush()
    record_audit_event(
        session,
        category=CATEGORY_DEVICE,
        action="device_enrolled",
        subject_ref=f"device:{device.id}",
        device_id=device.id,
        trace_id=trace_id,
        metadata={"name": name, "platform": platform, "capabilities": capabilities},
    )
    session.commit()
    return device


def get_device(session: Session, device_id: uuid.UUID) -> Device | None:
    return session.get(Device, device_id)


def list_devices(session: Session) -> list[Device]:
    return list(session.execute(select(Device).order_by(Device.enrolled_at)).scalars())


def revoke_device(session: Session, device_id: uuid.UUID, *, trace_id: str | None) -> Device | None:
    device = session.get(Device, device_id)
    if device is None:
        return None
    if device.status != DEVICE_STATUS_REVOKED:
        device.status = DEVICE_STATUS_REVOKED
        device.revoked_at = utcnow()
        record_audit_event(
            session,
            category=CATEGORY_DEVICE,
            action="device_revoked",
            subject_ref=f"device:{device.id}",
            device_id=device.id,
            trace_id=trace_id,
        )
    session.commit()
    return device


def touch_last_seen(session: Session, device_id: uuid.UUID) -> None:
    session.execute(
        update(Device)
        .where(Device.id == device_id)
        .values(last_seen_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    session.commit()


# -------------------------------------------------------------------- sessions


def start_device_session(
    session: Session,
    *,
    device_id: uuid.UUID,
    software_version: str,
    connection_metadata: dict[str, Any],
    trace_id: str | None,
) -> DeviceSession:
    row = DeviceSession(
        device_id=device_id,
        software_version=software_version,
        connection_metadata_json=connection_metadata,
    )
    session.add(row)
    session.flush()
    session.execute(
        update(Device).where(Device.id == device_id).values(last_seen_at=utcnow())
    )
    record_audit_event(
        session,
        category=CATEGORY_SESSION,
        action="session_started",
        subject_ref=f"device_session:{row.id}",
        device_id=device_id,
        trace_id=trace_id,
        metadata={"software_version": software_version},
    )
    session.commit()
    return row


def end_device_session(
    session: Session,
    *,
    session_id: uuid.UUID,
    device_id: uuid.UUID,
    reason: str,
    trace_id: str | None,
) -> None:
    now = utcnow()
    result = session.execute(
        update(DeviceSession)
        .where(DeviceSession.id == session_id, DeviceSession.ended_at.is_(None))
        .values(ended_at=now)
        .execution_options(synchronize_session=False)
    )
    session.execute(
        update(Device)
        .where(Device.id == device_id)
        .values(last_seen_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        record_audit_event(
            session,
            category=CATEGORY_SESSION,
            action="session_ended",
            subject_ref=f"device_session:{session_id}",
            device_id=device_id,
            trace_id=trace_id,
            metadata={"reason": reason},
        )
    session.commit()
    session.expire_all()


def end_orphan_sessions(session: Session) -> int:
    """Close sessions left open by a previous broker process (crash/restart)."""
    result = session.execute(
        update(DeviceSession)
        .where(DeviceSession.ended_at.is_(None))
        .values(ended_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    session.commit()
    session.expire_all()
    return result.rowcount or 0


# -------------------------------------------------------------------- commands


def create_command(
    session: Session,
    *,
    device_id: uuid.UUID,
    capability: str,
    payload: dict[str, Any],
    idempotency_key: str,
    timeout_s: float,
    trace_id: str,
) -> tuple[DeviceCommand, bool]:
    """Durably insert a command; dedup on (device_id, idempotency_key).

    Returns (command, created). On dedup, the existing command is returned
    unchanged and no audit row is added.
    """
    existing = session.execute(
        select(DeviceCommand).where(
            DeviceCommand.device_id == device_id,
            DeviceCommand.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    command = DeviceCommand(
        device_id=device_id,
        capability=capability,
        payload_json=payload,
        idempotency_key=idempotency_key,
        status=COMMAND_STATUS_PENDING,
        trace_id=trace_id,
        expires_at=utcnow() + timedelta(seconds=timeout_s),
    )
    session.add(command)
    try:
        session.flush()
    except IntegrityError:
        # Lost a creation race: return the winner.
        session.rollback()
        winner = session.execute(
            select(DeviceCommand).where(
                DeviceCommand.device_id == device_id,
                DeviceCommand.idempotency_key == idempotency_key,
            )
        ).scalar_one()
        return winner, False
    record_audit_event(
        session,
        category=CATEGORY_COMMAND,
        action="command_created",
        subject_ref=f"device_command:{command.id}",
        device_id=device_id,
        command_id=command.id,
        trace_id=trace_id,
        metadata={"capability": capability, "idempotency_key": idempotency_key},
    )
    session.commit()
    return command, True


def get_command(
    session: Session, device_id: uuid.UUID, command_id: uuid.UUID
) -> DeviceCommand | None:
    command = session.get(DeviceCommand, command_id)
    if command is None or command.device_id != device_id:
        return None
    return command


def deliverable_commands(session: Session, device_id: uuid.UUID) -> list[DeviceCommand]:
    """All non-terminal, non-expired commands for (re)delivery on connect."""
    return list(
        session.execute(
            select(DeviceCommand)
            .where(
                DeviceCommand.device_id == device_id,
                DeviceCommand.status.not_in(TERMINAL_STATUSES),
                DeviceCommand.expires_at > utcnow(),
            )
            .order_by(DeviceCommand.created_at)
        ).scalars()
    )


def mark_command_delivered(session: Session, command_id: uuid.UUID) -> DeviceCommand | None:
    """Record delivery: pending -> delivered (never regresses a later status)."""
    command = session.get(DeviceCommand, command_id)
    if command is None:
        return None
    changed = False
    if command.delivered_at is None:
        command.delivered_at = utcnow()
        changed = True
    if command.status == COMMAND_STATUS_PENDING:
        command.status = COMMAND_STATUS_DELIVERED
        changed = True
    if changed:
        record_audit_event(
            session,
            category=CATEGORY_COMMAND,
            action="command_delivered",
            subject_ref=f"device_command:{command.id}",
            device_id=command.device_id,
            command_id=command.id,
            trace_id=command.trace_id,
        )
    session.commit()
    return command


def apply_command_ack(
    session: Session,
    *,
    device_id: uuid.UUID,
    command_id: uuid.UUID,
    ack_status: str,
    result: dict[str, Any] | None,
    error_class: str | None,
    error_message: str | None,
) -> tuple[TransitionDecision | None, DeviceCommand | None]:
    """Process a command_ack. Returns (decision, command).

    decision None means the command does not exist for this device.
    """
    command = get_command(session, device_id, command_id)
    if command is None:
        return None, None
    decision = classify_transition(command.status, ack_status)
    if decision is TransitionDecision.APPLY:
        command.status = ack_status
        if ack_status in (COMMAND_STATUS_SUCCEEDED, COMMAND_STATUS_FAILED):
            command.terminal_at = utcnow()
            command.result_json = result
            command.error_class = error_class
            command.error_message = error_message
        record_audit_event(
            session,
            category=CATEGORY_COMMAND,
            action=f"command_ack_{ack_status}",
            subject_ref=f"device_command:{command.id}",
            device_id=device_id,
            command_id=command.id,
            trace_id=command.trace_id,
            metadata={"error_class": error_class} if error_class else None,
        )
        session.commit()
    return decision, command


def cancel_command(
    session: Session,
    *,
    device_id: uuid.UUID,
    command_id: uuid.UUID,
    trace_id: str | None,
) -> tuple[str, DeviceCommand | None]:
    """REST cancel. Returns (outcome, command) with outcome one of:

    - "not_found"
    - "already_terminal": no-op
    - "cancelled": was still undelivered; terminally cancelled server-side
    - "forward": delivered/in-flight; caller should forward a cancel frame
    """
    command = get_command(session, device_id, command_id)
    if command is None:
        return "not_found", None
    if command.status in TERMINAL_STATUSES:
        return "already_terminal", command
    record_audit_event(
        session,
        category=CATEGORY_COMMAND,
        action="command_cancel_requested",
        subject_ref=f"device_command:{command.id}",
        device_id=device_id,
        command_id=command.id,
        trace_id=trace_id or command.trace_id,
    )
    if command.status == COMMAND_STATUS_PENDING:
        command.status = COMMAND_STATUS_CANCELLED
        command.terminal_at = utcnow()
        command.error_class = "cancelled"
        command.error_message = "cancelled by owner before delivery"
        record_audit_event(
            session,
            category=CATEGORY_COMMAND,
            action="command_cancelled",
            subject_ref=f"device_command:{command.id}",
            device_id=device_id,
            command_id=command.id,
            trace_id=trace_id or command.trace_id,
        )
        session.commit()
        return "cancelled", command
    session.commit()
    return "forward", command


def expire_due_commands(session: Session) -> list[DeviceCommand]:
    """Mark pending/delivered commands past expires_at as expired (audited)."""
    now = utcnow()
    due = list(
        session.execute(
            select(DeviceCommand).where(
                DeviceCommand.status.in_([COMMAND_STATUS_PENDING, COMMAND_STATUS_DELIVERED]),
                DeviceCommand.expires_at <= now,
            )
        ).scalars()
    )
    for command in due:
        command.status = COMMAND_STATUS_EXPIRED
        command.terminal_at = now
        command.error_class = "command_expired"
        command.error_message = "expired before completion"
        record_audit_event(
            session,
            category=CATEGORY_COMMAND,
            action="command_expired",
            subject_ref=f"device_command:{command.id}",
            device_id=command.device_id,
            command_id=command.id,
            trace_id=command.trace_id,
        )
    if due:
        session.commit()
    return due
