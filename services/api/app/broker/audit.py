"""Audit event persistence (DEVICE_PROTOCOL.md §7).

Every broker-relevant event (enrollment, session start/end, command created /
delivered / ack transitions / cancel / expiry, revocation) becomes an
audit_events row carrying trace_id / device_id / command_id. Metadata never
contains secrets and is capped at 4 KB serialized.
"""

import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.broker.models import AuditEvent

MAX_METADATA_BYTES = 4096

CATEGORY_DEVICE = "device"
CATEGORY_COMMAND = "device_command"
CATEGORY_SESSION = "device_session"


def _bounded_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = metadata or {}
    try:
        encoded = json.dumps(metadata, default=str)
    except (TypeError, ValueError):
        return {"metadata_error": "not JSON-serializable"}
    if len(encoded.encode("utf-8")) <= MAX_METADATA_BYTES:
        return metadata
    return {
        "truncated": True,
        "original_bytes": len(encoded.encode("utf-8")),
        "keys": sorted(str(k) for k in metadata)[:50],
    }


def record_audit_event(
    session: Session,
    *,
    category: str,
    action: str,
    subject_ref: str | None = None,
    device_id: uuid.UUID | None = None,
    command_id: uuid.UUID | None = None,
    trace_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add an audit row to the given session (caller owns the transaction)."""
    event = AuditEvent(
        category=category,
        action=action,
        subject_ref=subject_ref,
        device_id=device_id,
        command_id=command_id,
        trace_id=trace_id,
        metadata_json=_bounded_metadata(metadata),
    )
    session.add(event)
    return event
