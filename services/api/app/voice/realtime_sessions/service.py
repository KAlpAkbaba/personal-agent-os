"""Realtime session transactions (M12 spec §4, §6, §7, §9).

Every function takes the SQLAlchemy ``Session`` first and owns its commit, like
the other service modules. Every step writes a ``voice_*`` audit row into the
shared ``audit_events`` table (category ``voice_realtime``) with ids and
timings only: the metadata scrubber refuses any key that smells like audio or
a credential, and no function here ever receives the provider secret after it
has been handed to the client once.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.broker.audit import record_audit_event
from app.identity.service import SessionContext
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_VOICE_SESSION_ATTACHED,
    EVENT_TYPE_VOICE_SESSION_CLOSED,
    EVENT_TYPE_VOICE_SESSION_CREATED,
    SUBSYSTEM_VOICE,
)
from app.logging import get_logger
from app.narration import service as narration_service
from app.narration.commands import NarrationState, State
from app.uistate import UiState
from app.uistate import publish as publish_ui
from app.voice import service as voice_service
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.intents import Intent, ResolvedIntent, resolve_intent
from app.voice.providers import EphemeralCredential, RealtimeProvider, RealtimeSessionConfig
from app.voice.realtime import RealtimeState
from app.voice.realtime_bench import (
    SOURCE_CLIENT,
    TIMING_EVENT_KINDS,
    RealtimeBenchReport,
    build_report,
    events_from_client_reports,
)
from app.voice.realtime_sessions.models import (
    REALTIME_STATE_ACTIVE,
    REALTIME_STATE_CLOSED,
    REALTIME_STATE_CREATED,
    REALTIME_STATE_EXPIRED,
    TOOL_STATUS_FAILED,
    TOOL_STATUS_RUNNING,
    TOOL_STATUS_SUCCEEDED,
    RealtimeSessionRow,
    RealtimeToolCall,
)
from app.voice.realtime_sessions.persona import build_instructions
from app.voice.realtime_sessions.sideband import (
    SB_LEG_CLOSED,
    SB_NARRATION_CURSOR,
    SB_TOOL_COMPLETED,
    SidebandPusher,
    sideband_frame,
)
from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.realtime_sessions.service")

AUDIT_CATEGORY = "voice_realtime"
ACTION_SESSION_CREATED = "voice_session_created"
ACTION_SESSION_ATTACHED = "voice_session_attached"
ACTION_LEG_CLOSED = "voice_leg_closed"
ACTION_SESSION_CLOSED = "voice_session_closed"
ACTION_SESSION_EXPIRED = "voice_session_expired"
ACTION_CREDENTIAL_MINTED = "voice_credential_minted"
ACTION_TOOL_CALL = "voice_tool_call"
ACTION_TOOL_CALL_REPLAYED = "voice_tool_call_replayed"
ACTION_TOOL_COMPLETED = "voice_tool_completed"
ACTION_CLIENT_EVENT = "voice_client_event"
ACTION_INTENT_RESOLVED = "voice_intent_resolved"
ACTION_SIDEBAND_QUEUED = "voice_sideband_queued"
ACTION_SIDEBAND_PUSHED = "voice_sideband_pushed"

#: client-reported event kinds accepted by POST .../events
STATE_EVENT_KINDS = ("utterance", "summary", "intent", "state", "error", "spoken")
CLIENT_EVENT_KINDS = TIMING_EVENT_KINDS + STATE_EVENT_KINDS

MAX_PENDING_SIDEBAND = 50
MAX_SUMMARY_CHARS = 2000

#: Any metadata/payload key containing one of these never reaches an audit row
#: (and is refused at the route). Keys are NORMALIZED before matching - case and
#: separators dropped - so ``apiKey``, ``api-key`` and ``API_KEY`` are all the
#: same key as ``api_key``; the literal-substring check this replaced let the
#: camelCase and hyphenated spellings through both the validator and the scrubber.
FORBIDDEN_KEY_PARTS = (
    "audio",
    "pcm",
    "wave",
    "secret",
    "credential",
    "token",
    "apikey",
    "password",
    "text",
    "transcript",
)
_KEY_NORMALIZER = re.compile(r"[^a-z0-9]+")


def is_forbidden_key(key: Any) -> bool:
    """True when ``key`` is audio/credential/transcript-shaped under any spelling."""
    normalized = _KEY_NORMALIZER.sub("", str(key).lower())
    return any(part in normalized for part in FORBIDDEN_KEY_PARTS)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None, fallback: datetime) -> datetime:
    """SQLite hands naive datetimes back; treat them as UTC."""
    if dt is None:
        return fallback
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def scrub_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Ids and timings only: drop forbidden keys, bytes, and long strings."""
    out: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if is_forbidden_key(key):
            continue
        if isinstance(value, bytes | bytearray):
            continue
        if isinstance(value, str):
            out[key] = value[:256]
        elif isinstance(value, dict):
            out[key] = scrub_metadata(value)
        elif isinstance(value, list | tuple):
            out[key] = [v for v in value if not isinstance(v, bytes | bytearray)][:50]
        else:
            out[key] = value
    return out


