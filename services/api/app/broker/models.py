"""Device broker ORM models (M1).

Canonical schema lives in alembic/versions/20260831_0002_device_broker.py.
Types are chosen to be portable (generic Uuid, JSON with a JSONB variant on
PostgreSQL) so unit tests can exercise the service layer against SQLite while
integration tests run against the real PostgreSQL schema.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

# devices.status (stored)
DEVICE_STATUS_ENROLLED = "enrolled"
DEVICE_STATUS_REVOKED = "revoked"

# device_commands.status
COMMAND_STATUS_PENDING = "pending"
COMMAND_STATUS_DELIVERED = "delivered"
COMMAND_STATUS_ACCEPTED = "accepted"
COMMAND_STATUS_RUNNING = "running"
COMMAND_STATUS_SUCCEEDED = "succeeded"
COMMAND_STATUS_FAILED = "failed"
COMMAND_STATUS_EXPIRED = "expired"
COMMAND_STATUS_CANCELLED = "cancelled"

COMMAND_STATUSES = (
    COMMAND_STATUS_PENDING,
    COMMAND_STATUS_DELIVERED,
    COMMAND_STATUS_ACCEPTED,
    COMMAND_STATUS_RUNNING,
    COMMAND_STATUS_SUCCEEDED,
    COMMAND_STATUS_FAILED,
    COMMAND_STATUS_EXPIRED,
    COMMAND_STATUS_CANCELLED,
)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    public_key_spki_b64: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities_json: Mapped[list[Any]] = mapped_column(
        JSONColumn, nullable=False, default=list
    )
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DEVICE_STATUS_ENROLLED
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # M13/app.devices: owner-set aliases/labels/policy ({"aliases": [...],
    # "labels": [...], "policy": {...}}), never a machine literal in code.
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    # Refreshed from every hello (ws.py); last known agent build, distinct from
    # a single DeviceSession's software_version (which is per-connection history).
    software_version: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DeviceSession(Base):
    __tablename__ = "device_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    software_version: Mapped[str] = mapped_column(String(64), nullable=False)


class DeviceCommand(Base):
    __tablename__ = "device_commands"
    __table_args__ = (
        UniqueConstraint("device_id", "idempotency_key", name="uq_device_commands_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=COMMAND_STATUS_PENDING, index=True
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EnrollmentToken(Base):
    __tablename__ = "enrollment_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    command_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
