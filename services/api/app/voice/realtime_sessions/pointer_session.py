"""ADR-0199 stage 2: the pointer-streaming session record and its ONE ending.

``operator.pointer_session`` (``app.voice.realtime_sessions.tools_operator``) opens the
session and mints the record this module shapes; the pointer WebSocket
(``app.voice.realtime_sessions.pointer_ws``) reads it once, at connect, to check the
single-use token. Both ends of "end" - the tool's own ``{"action":"end"}``, and the
WebSocket's three own endings (the client's ``end`` frame, the socket closing, 60s of
silence) - write the SAME receipt shape through :func:`end_receipt`, so a reader of the
ledger never has to ask which path closed a given stream.

**The record replaces itself, whole, on every write.** ADR-0196 already found the bug
this guards against, one milestone earlier, for a different nested structure: a plain
JSON column (``RealtimeSessionRow.context_json``) compares the value SQLAlchemy is about
to write against the value it loaded, and ``handle_tool_call`` hands a tool a *shallow*
copy of that column (``dict(row.context_json or {})``) - the outer dict is fresh, but a
nested dict inside it is the SAME object the row's own committed state points at. Mutate
that nested object in place (``record["used"] = True``) and the "old" value SQLAlchemy
compares against has already changed too, so the write is silently a no-op. Every
function here that changes the record therefore returns/stores a NEW dict
(``{**record, "used": True}``, never ``record["used"] = True``).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy.orm import Session

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_FAILED,
    TERMINAL_UNVERIFIED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.ledger.vocabulary import SUBSYSTEM_OPERATOR
from app.operator.capabilities import CAPABILITY_POINTER_SESSION
from app.routines.dispatch import DeviceActionPort

#: ``RealtimeSessionRow.context_json``'s key for the pointer-session record.
CONTEXT_KEY: Final = "pointer_session"

#: ADR-0199: "stream_token (random, single-use, bound to the session, expires in 5
#: min)". 24 raw bytes -> a 32-character urlsafe token (``secrets.token_urlsafe``'s own
#: ratio), matching the ADR's "32 random bytes" reading of the wire token's size.
STREAM_TOKEN_BYTES: Final = 24
STREAM_EXPIRES_S: Final = 300.0
#: "60 s of silence ends it too" - the pointer WebSocket's own read timeout.
SILENCE_TIMEOUT_S: Final = 60.0
#: "Bounded to ~30 frames/s per socket" (inbound, browser -> Cloud Core).
MAX_CLIENT_FRAMES_PER_S: Final = 30
#: A move's |dx|/|dy| bound the wire frame itself enforces (ADR-0199's frame shapes).
MAX_MOVE_DELTA: Final = 200

SPEECH_OPENED: Final = "Fare akışını açtım efendim."
SPEECH_OPEN_FAILED: Final = "Fare akışını açamadım efendim."
SPEECH_CLOSED: Final = "Fare akışını kapattım efendim."
SPEECH_CLOSED_UNCONFIRMED: Final = "Fare akışını kapattım efendim; cihaz onaylamadı."
SPEECH_NOTHING_OPEN: Final = "Açık bir fare akışı yok efendim."


def new_stream_token() -> str:
    return secrets.token_urlsafe(STREAM_TOKEN_BYTES)


def new_session_id() -> str:
    """The id THIS pointer session is known by - to the device (``pointer.stream_begin``'s
    ``session``), to the WebSocket's ``hello`` (implicitly, via the stream token it binds
    to), and to the outbound ``pointer_stream`` frame. Deliberately not the realtime
    session id: a fresh one every ``begin`` keeps a stale stream from ever being
    mistaken for the current one."""
    return uuid.uuid4().hex


def build_record(
    *, session: str, window_id: str, stream_token: str, started_at: datetime
) -> dict[str, Any]:
    started = started_at if started_at.tzinfo is not None else started_at.replace(tzinfo=UTC)
    expires_at = started + timedelta(seconds=STREAM_EXPIRES_S)
    return {
        "session": session,
        "window_id": window_id,
        "stream_token": stream_token,
        "used": False,
        "started_at": started.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def record_from_context(context: dict[str, Any]) -> dict[str, Any] | None:
    record = context.get(CONTEXT_KEY)
    return dict(record) if isinstance(record, dict) else None


def mark_used(context: dict[str, Any], record: dict[str, Any]) -> None:
    """Single-use: good for exactly one WebSocket connect. A fresh dict (module
    docstring) - never ``record["used"] = True`` on the object already in ``context``."""
    context[CONTEXT_KEY] = {**record, "used": True}


def clear(context: dict[str, Any]) -> None:
    context[CONTEXT_KEY] = None


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def is_expired(record: dict[str, Any], *, now: datetime) -> bool:
    expires_at = parse_iso(record.get("expires_at"))
    if expires_at is None:
        return True
    return now > expires_at


def token_matches(record: dict[str, Any] | None, token: str) -> bool:
    """Constant-time, and refuses a record already spent (single-use) or a blank guess."""
    if record is None or record.get("used") or not token:
        return False
    live = record.get("stream_token")
    return isinstance(live, str) and secrets.compare_digest(live, token)


def end_receipt(
    db: Session | None,
    *,
    session_id: uuid.UUID,
    action_id: str,
    record: dict[str, Any],
    device_action: DeviceActionPort | None,
    moves: int,
    buttons: int,
    dropped: int,
    started_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    """``pointer.stream_end`` on the device (best effort: a device that went offline
    mid-stream still gets an honest receipt, never a hang) and the ONE receipt shape
    every ending path writes under ``operator.pointer_session``.

    Not built through ``OperatorService.start_task``/``Plan`` like ``begin`` is: the
    counts this receipt carries (``moves``/``buttons``/``dropped``) live in the
    WebSocket's own memory, not in anything an ``OperatorTask`` step observes, and this
    is reached from three places that do not share one call site.
    """
    duration_ms = max(0, int((now - started_at).total_seconds() * 1000))
    stopped = False
    if device_action is not None:
        try:
            result = device_action.run(
                capability="pointer.stream_end",
                payload={"session": record["session"]},
                idempotency_key=f"pointer-stream-end-{record['session']}",
                timeout_s=10.0,
            )
            stopped = bool(getattr(result, "ok", False))
        except Exception:  # noqa: BLE001 - the receipt still records what WE observed
            stopped = False
    server = {
        "moves": int(moves),
        "buttons": int(buttons),
        "dropped": int(dropped),
        "duration_ms": duration_ms,
    }
    receipt = ActionReceipt(
        action_id=action_id,
        capability=CAPABILITY_POINTER_SESSION,
        requested_state="closed",
        execution_status=EXECUTION_EXECUTED if stopped else EXECUTION_FAILED,
        terminal_status=TERMINAL_VERIFIED if stopped else TERMINAL_UNVERIFIED,
        observed_after={"server": server, "local": {}},
        evidence_refs=[{"kind": "realtime_session", "ref": str(session_id)}],
        error_class=None if stopped else "device_unreachable",
        speech=SPEECH_CLOSED if stopped else SPEECH_CLOSED_UNCONFIRMED,
        started_at=started_at,
        completed_at=now,
        session_id=str(session_id),
        observed_at=now,
    )
    if db is not None:
        record_receipt(db, receipt, SUBSYSTEM_OPERATOR)
    return receipt.as_dict()


__all__ = [
    "CONTEXT_KEY",
    "MAX_CLIENT_FRAMES_PER_S",
    "MAX_MOVE_DELTA",
    "SILENCE_TIMEOUT_S",
    "SPEECH_CLOSED",
    "SPEECH_CLOSED_UNCONFIRMED",
    "SPEECH_NOTHING_OPEN",
    "SPEECH_OPENED",
    "SPEECH_OPEN_FAILED",
    "STREAM_EXPIRES_S",
    "STREAM_TOKEN_BYTES",
    "build_record",
    "clear",
    "end_receipt",
    "is_expired",
    "mark_used",
    "new_session_id",
    "new_stream_token",
    "parse_iso",
    "record_from_context",
    "token_matches",
]
