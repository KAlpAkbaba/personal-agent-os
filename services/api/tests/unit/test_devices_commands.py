"""app.devices.commands.DeviceCommandClient: create + poll a device command."""

import base64
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.broker import service as broker_service
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.devices.commands import (
    CommandExpired,
    CommandFailed,
    CommandSucceeded,
    DeviceCommandClient,
    get_broker_runtime,
    register_broker_runtime,
)

BROKER_TABLES = [Device.__table__, DeviceSession.__table__, DeviceCommand.__table__,
                 EnrollmentToken.__table__, AuditEvent.__table__]


@pytest.fixture()
def factory():
    engine = create_engine("sqlite://")
    for table in BROKER_TABLES:
        table.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


def _enroll(factory) -> Device:
    with factory() as db:
        return broker_service.enroll_device(
            db, name="pc", platform="windows", public_key_spki_b64=_spki(),
            capabilities=["browser.chrome"], trace_id=None,
        )


def test_run_returns_succeeded_for_an_already_terminal_command(factory) -> None:
    device = _enroll(factory)
    with factory() as db:
        command, _ = broker_service.create_command(
            db, device_id=device.id, capability="browser.fetch_evidence", payload={"url": "x"},
            idempotency_key="k-succeed", timeout_s=60, trace_id="t",
        )
        broker_service.apply_command_ack(
            db, device_id=device.id, command_id=command.id, ack_status="succeeded",
            result={"excerpt": "hello"}, error_class=None, error_message=None,
        )

    client = DeviceCommandClient(factory, poll_interval_s=0.01)
    outcome = client.run(
        device_id=device.id, capability="browser.fetch_evidence", payload={"url": "x"},
        idempotency_key="k-succeed", timeout_s=60, trace_id="t",
    )
    assert isinstance(outcome, CommandSucceeded)
    assert outcome.result == {"excerpt": "hello"}


def test_run_returns_failed_with_error_class_and_retryable(factory) -> None:
    device = _enroll(factory)
    with factory() as db:
        command, _ = broker_service.create_command(
            db, device_id=device.id, capability="browser.fetch_evidence", payload={},
            idempotency_key="k-fail", timeout_s=60, trace_id="t",
        )
        broker_service.apply_command_ack(
            db, device_id=device.id, command_id=command.id, ack_status="failed",
            result=None, error_class="timeout", error_message="navigation timed out",
        )

    client = DeviceCommandClient(factory, poll_interval_s=0.01)
    outcome = client.run(
        device_id=device.id, capability="browser.fetch_evidence", payload={},
        idempotency_key="k-fail", timeout_s=60, trace_id="t",
    )
    assert isinstance(outcome, CommandFailed)
    assert outcome.error_class == "timeout"
    assert outcome.retryable is True


def test_run_non_retryable_error_class(factory) -> None:
    device = _enroll(factory)
    with factory() as db:
        command, _ = broker_service.create_command(
            db, device_id=device.id, capability="browser.fetch_evidence", payload={},
            idempotency_key="k-scope", timeout_s=60, trace_id="t",
        )
        broker_service.apply_command_ack(
            db, device_id=device.id, command_id=command.id, ack_status="failed",
            result=None, error_class="security_scope_error", error_message="risk class refused",
        )

    client = DeviceCommandClient(factory, poll_interval_s=0.01)
    outcome = client.run(
        device_id=device.id, capability="browser.fetch_evidence", payload={},
        idempotency_key="k-scope", timeout_s=60, trace_id="t",
    )
    assert isinstance(outcome, CommandFailed)
    assert outcome.retryable is False


def test_run_expires_when_never_delivered_within_timeout(factory) -> None:
    device = _enroll(factory)
    client = DeviceCommandClient(factory, poll_interval_s=0.01)
    started = time.monotonic()
    outcome = client.run(
        device_id=device.id, capability="browser.fetch_evidence", payload={},
        idempotency_key="k-expire", timeout_s=0.05, trace_id="t",
    )
    elapsed = time.monotonic() - started
    assert isinstance(outcome, CommandExpired)
    assert elapsed < 5  # never hangs beyond the command's own timeout


def test_run_calls_heartbeat_while_polling(factory) -> None:
    device = _enroll(factory)
    client = DeviceCommandClient(factory, poll_interval_s=0.01)
    calls = []
    client.run(
        device_id=device.id, capability="browser.fetch_evidence", payload={},
        idempotency_key="k-heartbeat", timeout_s=0.05, trace_id="t",
        heartbeat=lambda: calls.append(1),
    )
    assert calls  # heartbeat was invoked at least once during the poll


def test_run_is_idempotent_on_key_no_duplicate_command_row(factory) -> None:
    device = _enroll(factory)
    with factory() as db:
        command, _ = broker_service.create_command(
            db, device_id=device.id, capability="browser.fetch_evidence", payload={},
            idempotency_key="k-dup", timeout_s=60, trace_id="t",
        )
        broker_service.apply_command_ack(
            db, device_id=device.id, command_id=command.id, ack_status="succeeded",
            result={"n": 1}, error_class=None, error_message=None,
        )

    client = DeviceCommandClient(factory, poll_interval_s=0.01)
    first = client.run(
        device_id=device.id, capability="browser.fetch_evidence", payload={},
        idempotency_key="k-dup", timeout_s=60, trace_id="t",
    )
    second = client.run(
        device_id=device.id, capability="browser.fetch_evidence", payload={},
        idempotency_key="k-dup", timeout_s=60, trace_id="t",
    )
    assert first == second
    with factory() as db:
        from sqlalchemy import select as sa_select

        rows = db.execute(
            sa_select(DeviceCommand).where(DeviceCommand.idempotency_key == "k-dup")
        ).scalars().all()
        assert len(rows) == 1


def test_broker_runtime_registry_roundtrip() -> None:
    assert get_broker_runtime() is None
    sentinel = object()
    register_broker_runtime(sentinel)  # type: ignore[arg-type]
    try:
        assert get_broker_runtime() is sentinel
    finally:
        register_broker_runtime(None)
    assert get_broker_runtime() is None
