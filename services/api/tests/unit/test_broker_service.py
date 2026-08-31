"""Unit tests: broker service layer against in-memory SQLite.

Covers enrollment token single-use, idempotent command creation, expiry
sweep logic, ack application and cancellation semantics, plus the audit
metadata bound.
"""

import base64
import json
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.broker import service
from app.broker.audit import MAX_METADATA_BYTES, _bounded_metadata
from app.broker.models import (
    AuditEvent,
    Device,
    DeviceCommand,
    DeviceSession,
    EnrollmentToken,
)
from app.broker.state import TransitionDecision

BROKER_TABLES = [
    Device.__table__,
    DeviceSession.__table__,
    DeviceCommand.__table__,
    EnrollmentToken.__table__,
    AuditEvent.__table__,
]


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in BROKER_TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


def make_spki_b64() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


def enroll(db: Session) -> Device:
    return service.enroll_device(
        db,
        name="test-pc",
        platform="windows",
        public_key_spki_b64=make_spki_b64(),
        capabilities=["desktop.open_application"],
        trace_id="trace-enroll",
    )


def audit_actions(db: Session) -> list[str]:
    return [row.action for row in db.execute(select(AuditEvent)).scalars()]


# ------------------------------------------------------------------ enrollment


def test_enrollment_token_single_use(db: Session) -> None:
    token, expires_at = service.issue_enrollment_token(db, ttl_s=900, trace_id="t1")
    stored = db.execute(select(EnrollmentToken)).scalar_one()
    assert stored.token_hash != token  # stored hashed, never in clear
    assert len(stored.token_hash) == 64

    assert service.consume_enrollment_token(db, token) is True
    assert service.consume_enrollment_token(db, token) is False  # single-use


def test_expired_enrollment_token_rejected(db: Session) -> None:
    token, _ = service.issue_enrollment_token(db, ttl_s=-1, trace_id="t1")
    assert service.consume_enrollment_token(db, token) is False


def test_unknown_enrollment_token_rejected(db: Session) -> None:
    assert service.consume_enrollment_token(db, "never-issued-token") is False


def test_enroll_device_validates_key_and_audits(db: Session) -> None:
    device = enroll(db)
    assert device.status == "enrolled"
    assert "device_enrolled" in audit_actions(db)
    with pytest.raises(ValueError):
        service.enroll_device(
            db,
            name="bad",
            platform="windows",
            public_key_spki_b64="bm90IGEga2V5",
            capabilities=[],
            trace_id=None,
        )


# -------------------------------------------------------------------- commands


def test_idempotent_command_creation(db: Session) -> None:
    device = enroll(db)
    cmd1, created1 = service.create_command(
        db,
        device_id=device.id,
        capability="desktop.open_application",
        payload={"application": "notepad"},
        idempotency_key="key-12345678",
        timeout_s=300,
        trace_id="trace-a",
    )
    cmd2, created2 = service.create_command(
        db,
        device_id=device.id,
        capability="desktop.open_application",
        payload={"application": "notepad"},
        idempotency_key="key-12345678",
        timeout_s=300,
        trace_id="trace-b",
    )
    assert created1 is True and created2 is False
    assert cmd1.id == cmd2.id
    assert cmd2.trace_id == "trace-a"  # original creation wins
    assert audit_actions(db).count("command_created") == 1

    cmd3, created3 = service.create_command(
        db,
        device_id=device.id,
        capability="desktop.open_application",
        payload={"application": "calc"},
        idempotency_key="key-87654321",
        timeout_s=300,
        trace_id="trace-c",
    )
    assert created3 is True and cmd3.id != cmd1.id


def test_ack_lifecycle_persists_result_and_audits(db: Session) -> None:
    device = enroll(db)
    command, _ = service.create_command(
        db,
        device_id=device.id,
        capability="desktop.open_application",
        payload={"application": "notepad"},
        idempotency_key="key-12345678",
        timeout_s=300,
        trace_id="trace-a",
    )
    service.mark_command_delivered(db, command.id)
    for status in ("accepted", "running"):
        decision, _ = service.apply_command_ack(
            db,
            device_id=device.id,
            command_id=command.id,
            ack_status=status,
            result=None,
            error_class=None,
            error_message=None,
        )
        assert decision is TransitionDecision.APPLY
    decision, updated = service.apply_command_ack(
        db,
        device_id=device.id,
        command_id=command.id,
        ack_status="succeeded",
        result={"pid": 1234},
        error_class=None,
        error_message=None,
    )
    assert decision is TransitionDecision.APPLY
    assert updated is not None
    assert updated.status == "succeeded"
    assert updated.result_json == {"pid": 1234}
    assert updated.terminal_at is not None

    # duplicate terminal re-ack: tolerated, no error, no state change
    decision, _ = service.apply_command_ack(
        db,
        device_id=device.id,
        command_id=command.id,
        ack_status="succeeded",
        result={"pid": 1234},
        error_class=None,
        error_message=None,
    )
    assert decision is TransitionDecision.IGNORE_DUPLICATE

    actions = audit_actions(db)
    for expected in (
        "command_created",
        "command_delivered",
        "command_ack_accepted",
        "command_ack_running",
        "command_ack_succeeded",
    ):
        assert actions.count(expected) == 1, expected