def _audit(
    db: Session,
    action: str,
    row: RealtimeSessionRow | None,
    *,
    trace_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    record_audit_event(
        db,
        category=AUDIT_CATEGORY,
        action=action,
        subject_ref=str(row.id) if row is not None else None,
        device_id=row.device_id if row is not None else None,
        trace_id=trace_id,
        metadata=scrub_metadata(metadata),
    )


_LEDGER_EVENT_TYPE_BY_STATE = {
    "created": EVENT_TYPE_VOICE_SESSION_CREATED,
    "attached": EVENT_TYPE_VOICE_SESSION_ATTACHED,
    "closed": EVENT_TYPE_VOICE_SESSION_CLOSED,
}
_LEDGER_SUMMARY_BY_STATE = {
    "created": "Sesli oturum oluşturuldu.",
    "attached": "Sesli oturum yeniden bağlandı.",
    "closed": "Sesli oturum kapandı.",
}


def _ledger(
    db: Session,
    state: str,
    row: RealtimeSessionRow,
    *,
    trace_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Activity Ledger write for create/attach/close (M16 track A, spec §1.2
    writer table: ``source_ref = realtime_sessions:<id>:<state>``).
    Best-effort: a ledger failure must never fail the voice session
    transition it is describing — that transition already committed."""
    try:
        ledger_service.record(
            db,
            ledger_service.ActivityEvent(
                event_type=_LEDGER_EVENT_TYPE_BY_STATE[state],
                subsystem=SUBSYSTEM_VOICE,
                action=f"voice_session_{state}",
                factual_summary=_LEDGER_SUMMARY_BY_STATE[state],
                occurred_at=utcnow(),
                trace_id=trace_id,
                evidence_refs=[{"kind": "realtime_session", "ref": str(row.id)}],
                detail_json=detail or {},
                source="live",
                source_ref=f"realtime_sessions:{row.id}:{state}",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning(
            "ledger_record_failed", session_id=str(row.id), error=f"{type(exc).__name__}: {exc}"
        )


# ---------------------------------------------------------------- sessions


def get_session(db: Session, session_id: uuid.UUID) -> RealtimeSessionRow | None:
    return db.get(RealtimeSessionRow, session_id)


def require_live(
    db: Session,
    row: RealtimeSessionRow,
    *,
    now: datetime | None = None,
    trace_id: str | None = None,
) -> RealtimeSessionRow:
    """Refuse a closed/expired session; lazily mark expiry."""
    now = now or utcnow()
    if row.state in (REALTIME_STATE_CLOSED, REALTIME_STATE_EXPIRED):
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"session is {row.state}",
            details={"state": row.state},
        )
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    if expires <= now:
        row.state = REALTIME_STATE_EXPIRED
        row.closed_at = now
        row.updated_at = now
        _audit(
            db,
            ACTION_SESSION_EXPIRED,
            row,
            trace_id=trace_id,
            metadata={"expires_at": _iso(row.expires_at)},
        )
        db.commit()
        _publish_session_over(row, reason="expired")
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            "session has expired",
            details={"state": REALTIME_STATE_EXPIRED},
        )
    return row


def require_leg(row: RealtimeSessionRow, owner: SessionContext) -> None:
    """Tool execution and events are accepted only from the owner API session
    that holds the CURRENT media leg (spec §9; attach moves the leg)."""
    if row.owner_session_id != owner.session_id:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            "this owner session does not hold the session's current media leg; attach first",
            details={"leg": "mismatch"},
        )


def mint_credential(
    provider: RealtimeProvider,
    *,
    session_id: uuid.UUID,
    ttl_s: int,
    transport: str,
    session_config: RealtimeSessionConfig | None = None,
) -> EphemeralCredential:
    """Through the adapter interface only: the runtime never reads a vendor key here.
    ``session_config`` carries the persona instructions + tool manifest so a real
    provider bakes them into the session server-side (spec §4 step 1)."""
    return provider.mint_credential(
        session_id=str(session_id), ttl_s=ttl_s, transport=transport, session_config=session_config
    )


def _session_config(
    row: RealtimeSessionRow,
    *,
    registry: ToolRegistry,
    prefs: Any,
) -> RealtimeSessionConfig:
    ctx = row.context_json or {}
    return RealtimeSessionConfig(
        language=row.language,
        instructions=build_instructions(
            prefs,
            narration_attached=bool(ctx.get("narration_session_id")),
            plan=ctx.get("plan"),
            transcript_summary=row.transcript_summary,
            voice_profile=ctx.get("voice_profile"),
        ),
        tools=tuple(registry.manifest()),
        voice=ctx.get("voice"),
    )


def create_session(
    db: Session,
    *,
    owner: SessionContext,
    provider: RealtimeProvider,
    transport: str,
    client_kind: str | None = None,
    device_id: uuid.UUID | None = None,
    language: str = "tr-TR",
    session_ttl_s: int = 3600,
    credential_ttl_s: int = 600,
    narration_session_id: uuid.UUID | None = None,
    voice: str | None = None,
    voice_profile: str | None = None,
    registry: ToolRegistry,
    selection: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> tuple[RealtimeSessionRow, EphemeralCredential, dict[str, Any]]:
    """Spec §4 step 1. Returns the row, the one-time credential and the
    client payload (session id, provider, transport, tools, instructions)."""
    now = utcnow()
    if narration_session_id is not None:
        if narration_service.get_session(db, narration_session_id) is None:
            raise VoiceError(
                VoiceErrorClass.VALIDATION_ERROR, "narration_session_id does not exist"
            )
    row = RealtimeSessionRow(
        provider=provider.name,
        transport=transport,
        client_kind=(client_kind or owner.client_kind)[:16],
        device_id=device_id if device_id is not None else owner.device_id,
        owner_session_id=owner.session_id,
        language=language,
        state=REALTIME_STATE_CREATED,
        narration_session_id=narration_session_id,
        context_json={
            "narration_session_id": str(narration_session_id) if narration_session_id else None,
            "pending_sideband": [],
            "fsm_state": RealtimeState.IDLE.value,
            "barge_in_count": 0,
            "legs": 1,
            "selection": selection or {},
            # ADR-0043: the requested wire voice (already validated against the
            # provider) and the owner's perceptual profile, recorded for the benchmark
            "voice": voice,
            "voice_profile": voice_profile,
            # the provider's model id, so the evidence record names what spoke
            "model": str(getattr(provider, "model", "") or "") or None,
        },
        transcript_summary="",
        # explicit, microsecond-precision creation time: the server default renders at
        # one-second resolution on SQLite and "newest first" then breaks on ties
        created_at=now,
        expires_at=now + timedelta(seconds=session_ttl_s),
        updated_at=now,
    )
    db.add(row)
    db.flush()
    prefs = voice_service.load_preferences(db)
    config = _session_config(row, registry=registry, prefs=prefs)
    credential = mint_credential(
        provider,
        session_id=row.id,
        ttl_s=credential_ttl_s,
        transport=transport,
        session_config=config,
    )
    _audit(
        db,
        ACTION_SESSION_CREATED,
        row,
        trace_id=trace_id,
        metadata={
            "provider": row.provider,
            "transport": row.transport,
            "client_kind": row.client_kind,
            "owner_session_id": str(owner.session_id),
            "expires_at": _iso(row.expires_at),
            "narration_session_id": row.context_json.get("narration_session_id"),
            "selection": selection or {},
            "voice": voice,
            "voice_profile": voice_profile,
        },
    )
    _audit(
        db,
        ACTION_CREDENTIAL_MINTED,
        row,
        trace_id=trace_id,
        metadata={
            "provider": credential.provider,
            "session_ref": credential.session_ref,
            "expires_at": _iso(credential.expires_at),
            "ttl_s": credential_ttl_s,
        },
    )
    db.commit()
    _ledger(db, "created", row, trace_id=trace_id, detail={"provider": row.provider})
    payload = _leg_payload(row, credential, registry=registry, config=config)
    return row, credential, payload


def _leg_payload(
    row: RealtimeSessionRow,
    credential: EphemeralCredential,
    *,
    registry: ToolRegistry,
    config: RealtimeSessionConfig,
) -> dict[str, Any]:
    """The client sees exactly the instructions/tools the credential was minted
    with (same ``config`` object), so the two can never drift."""
    return {
        "session_id": str(row.id),
        "provider": row.provider,
        "transport": row.transport,
        "credential": credential.to_client_dict(),
        "tools": registry.manifest(),
        "instructions": config.instructions,
        "language": row.language,
        "expires_at": _iso(row.expires_at),
        "state": row.state,
        "voice": (row.context_json or {}).get("voice"),
        "voice_profile": (row.context_json or {}).get("voice_profile"),
    }


def session_state(db: Session, row: RealtimeSessionRow) -> dict[str, Any]:
    """The continuity state (spec §7). Never includes a credential."""
    ctx = row.context_json or {}
    narration: dict[str, Any] | None = None
    if row.narration_session_id is not None:
        nrow = narration_service.get_session(db, row.narration_session_id)
        if nrow is not None:
            cursor = {k: v for k, v in (nrow.semantic_cursor_json or {}).items() if k != "_state"}
            narration = {
                "narration_session_id": str(nrow.id),
                "state": nrow.state,
                "speed": nrow.speed,
                "cursor": cursor,
                "artifact_id": str(nrow.artifact_id),
            }
    return {
        "session_id": str(row.id),
        "provider": row.provider,
        "transport": row.transport,
        "client_kind": row.client_kind,
        "device_id": str(row.device_id) if row.device_id else None,
        "state": row.state,
        "language": row.language,
        "plan": ctx.get("plan"),
        "plan_id": str(row.plan_id) if row.plan_id else None,
        "narration": narration,
        "presentation": ctx.get("presentation"),
        "last_intent": ctx.get("last_intent"),
        "fsm_state": ctx.get("fsm_state"),
        "barge_in_count": int(ctx.get("barge_in_count", 0)),
        "voice": ctx.get("voice"),
        "voice_profile": ctx.get("voice_profile"),
        "network": ctx.get("network"),
        "legs": int(ctx.get("legs", 1)),
        "pending_sideband_count": len(ctx.get("pending_sideband") or []),
        "transcript_summary": row.transcript_summary,
        "created_at": _iso(row.created_at),
        "expires_at": _iso(row.expires_at),
        "closed_at": _iso(row.closed_at),
    }


def _touch(row: RealtimeSessionRow, now: datetime) -> None:
    if row.state == REALTIME_STATE_CREATED:
        row.state = REALTIME_STATE_ACTIVE
    row.updated_at = now


def _set_context(row: RealtimeSessionRow, ctx: dict[str, Any]) -> None:
    # Reassign so SQLAlchemy sees a changed JSON value (no MutableDict here).
    row.context_json = dict(ctx)


# ---------------------------------------------------------------- sideband


def _deliver(
    db: Session,
    row: RealtimeSessionRow,
    ctx: dict[str, Any],
    sideband: SidebandPusher,
    event: str,
    payload: dict[str, Any],
    *,
    trace_id: str | None,
) -> bool:
    frame = sideband_frame(row.id, event, payload)
    delivered = sideband.push(device_id=row.device_id, frame=frame)
    if delivered:
        _audit(
            db,
            ACTION_SIDEBAND_PUSHED,
            row,
            trace_id=trace_id,
            metadata={"event": event, "device_id": str(row.device_id)},
        )
        return True
    pending = list(ctx.get("pending_sideband") or [])
    pending.append(frame)
    ctx["pending_sideband"] = pending[-MAX_PENDING_SIDEBAND:]
    _audit(
        db,
        ACTION_SIDEBAND_QUEUED,
        row,
        trace_id=trace_id,
        metadata={"event": event, "queued": len(ctx["pending_sideband"])},
    )
    return False


def drain_pending_sideband(row: RealtimeSessionRow) -> list[dict[str, Any]]:
    ctx = dict(row.context_json or {})
    pending = list(ctx.get("pending_sideband") or [])
    ctx["pending_sideband"] = []
    _set_context(row, ctx)
    return pending


# -------------------------------------------------------------- tool calls


def _tool_row_payload(
    call: RealtimeToolCall, *, preamble: str | None = None, replayed: bool = False
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "call_id": call.call_id,
        "name": call.name,
        "status": call.status,
        "long_running": call.long_running,
        "replayed": replayed,
    }
    if call.status == TOOL_STATUS_SUCCEEDED:
        out["result"] = call.result_json
    elif call.status == TOOL_STATUS_FAILED:
        out["error"] = {"error_class": call.error_class, **(call.result_json or {})}
    elif call.status == TOOL_STATUS_RUNNING:
        out["result"] = call.result_json
        if preamble:
            out["preamble"] = preamble
    return out


def get_tool_call(db: Session, session_id: uuid.UUID, call_id: str) -> RealtimeToolCall | None:
    return db.execute(
        select(RealtimeToolCall).where(
            RealtimeToolCall.session_id == session_id, RealtimeToolCall.call_id == call_id
        )
    ).scalar_one_or_none()


def handle_tool_call(
    db: Session,
    row: RealtimeSessionRow,
    *,
    owner: SessionContext,
    call_id: str,
    name: str,
    arguments: dict[str, Any],
    registry: ToolRegistry,
    sideband: SidebandPusher,
    trace_id: str | None = None,
    live: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Spec §4 step 3. Idempotent on ``call_id`` per session: a replay returns
    the recorded outcome and executes nothing.

    ``live`` carries the in-process runtimes a handler may read (the presence engine,
    the broker, a health probe) - see ``ToolContext.live``."""
    now = utcnow()
    require_live(db, row, now=now, trace_id=trace_id)
    require_leg(row, owner)
    existing = get_tool_call(db, row.id, call_id)
    if existing is not None:
        spec = registry.get(existing.name)
        _audit(
            db,
            ACTION_TOOL_CALL_REPLAYED,
            row,
            trace_id=trace_id,
            metadata={"call_id": call_id, "name": existing.name, "status": existing.status},
        )
        db.commit()
        return _tool_row_payload(existing, preamble=spec.preamble if spec else None, replayed=True)

    spec = registry.get(name)
    if spec is not None:
        name = spec.name  # the vendor spelling (research__start) is recorded canonically
    call = RealtimeToolCall(
        session_id=row.id,
        call_id=call_id,
        name=name,
        arguments_json=dict(arguments),
        status=TOOL_STATUS_RUNNING,
        long_running=bool(spec and spec.long_running),
    )
    db.add(call)
    try:
        db.flush()
    except IntegrityError:
        # Lost a race with the same call_id: replay the winner's outcome.
        db.rollback()
        winner = get_tool_call(db, row.id, call_id)
        if winner is None:  # pragma: no cover - only on a genuine DB fault
            raise
        return _tool_row_payload(winner, replayed=True)

    ctx = dict(row.context_json or {})
    tool_ctx = ToolContext(
        session_id=row.id,
        owner_session_id=owner.session_id,
        device_id=row.device_id,
        client_kind=row.client_kind,
        context=ctx,
        db=db,
        now=now,
        call_id=call_id,
        live=dict(live or {}),
    )
    started = utcnow()
    preamble: str | None = None
    if spec is None:
        call.status = TOOL_STATUS_FAILED
        call.error_class = VoiceErrorClass.CAPABILITY_MISSING.value
        call.result_json = {"message": f"unknown tool {name!r}", "available": registry.names()}
        call.completed_at = utcnow()
    else:
        try:
            result = spec.handler(tool_ctx, dict(arguments))
        except VoiceError as exc:
            call.status = TOOL_STATUS_FAILED
            call.error_class = exc.error_class.value
            call.result_json = {"message": exc.message, "details": exc.details}
            call.completed_at = utcnow()
        except Exception as exc:  # noqa: BLE001 - a tool bug must not kill the session
            logger.exception("voice_tool_handler_crashed", tool=name, call_id=call_id)
            call.status = TOOL_STATUS_FAILED
            call.error_class = VoiceErrorClass.INTERNAL_BUG.value
            call.result_json = {"message": f"{type(exc).__name__}"}
            call.completed_at = utcnow()
        else:
            if spec.long_running:
                call.status = TOOL_STATUS_RUNNING
                call.result_json = result
                preamble = spec.preamble
                plan = ctx.get("plan") or {}
                if plan.get("plan_id"):
                    row.plan_id = uuid.UUID(str(plan["plan_id"]))
            else:
                call.status = TOOL_STATUS_SUCCEEDED
                call.result_json = result
                call.completed_at = utcnow()
    duration_ms = int((utcnow() - started).total_seconds() * 1000)
    for event, payload in tool_ctx.pushes:
        _deliver(db, row, ctx, sideband, event, payload, trace_id=trace_id)
    _set_context(row, ctx)
    _touch(row, now)
    _audit(
        db,
        ACTION_TOOL_CALL,
        row,
        trace_id=trace_id,
        metadata={
            "call_id": call_id,
            "name": name,
            "status": call.status,
            "long_running": call.long_running,
            "duration_ms": duration_ms,
            "error_class": call.error_class,
            "plan_id": str(row.plan_id) if row.plan_id else None,
            "preamble_chars": len(preamble or ""),
        },
    )
    db.commit()
    return _tool_row_payload(call, preamble=preamble)


def complete_tool_call(
    db: Session,
    row: RealtimeSessionRow,
    *,
    owner: SessionContext,
    call_id: str,
    result: dict[str, Any] | None,
    error: dict[str, Any] | None,
    sideband: SidebandPusher,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """A long-running tool finished. Records the outcome and pushes
    ``tool_completed`` over the sideband so the client can submit the final
    output to the provider (spec §4 step 3-4).

    Reachable only through the owner router, so it is gated exactly like its
    siblings: the session must be live and the caller must hold the CURRENT
    media leg. Without that, a leg superseded by ``attach`` (whose owner bearer
    is still valid) could inject a tool result into the live conversation, and
    a closed session could still be written to. A worker/pipeline that
    completes tool calls without a media leg must arrive through its own,
    non-owner credential - never through this path."""
    now = utcnow()
    require_live(db, row, now=now, trace_id=trace_id)
    require_leg(row, owner)
    call = get_tool_call(db, row.id, call_id)
    if call is None:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, f"unknown tool call {call_id!r}")
    if call.status != TOOL_STATUS_RUNNING:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"tool call {call_id!r} is already {call.status}",
            details={"status": call.status},
        )
    if error:
        call.status = TOOL_STATUS_FAILED
        call.error_class = str(error.get("error_class") or VoiceErrorClass.DEPENDENCY_UNAVAILABLE)
        call.result_json = {"message": str(error.get("message") or "")[:2000]}
    else:
        call.status = TOOL_STATUS_SUCCEEDED
        call.result_json = dict(result or {})
    call.completed_at = now
    ctx = dict(row.context_json or {})
    plan = ctx.get("plan")
    if plan and str(plan.get("plan_id")) == (str(row.plan_id) if row.plan_id else None):
        plan = dict(plan)
        plan["status"] = "completed" if call.status == TOOL_STATUS_SUCCEEDED else "failed"
        ctx["plan"] = plan
    payload = {
        "call_id": call.call_id,
        "name": call.name,
        "status": call.status,
        "result": call.result_json if call.status == TOOL_STATUS_SUCCEEDED else None,
        "error": (
            {"error_class": call.error_class, **(call.result_json or {})}
            if call.status == TOOL_STATUS_FAILED
            else None
        ),
    }
    delivered = _deliver(db, row, ctx, sideband, SB_TOOL_COMPLETED, payload, trace_id=trace_id)
    _set_context(row, ctx)
    row.updated_at = now
    _audit(
        db,
        ACTION_TOOL_COMPLETED,
        row,
        trace_id=trace_id,
        metadata={
            "call_id": call_id,
            "name": call.name,
            "status": call.status,
            "delivered": delivered,
            "error_class": call.error_class,
            "running_ms": int((now - _aware(call.created_at, now)).total_seconds() * 1000),
        },
    )
    db.commit()
    return {**payload, "delivered": delivered}


# ------------------------------------------------------------ client events


def _narration_state_for(db: Session, row: RealtimeSessionRow) -> NarrationState | None:
    if row.narration_session_id is None:
        return None
    nrow = narration_service.get_session(db, row.narration_session_id)
    if nrow is None:
        return None
    try:
        return NarrationState(state=State(nrow.state), speed=nrow.speed)
    except ValueError:
        return None


def record_client_events(
    db: Session,
    row: RealtimeSessionRow,
    *,
    owner: SessionContext,
    events: list[dict[str, Any]],
    trace_id: str | None = None,
    sideband: SidebandPusher | None = None,
) -> dict[str, Any]:
    """Spec §4 step 5: timing events (benchmark) and state transitions (audit).

    Timing events are stored as audit rows and later assembled into a report;
    ``utterance`` events are resolved into intents HERE (Cloud Core resolves,
    the client only transcribes) and the intent — not the text — is audited.
    """
    now = utcnow()
    require_live(db, row, now=now, trace_id=trace_id)
    require_leg(row, owner)
    ctx = dict(row.context_json or {})
    resolved: list[dict[str, Any]] = []
    narration_state = None
    accepted = 0
    sideband_payloads: list[tuple[str, dict[str, Any]]] = []
    for ev in events:
        kind = str(ev.get("kind"))
        if kind not in CLIENT_EVENT_KINDS:
            continue
        payload = dict(ev.get("payload") or {})
        t_ms = int(ev.get("t_ms", 0))
        turn = int(ev.get("turn") or 0)
        meta: dict[str, Any] = {"kind": kind, "t_ms": t_ms, "turn": turn}
        if kind == "utterance":
            if narration_state is None:
                narration_state = _narration_state_for(db, row)
            text = str(ev.get("text") or payload.get("utterance") or "")
            fsm = ctx.get("fsm_state")
            intent: ResolvedIntent = resolve_intent(
                text,
                session_state=RealtimeState(fsm) if fsm else None,
                narration=narration_state,
            )
            ctx["last_intent"] = intent.intent.value
            resolved.append(
                {"t_ms": t_ms, "turn": turn, **intent.to_dict(), "normalized_text": None}
            )
            if intent.intent == Intent.EYE_DISABLE:
                # M18 spec §2: "Gözünü kapat", "Kamerayı kapat" and "Beni izleme"
                # stop perception immediately. This is deterministic — it does not
                # wait for the realtime provider to decide to call a tool — because
                # a privacy-critical disable must not depend on a model's judgment
                # call. app.presence.eye.disable_eye is itself idempotent, durable
                # (ledger row) and observable (eye.disabled UI-state event); a
                # repeated phrase just repeats the same real owner action.
                from app.presence.eye import disable_eye

                try:
                    changed = bool(disable_eye(db, reason=f"voice:{intent.matched}"))
                    meta["eye_disable"] = "applied" if changed else "already"
                    # The bookkeeping the eye.disable tool handler reads (contract §5.3):
                    # if the model calls the tool for this same command, the receipt says
                    # "verified" - the command closed the eye - rather than "already",
                    # even though the durable write was this safety net's.
                    ctx["eye_safety"] = {
                        "turn": turn,
                        "action": "disable",
                        "applied_at": _iso(now),
                        "changed": changed,
                    }
                except Exception as exc:  # noqa: BLE001
                    # The failure is caught so one broken write cannot lose the
                    # owner's transcript - but it is NOT swallowed. The owner just
                    # said "stop watching me" and it did not happen; a debug line
                    # nobody reads is the wrong place for that. It goes on the
                    # audit record, and onto the UI-state bus as an error, so the
                    # Core can say the camera did not close.
                    logger.error(
                        "voice_eye_disable_failed",
                        matched=intent.matched,
                        reason=type(exc).__name__,
                    )
                    meta["eye_disable"] = "failed"
                    meta["eye_disable_error"] = type(exc).__name__
                    publish_ui(
                        UiState.ERROR,
                        subsystem="presence",
                        severity="critical",
                        status="eye_disable_failed",
                        session_id=str(row.id),
                        label="kamera kapatılamadı",
                    )
            meta.update(
                {
                    "intent": intent.intent.value,
                    "scope": intent.scope,
                    "target_index": intent.target_index,
                    "chars": len(text),
                    "fillers_removed": intent.fillers_removed,
                    # WHAT the owner asked, normalised - never the words themselves. Without
                    # it the durable record could not distinguish "the owner never asked
                    # that question" from "the owner asked and the model failed to route
                    # it", and the second is what actually happened to the world model on
                    # 2026-09-05 (owner M17 run).
                    "query_kind": intent.query_kind,
                    # query | action | control and the capability an action targets
                    # (contract §2), so the record says what CLASS of thing was asked.
                    "klass": intent.klass,
                    "capability": intent.capability,
                }
            )
            _audit(db, ACTION_INTENT_RESOLVED, row, trace_id=trace_id, metadata=meta)
            accepted += 1
            continue
        if kind in _UI_STATE_BY_EVENT:
            # The future Holographic Core draws what is actually happening (ADR-0052): the
            # client's own timing events are the truest signal we have, and they carry no
            # content. Bounded energy only - never a sample, never a transcript.
            ui_state, intensity = _UI_STATE_BY_EVENT[kind]
            publish_ui(
                ui_state,
                subsystem="voice",
                intensity=_ui_energy(payload, intensity),
                session_id=str(row.id),
                status=str(ctx.get("fsm_state") or "") or None,
                metadata={"turn": turn},
            )
        if kind == "spoken":
            # The assistant transcript spoken so far (M16 spec §3.2). Used once to place
            # the narration cursor, then dropped: the text itself is never audited.
            text = str(ev.get("text") or "")
            final = int(payload.get("final") or 0) == 1
            aligned = _align_narration(db, row, ctx, sideband_payloads, text, final=final, now=now)
            meta.update({"chars": len(text), "final": int(final), **aligned})
            payload = {k: v for k, v in payload.items() if k != "text"}
        elif kind == "summary":
            row.transcript_summary = str(ev.get("text") or "")[:MAX_SUMMARY_CHARS]
            meta["chars"] = len(row.transcript_summary)
        elif kind == "state":
            state = str(payload.get("state") or "")
            if state in RealtimeState.__members__:
                ctx["fsm_state"] = state
                meta["state"] = state
        elif kind == "barge_in_start":
            ctx["barge_in_count"] = int(ctx.get("barge_in_count", 0)) + 1
        elif kind == "network_lost":
            ctx["network"] = "lost"
        elif kind == "network_restored":
            ctx["network"] = "restored"
        elif kind == "error":
            meta["error_class"] = str(payload.get("error_class") or "")[:64]
        meta["payload"] = payload
        _audit(db, ACTION_CLIENT_EVENT, row, trace_id=trace_id, metadata=meta)
        accepted += 1
    for event, sb_payload in sideband_payloads:
        if sideband is not None:
            _deliver(db, row, ctx, sideband, event, sb_payload, trace_id=trace_id)
        else:  # no push path: the frame rides this response's pending_sideband
            queued = list(ctx.get("pending_sideband") or [])
            queued.append(sideband_frame(row.id, event, sb_payload))
            ctx["pending_sideband"] = queued[-MAX_PENDING_SIDEBAND:]
    pending = list(ctx.get("pending_sideband") or [])
    ctx["pending_sideband"] = []
    _set_context(row, ctx)
    _touch(row, now)
    db.commit()
    return {
        "accepted": accepted,
        "resolved_intents": resolved,
        "pending_sideband": pending,
        "state": session_state(db, row),
    }


#: Which client event means which owner-visible state, and how loud it looks by default.
#: `mic_speech_start` = the owner started talking; `first_audio` = the assistant is
#: speaking; `end_of_turn` = it is thinking between the two; `barge_in_start` = the owner
#: cut in; `response_done` = back to listening.
_UI_STATE_BY_EVENT: dict[str, tuple[UiState, float]] = {
    "mic_speech_start": (UiState.LISTENING, 0.5),
    "uplink_first_packet": (UiState.LISTENING, 0.6),
    "end_of_turn": (UiState.THINKING, 0.7),
    "first_audio": (UiState.SPEAKING, 0.6),
    "barge_in_start": (UiState.LISTENING, 0.9),
    "response_done": (UiState.LISTENING, 0.3),
    "network_lost": (UiState.ERROR, 0.8),
}


def _ui_energy(payload: dict[str, Any], default: float) -> float:
    """A bounded 0..1 animation signal from what the client already reports.

    The client's noise/level numbers are decibel-ish margins above its calibrated floor;
    a renderer only needs "how strong is this". Anything unusable falls back to the
    state's default intensity. No audio, ever.
    """
    for key in ("level_db", "margin_db", "rms_db"):
        value = payload.get(key)
        if isinstance(value, int | float):
            return max(0.0, min(1.0, (float(value) + 60.0) / 60.0))
    for key in ("energy", "intensity"):
        value = payload.get(key)
        if isinstance(value, int | float):
            return max(0.0, min(1.0, float(value)))
    return default


def _align_narration(
    db: Session,
    row: RealtimeSessionRow,
    ctx: dict[str, Any],
    pushes: list[tuple[str, dict[str, Any]]],
    spoken: str,
    *,
    final: bool,
    now: datetime,
) -> dict[str, Any]:
    """Place the attached narration's cursor where the speech actually stopped.

    A ``spoken`` event at barge-in (``final`` false) pauses the narration at the first
    sentence that was NOT fully spoken; at response completion (``final`` true) the
    cursor moves past what was read. Without an attached narration there is nothing to
    place and the event is only counted.
    """
    if row.narration_session_id is None:
        return {"aligned": 0}
    nrow = narration_service.get_session(db, row.narration_session_id)
    if nrow is None:
        return {"aligned": 0}
    from app.artifacts import service as artifact_service
    from app.narration.align import align
    from app.narration.engine import build_plan
    from app.narration.routes import _pack_state, _unpack_state

    version = artifact_service.get_version(db, nrow.artifact_id, nrow.artifact_version)
    if version is None:
        version = artifact_service.get_current_version(db, nrow.artifact_id)
    body_md = version.canonical_body if version else ""
    plan = build_plan(
        body_md,
        artifact_id=str(nrow.artifact_id),
        version=nrow.artifact_version,
        pronunciation=narration_service.pronunciation_map(db),
    )
    state = _unpack_state(nrow)
    result = align(plan, state.cursor, spoken)
    if final:
        new_state = replace(
            state,
            cursor=result.cursor or state.cursor,
            state=State.PAUSED if result.complete else state.state,
        )
        action = "spoken_complete" if result.complete else "spoken_progress"
    else:
        new_state = replace(state, cursor=result.cursor or state.cursor, state=State.PAUSED)
        action = "paused"
    narration_service.update_cursor(
        db,
        nrow.id,
        cursor=_pack_state(new_state),
        state=new_state.state.value,
        device_id=row.device_id,
    )
    cursor_payload = {
        "narration_session_id": str(nrow.id),
        "cursor": new_state.cursor.as_dict() if new_state.cursor else None,
        "state": new_state.state.value,
        "speed": new_state.speed,
        "action": action,
        "spoken_chunks": result.spoken_chunks,
    }
    pushes.append((SB_NARRATION_CURSOR, cursor_payload))
    if not final:
        _ledger_narration_event(
            db,
            row,
            nrow.id,
            now,
            "voice.narration.paused",
            {
                "action": action,
                "cursor": cursor_payload["cursor"],
                "spoken_chunks": result.spoken_chunks,
            },
        )
    return {
        "aligned": 1,
        "spoken_chunks": result.spoken_chunks,
        "complete": int(result.complete),
        "action": action,
    }


def _ledger_narration_event(
    db: Session,
    row: RealtimeSessionRow,
    narration_id: uuid.UUID,
    now: datetime,
    event_type: str,
    detail: dict[str, Any],
) -> None:
    try:
        from app.ledger import service as ledger_service
        from app.ledger.service import ActivityEvent
    except ImportError:
        return
    try:
        ledger_service.record(
            db,
            ActivityEvent(
                event_type=event_type,
                subsystem="voice",
                status="completed",
                severity="info",
                action=detail.get("action") or event_type,
                occurred_at=now,
                factual_summary="Anlatım sahibin araya girmesiyle duraklatıldı.",
                detail_json=detail,
                source="live",
                source_ref=f"voice_narration:{narration_id}:{now.isoformat()}",
                evidence_refs=[
                    {"kind": "narration_session", "ref": str(narration_id)},
                    {"kind": "realtime_session", "ref": str(row.id)},
                ],
            ),
        )
    except Exception:  # noqa: BLE001 - evidence, not a dependency
        logger.warning("voice_ledger_note_failed", event_type=event_type)


# ---------------------------------------------------------------- continuity


def attach(
    db: Session,
    row: RealtimeSessionRow,
    *,
    owner: SessionContext,
    provider: RealtimeProvider,
    registry: ToolRegistry,
    sideband: SidebandPusher,
    client_kind: str | None = None,
    device_id: uuid.UUID | None = None,
    transport: str | None = None,
    credential_ttl_s: int = 600,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Spec §7: a new client takes over the session. The previous media leg is
    told it is closed (sideband, best effort), the leg moves to the caller's
    owner session, a fresh credential is minted, and the continuity state +
    queued sideband messages are returned."""
    now = utcnow()
    require_live(db, row, now=now, trace_id=trace_id)
    if provider.name != row.provider:
        raise VoiceError(
            VoiceErrorClass.CAPABILITY_MISSING,
            f"session was opened on provider {row.provider!r}, which is no longer selectable",
            provider=provider.name,
        )
    ctx = dict(row.context_json or {})
    previous = {
        "owner_session_id": str(row.owner_session_id),
        "device_id": str(row.device_id) if row.device_id else None,
        "client_kind": row.client_kind,
    }
    same_leg = row.owner_session_id == owner.session_id
    if not same_leg:
        frame = sideband_frame(
            row.id,
            SB_LEG_CLOSED,
            {"reason": "attached_elsewhere", "new_client_kind": client_kind or owner.client_kind},
        )
        sideband.push(device_id=row.device_id, frame=frame)
        _audit(db, ACTION_LEG_CLOSED, row, trace_id=trace_id, metadata=previous)
    row.owner_session_id = owner.session_id
    row.client_kind = (client_kind or owner.client_kind)[:16]
    row.device_id = device_id if device_id is not None else owner.device_id
    if transport:
        row.transport = transport
    ctx["legs"] = int(ctx.get("legs", 1)) + (0 if same_leg else 1)
    ctx["network"] = "restored" if ctx.get("network") == "lost" else ctx.get("network")
    pending = list(ctx.get("pending_sideband") or [])
    ctx["pending_sideband"] = []
    _set_context(row, ctx)
    _touch(row, now)
    prefs = voice_service.load_preferences(db)
    config = _session_config(row, registry=registry, prefs=prefs)
    credential = mint_credential(
        provider,
        session_id=row.id,
        ttl_s=credential_ttl_s,
        transport=row.transport,
        session_config=config,
    )
    _audit(
        db,
        ACTION_CREDENTIAL_MINTED,
        row,
        trace_id=trace_id,
        metadata={
            "provider": credential.provider,
            "session_ref": credential.session_ref,
            "expires_at": _iso(credential.expires_at),
            "ttl_s": credential_ttl_s,
            "leg": ctx["legs"],
        },
    )
    _audit(
        db,
        ACTION_SESSION_ATTACHED,
        row,
        trace_id=trace_id,
        metadata={
            "owner_session_id": str(owner.session_id),
            "client_kind": row.client_kind,
            "device_id": str(row.device_id) if row.device_id else None,
            "same_leg": same_leg,
            "legs": ctx["legs"],
            "pending_sideband": len(pending),
        },
    )
    db.commit()
    _ledger(db, "attached", row, trace_id=trace_id, detail={"same_leg": same_leg})
    payload = _leg_payload(row, credential, registry=registry, config=config)
    payload["state"] = session_state(db, row)
    payload["pending_sideband"] = pending
    payload["previous_leg"] = previous if not same_leg else None
    return payload


def close_session(
    db: Session,
    row: RealtimeSessionRow,
    *,
    reason: str = "client_closed",
    trace_id: str | None = None,
) -> dict[str, Any]:
    now = utcnow()
    if row.state not in (REALTIME_STATE_CLOSED, REALTIME_STATE_EXPIRED):
        row.state = REALTIME_STATE_CLOSED
        row.closed_at = now
        row.updated_at = now
        created = _aware(row.created_at, now)
        _audit(
            db,
            ACTION_SESSION_CLOSED,
            row,
            trace_id=trace_id,
            metadata={
                "reason": reason[:64],
                "lifetime_ms": int((now - created).total_seconds() * 1000),
                "barge_in_count": int((row.context_json or {}).get("barge_in_count", 0)),
            },
        )
        snapshot_benchmark_at_close(db, row)
        db.commit()
        _ledger(db, "closed", row, trace_id=trace_id, detail={"reason": reason[:64]})
        _publish_session_over(row, reason=reason)
    return {"session_id": str(row.id), "state": row.state, "closed_at": _iso(row.closed_at)}


def _publish_session_over(row: RealtimeSessionRow, *, reason: str) -> None:
    """A session that is over says so on the UI-state bus.

    Until 2026-09-06 nothing was published at close or expiry, so the last
    ``agent.listening`` of a closed session stayed the bus's current event: the
    owner's Core reported "listening" with no live session, and the M18 eye
    qualification read that stale state at startup (owner run, session
    a71096ca). ``agent.idle`` with this session's id lets the Core - and any
    harness - tell "over" from "listening", and lets a fresh session's first
    event be recognised as fresh.
    """
    publish_ui(
        UiState.IDLE,
        subsystem="voice",
        intensity=0.0,
        session_id=str(row.id),
        status=row.state,
        label=reason[:64] if reason else None,
        metadata={"session_over": True},
    )


# ---------------------------------------------------------------- benchmark


def client_timing_rows(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    from app.broker.models import AuditEvent

    rows = db.execute(
        select(AuditEvent)
        .where(
            AuditEvent.category == AUDIT_CATEGORY,
            AuditEvent.action == ACTION_CLIENT_EVENT,
            AuditEvent.subject_ref == str(session_id),
        )
        .order_by(AuditEvent.id)
    ).scalars()
    return [dict(r.metadata_json or {}) for r in rows]


def _result_field(result: Any, *path: str) -> Any:
    node = result
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def session_activity(db: Session, row: RealtimeSessionRow) -> dict[str, Any]:
    """What happened in a session, from durable rows only (M16 spec §5).

    Tool calls come from ``realtime_tool_calls`` (name, status, and a bounded view of the
    result: level, intent, action, narration state, cursor, how much speech was handed
    out), intents from the ``voice_intent_resolved`` audit rows, client events from the
    ``voice_client_event`` rows (kinds and order only). No transcript, no credential.
    """
    from app.broker.models import AuditEvent

    # Rows are ordered by the audit trail (a monotonic id), not by created_at: several
    # calls can share one timestamp at the database's resolution, and the order the owner
    # spoke them in is part of the evidence.
    order_rows = db.execute(
        select(AuditEvent)
        .where(
            AuditEvent.category == AUDIT_CATEGORY,
            AuditEvent.subject_ref == str(row.id),
            AuditEvent.action == ACTION_TOOL_CALL,
        )
        .order_by(AuditEvent.id)
    ).scalars()
    order = {str((a.metadata_json or {}).get("call_id")): n for n, a in enumerate(order_rows)}
    calls = sorted(
        db.execute(select(RealtimeToolCall).where(RealtimeToolCall.session_id == row.id)).scalars(),
        key=lambda c: (order.get(c.call_id, len(order)), c.created_at or utcnow()),
    )
    tool_calls: list[dict[str, Any]] = []
    for call in calls:
        result = call.result_json or {}
        speech = str(_result_field(result, "speech") or "")
        tool_calls.append(
            {
                "call_id": call.call_id,
                "name": call.name,
                "status": call.status,
                "created_at": _iso(call.created_at) if call.created_at else None,
                "completed_at": _iso(call.completed_at) if call.completed_at else None,
                # A refused/failed ACTION is a succeeded tool call carrying a receipt whose
                # error_class names why (contract §5.5); the harness reads it from here.
                "error_class": call.error_class or _result_field(result, "error_class"),
                "level": _result_field(result, "level"),
                "intent": _result_field(result, "intent", "intent"),
                # The cognition block first: it is written by the engine that dispatched
                # the question. A state.now answer carries its query_kind/subsystem at the
                # top level (contract §4). The narration intent's query_kind is the last
                # fallback and is None for a question (it resolves narration CONTROLS),
                # which is why the owner's M17 run recorded five correct answers with an
                # empty query_kind and the harness declared four subsystems unreached
                # (2026-09-05).
                "query_kind": (
                    _result_field(result, "cognition", "query_kind")
                    or _result_field(result, "query_kind")
                    or _result_field(result, "intent", "query_kind")
                ),
                "subsystem": (
                    _result_field(result, "cognition", "subsystem")
                    or _result_field(result, "subsystem")
                ),
                # The receipt fields of an action (contract §5.5): what was targeted, what
                # the read-back said, whether anything was written.
                "capability": _result_field(result, "capability"),
                "terminal_status": _result_field(result, "terminal_status"),
                "execution_status": _result_field(result, "execution_status"),
                "requested_state": _result_field(result, "requested_state"),
                "routed": _result_field(result, "routed"),
                "entity_ids": _result_field(result, "cognition", "entity_ids"),
                "evidence_kinds": _result_field(result, "cognition", "evidence_kinds"),
                "action": _result_field(result, "narration", "action"),
                "narration_state": _result_field(result, "narration", "narration_state"),
                "cursor": (
                    _result_field(result, "narration", "cursor") or _result_field(result, "cursor")
                ),
                "speech_chars": len(speech),
                "speech_head": speech[:80],
                "facts": _result_field(result, "facts"),
                "uncertainties": _result_field(result, "uncertainties"),
                "evidence_count": _result_field(result, "evidence_count"),
                # structural provenance: which ledger events, which research job and which
                # numbers the spoken sentences rest on (never the wording itself)
                "provenance": _result_field(result, "provenance"),
                "narration_session_id": _result_field(result, "narration_session_id"),
            }
        )
    audits = db.execute(
        select(AuditEvent)
        .where(
            AuditEvent.category == AUDIT_CATEGORY,
            AuditEvent.subject_ref == str(row.id),
            AuditEvent.action.in_((ACTION_INTENT_RESOLVED, ACTION_CLIENT_EVENT)),
        )
        .order_by(AuditEvent.id)
    ).scalars()
    intents: list[dict[str, Any]] = []
    client_events: list[dict[str, Any]] = []
    for audit in audits:
        meta = dict(audit.metadata_json or {})
        at = _iso(audit.created_at) if getattr(audit, "created_at", None) else None
        if audit.action == ACTION_INTENT_RESOLVED:
            intents.append(
                {
                    "at": at,
                    "t_ms": meta.get("t_ms"),
                    "intent": meta.get("intent"),
                    "scope": meta.get("scope"),
                    "chars": meta.get("chars"),
                    "query_kind": meta.get("query_kind"),
                    "klass": meta.get("klass"),
                    "capability": meta.get("capability"),
                    "eye_disable": meta.get("eye_disable"),
                }
            )
        else:
            entry = {"at": at, "t_ms": meta.get("t_ms"), "kind": meta.get("kind")}
            for key in ("chars", "final", "aligned", "spoken_chunks", "complete", "action"):
                if key in meta:
                    entry[key] = meta[key]
            client_events.append(entry)
    return {
        "session_id": str(row.id),
        "state": row.state,
        "tool_calls": tool_calls,
        "intents": intents,
        "client_events": client_events,
        "narration": session_state(db, row).get("narration"),
        "barge_in_count": int((row.context_json or {}).get("barge_in_count", 0)),
        # the interruption policy's own evidence (M16): what was heard while the assistant
        # spoke and what was decided, cumulative, numbers only
        "noise": noise_summary(client_timing_rows(db, row.id)),
    }


NOISE_COUNTERS = (
    "false_starts",
    "false_barge_ins",
    "false_turns",
    "gate_opens",
    # M16 two-lane interruption policy (owner UX result 2026-09-04): what the client heard
    # while the assistant spoke and what it decided, so background speech that was ignored
    # and owner speech that was honoured are both in the evidence.
    "speech_detected",
    "potential_barge_in",
    "accepted_owner_interruption",
    "rejected_background_speech",
    "explicit_stop_command",
    "false_interruption",
)

#: Sub-phase numbers the client reports as payload on EXISTING timing kinds (ADR-0047),
#: so the five headline metrics can be decomposed without new event kinds:
#:   mic_speech_start:    gate_ms, capture_lag_ms
#:   uplink_first_packet: rtp_ms, provider_ms, basis (1 = RTP stats, 0 = provider fallback)
#:   barge_in_start:      detect_ms, stop_command_ms, gain_zero_ms, anomaly (1 = flagged)
#:   first_audio:         response_created_ms, first_delta_ms, playback_ms
BREAKDOWN_FIELDS: dict[str, tuple[str, ...]] = {
    "mic_speech_start": ("gate_ms", "capture_lag_ms"),
    "uplink_first_packet": ("rtp_ms", "provider_ms"),
    "barge_in_start": ("detect_ms", "stop_command_ms", "gain_zero_ms"),
    "first_audio": ("response_created_ms", "first_delta_ms", "playback_ms"),
}
BREAKDOWN_FLAGS: dict[str, tuple[str, ...]] = {
    "uplink_first_packet": ("basis",),
    "barge_in_start": ("anomaly",),
    # first_audio: basis 1 = first audible local sample, 0 = provider-mark fallback
    "first_audio": ("basis",),
}


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
    return float(ordered[index])


def timing_breakdown(client_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per sub-phase: n, p50, p95 (ms) from the client's payload numbers, plus the
    per-kind flag counts (RTP-measured vs fallback uplinks, flagged barge-in samples)
    and how many timing events carried no breakdown at all. Numbers only."""
    samples: dict[str, dict[str, list[float]]] = {
        kind: {f: [] for f in fields} for kind, fields in BREAKDOWN_FIELDS.items()
    }
    flags: dict[str, dict[str, int]] = {
        kind: {f: 0 for f in fields} for kind, fields in BREAKDOWN_FLAGS.items()
    }
    seen: dict[str, int] = {kind: 0 for kind in BREAKDOWN_FIELDS}
    without: dict[str, int] = {kind: 0 for kind in BREAKDOWN_FIELDS}
    for meta in client_rows:
        kind = meta.get("kind")
        if kind not in BREAKDOWN_FIELDS:
            continue
        seen[kind] += 1
        payload = meta.get("payload") or {}
        if not isinstance(payload, dict):
            without[kind] += 1
            continue
        got_any = False
        for field_name in BREAKDOWN_FIELDS[kind]:
            value = payload.get(field_name)
            if isinstance(value, int | float) and not isinstance(value, bool):
                samples[kind][field_name].append(float(value))
                got_any = True
        for flag in BREAKDOWN_FLAGS.get(kind, ()):
            value = payload.get(flag)
            if isinstance(value, int | float) and value:
                flags[kind][flag] += 1
        if not got_any:
            without[kind] += 1
    out: dict[str, Any] = {}
    for kind, fields in BREAKDOWN_FIELDS.items():
        entry: dict[str, Any] = {"events": seen[kind], "without_breakdown": without[kind]}
        for field_name in fields:
            values = samples[kind][field_name]
            entry[field_name] = {
                "n": len(values),
                "p50_ms": round(_percentile(values, 0.5), 1),
                "p95_ms": round(_percentile(values, 0.95), 1),
            }
        for flag in BREAKDOWN_FLAGS.get(kind, ()):
            entry[flag + "_count"] = flags[kind][flag]
        out[kind] = entry
    return out


def noise_summary(client_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The microphone/noise evidence for QUALIFICATION rows 6.16-6.21 (ADR-0044).

    The client reports CUMULATIVE counters in ``state`` events flagged
    ``mic_metrics: 1`` and each calibration as ``mic_calibration: 1``; the last
    counters row is the session total, the last calibration is the one in force.
    Numbers only, by construction on both sides."""
    counters: dict[str, Any] = {name: 0 for name in NOISE_COUNTERS}
    last_metrics: dict[str, Any] | None = None
    last_calibration: dict[str, Any] | None = None
    calibrations = 0
    for meta in client_rows:
        if meta.get("kind") != "state":
            continue
        payload = meta.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("mic_metrics"):
            last_metrics = payload
        if payload.get("mic_calibration"):
            calibrations += 1
            last_calibration = payload
    if last_metrics is not None:
        for name in NOISE_COUNTERS:
            value = last_metrics.get(name)
            if isinstance(value, int | float):
                counters[name] = int(value)
    calibration = {k: v for k, v in (last_calibration or {}).items() if k != "mic_calibration"}
    # "measured zero" vs "not measured": a calibration counts as measured only when the
    # client says so (measured: 1 with samples > 0); anything else is reported as such.
    measured = bool(calibration.get("measured")) and float(calibration.get("samples") or 0) > 0
    return {
        "reported": last_metrics is not None,
        **counters,
        "calibrations": calibrations,
        "calibration_measured": measured,
        "calibration": calibration,
        "metrics": {k: v for k, v in (last_metrics or {}).items() if k != "mic_metrics"},
    }


def benchmark_report(db: Session, row: RealtimeSessionRow) -> RealtimeBenchReport:
    """The five metrics from the CLIENT's reported timestamps (spec §8):
    acceptance evidence when the client is the owner's real machine. The
    ``noise`` block (ADR-0044) carries the session's false-start/turn counters
    and the calibration in force, so the noise matrix is read from the same
    document as the latency metrics."""
    rows = client_timing_rows(db, row.id)
    events = events_from_client_reports(rows)
    return build_report(
        events,
        source=SOURCE_CLIENT,
        context={
            "session_id": str(row.id),
            "provider": row.provider,
            "model": (row.context_json or {}).get("model"),
            "transport": row.transport,
            "client_kind": row.client_kind,
            "voice": (row.context_json or {}).get("voice"),
            "voice_profile": (row.context_json or {}).get("voice_profile"),
            "state": row.state,
            "started_at": _iso(row.created_at) if getattr(row, "created_at", None) else None,
            "ended_at": _iso(row.closed_at) if row.closed_at else None,
            "benchmark_snapshot_at_close": bool(
                (row.context_json or {}).get(BENCHMARK_SNAPSHOT_KEY)
            ),
            "noise": noise_summary(rows),
            "breakdown": timing_breakdown(rows),
        },
    )


BENCHMARK_SNAPSHOT_KEY = "benchmark_at_close"


def snapshot_benchmark_at_close(db: Session, row: RealtimeSessionRow) -> None:
    """Persist the evidence record on the session row itself when it closes.

    The report is always recomputable from the durable audit rows, but a snapshot
    taken at close time survives later changes to the report code and makes the
    row self-describing: ids, provider, model, voice, profile, timestamps, the
    five latency metrics, barge-in and noise counters. Never audio, never a
    credential; the provider's ephemeral secret is not part of any of it."""
    report = benchmark_report(db, row).to_dict()
    report.pop("events", None)
    ctx = dict(row.context_json or {})
    ctx[BENCHMARK_SNAPSHOT_KEY] = report
    _set_context(row, ctx)


def list_recent_sessions(db: Session, *, limit: int = 20) -> list[dict[str, Any]]:
    """Newest first: what an owner needs to pick a session without transcribing a UUID."""
    rows = db.execute(
        select(RealtimeSessionRow)
        .order_by(RealtimeSessionRow.created_at.desc(), RealtimeSessionRow.updated_at.desc())
        .limit(limit)
    ).scalars()
    out: list[dict[str, Any]] = []
    for row in rows:
        ctx = row.context_json or {}
        out.append(
            {
                "session_id": str(row.id),
                "state": row.state,
                "provider": row.provider,
                "model": ctx.get("model"),
                "voice": ctx.get("voice"),
                "voice_profile": ctx.get("voice_profile"),
                "client_kind": row.client_kind,
                "started_at": _iso(row.created_at) if getattr(row, "created_at", None) else None,
                "ended_at": _iso(row.closed_at) if row.closed_at else None,
                "benchmark_snapshot_at_close": bool(ctx.get(BENCHMARK_SNAPSHOT_KEY)),
            }
        )
    return out


__all__ = [
    "ACTION_CLIENT_EVENT",
    "ACTION_CREDENTIAL_MINTED",
    "ACTION_INTENT_RESOLVED",
    "ACTION_LEG_CLOSED",
    "ACTION_SESSION_ATTACHED",
    "ACTION_SESSION_CLOSED",
    "ACTION_SESSION_CREATED",
    "ACTION_SESSION_EXPIRED",
    "ACTION_SIDEBAND_PUSHED",
    "ACTION_SIDEBAND_QUEUED",
    "ACTION_TOOL_CALL",
    "ACTION_TOOL_CALL_REPLAYED",
    "ACTION_TOOL_COMPLETED",
    "AUDIT_CATEGORY",
    "CLIENT_EVENT_KINDS",
    "attach",
    "benchmark_report",
    "client_timing_rows",
    "close_session",
    "complete_tool_call",
    "create_session",
    "drain_pending_sideband",
    "get_session",
    "get_tool_call",
    "handle_tool_call",
    "list_recent_sessions",
    "noise_summary",
    "snapshot_benchmark_at_close",
    "timing_breakdown",
    "record_client_events",
    "require_leg",
    "require_live",
    "scrub_metadata",
    "session_state",
]