def test_ack_for_wrong_device_is_unknown(db: Session) -> None:
    device_a = enroll(db)
    device_b = enroll(db)
    command, _ = service.create_command(
        db,
        device_id=device_a.id,
        capability="desktop.open_application",
        payload={},
        idempotency_key="key-12345678",
        timeout_s=300,
        trace_id="t",
    )
    decision, found = service.apply_command_ack(
        db,
        device_id=device_b.id,
        command_id=command.id,
        ack_status="accepted",
        result=None,
        error_class=None,
        error_message=None,
    )
    assert decision is None and found is None


# ---------------------------------------------------------------------- expiry


def test_expiry_sweep_marks_pending_and_delivered_only(db: Session) -> None:
    device = enroll(db)

    def make(key: str, timeout_s: float) -> DeviceCommand:
        command, _ = service.create_command(
            db,
            device_id=device.id,
            capability="desktop.open_application",
            payload={},
            idempotency_key=key,
            timeout_s=timeout_s,
            trace_id="t",
        )
        return command

    past_pending = make("key-pending1", -5)
    past_delivered = make("key-delivered", -5)
    service.mark_command_delivered(db, past_delivered.id)
    future_pending = make("key-future12", 3600)
    past_running = make("key-running1", -5)
    service.mark_command_delivered(db, past_running.id)
    service.apply_command_ack(
        db,
        device_id=device.id,
        command_id=past_running.id,
        ack_status="running",
        result=None,
        error_class=None,
        error_message=None,
    )

    expired = service.expire_due_commands(db)
    expired_ids = {c.id for c in expired}
    assert past_pending.id in expired_ids
    assert past_delivered.id in expired_ids
    assert future_pending.id not in expired_ids
    assert past_running.id not in expired_ids  # in-flight, not swept

    refreshed = db.get(DeviceCommand, past_pending.id)
    assert refreshed is not None
    assert refreshed.status == "expired"
    assert refreshed.error_class == "command_expired"
    assert refreshed.terminal_at is not None
    assert audit_actions(db).count("command_expired") == 2

    # sweep is idempotent
    assert service.expire_due_commands(db) == []


def test_deliverable_commands_excludes_terminal_and_expired(db: Session) -> None:
    device = enroll(db)
    fresh, _ = service.create_command(
        db,
        device_id=device.id,
        capability="desktop.open_application",
        payload={},
        idempotency_key="key-fresh123",
        timeout_s=3600,
        trace_id="t",
    )
    stale, _ = service.create_command(
        db,
        device_id=device.id,
        capability="desktop.open_application",
        payload={},
        idempotency_key="key-stale123",
        timeout_s=-5,
        trace_id="t",
    )
    deliverable = service.deliverable_commands(db, device.id)
    ids = {c.id for c in deliverable}
    assert fresh.id in ids
    assert stale.id not in ids


# ---------------------------------------------------------------------- cancel


def test_cancel_pending_command_is_terminal(db: Session) -> None:
    device = enroll(db)
    command, _ = service.create_command(
        db,
        device_id=device.id,
        capability="desktop.open_application",
        payload={},
        idempotency_key="key-cancel12",
        timeout_s=3600,
        trace_id="t",
    )
    outcome, cancelled = service.cancel_command(
        db, device_id=device.id, command_id=command.id, trace_id="t"
    )
    assert outcome == "cancelled"
    assert cancelled is not None and cancelled.status == "cancelled"
    assert cancelled.error_class == "cancelled"
    assert "command_cancelled" in audit_actions(db)

    outcome2, _ = service.cancel_command(
        db, device_id=device.id, command_id=command.id, trace_id="t"
    )
    assert outcome2 == "already_terminal"


def test_cancel_delivered_command_requests_forward(db: Session) -> None:
    device = enroll(db)
    command, _ = service.create_command(
        db,
        device_id=device.id,
        capability="desktop.open_application",
        payload={},
        idempotency_key="key-cancel34",
        timeout_s=3600,
        trace_id="t",
    )
    service.mark_command_delivered(db, command.id)
    outcome, still = service.cancel_command(
        db, device_id=device.id, command_id=command.id, trace_id="t"
    )
    assert outcome == "forward"
    assert still is not None and still.status == "delivered"


# ------------------------------------------------------------------- revocation


def test_revoke_device(db: Session) -> None:
    device = enroll(db)
    revoked = service.revoke_device(db, device.id, trace_id="t")
    assert revoked is not None
    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None
    assert "device_revoked" in audit_actions(db)
    # idempotent
    again = service.revoke_device(db, device.id, trace_id="t")
    assert again is not None and again.status == "revoked"
    assert audit_actions(db).count("device_revoked") == 1


# ----------------------------------------------------------------------- audit


def test_audit_metadata_capped_at_4kb() -> None:
    small = {"k": "v"}
    assert _bounded_metadata(small) == small
    huge = {"blob": "x" * (MAX_METADATA_BYTES * 2)}
    bounded = _bounded_metadata(huge)
    assert bounded["truncated"] is True
    assert len(json.dumps(bounded).encode()) <= MAX_METADATA_BYTES
    assert _bounded_metadata({"bad": uuid.uuid4()})  # serializable via default=str


def test_orphan_session_cleanup(db: Session) -> None:
    device = enroll(db)
    row = service.start_device_session(
        db,
        device_id=device.id,
        software_version="0.1.0",
        connection_metadata={},
        trace_id=None,
    )
    assert row.ended_at is None
    assert service.end_orphan_sessions(db) == 1
    refreshed = db.get(DeviceSession, row.id)
    assert refreshed is not None and refreshed.ended_at is not None
    assert service.end_orphan_sessions(db) == 0
