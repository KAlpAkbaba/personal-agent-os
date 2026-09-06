"""The grounded action and live-state tools (docs/M18_ACTION_CONTRACT.md §2, §4, §5).

``eye.enable`` / ``eye.disable`` / ``release.promote`` are ACTIONS: each returns an
:class:`app.actions.receipt.ActionReceipt` built from what the handler READ BACK after
acting - the durable flag re-read from the ledger, the client's own report of its
camera - and the model reads the receipt's ``speech`` verbatim. ``state.now`` is the
QUERY tool for "what is true now"; it delegates to :func:`app.state.now.compose_live_state`.

Handlers here take the same ``(ToolContext, arguments)`` shape as the rest of the
registry (``app.voice.realtime_sessions.tools``); they live in their own module because
that one is the manifest and this one is the WRITE -> READ-BACK -> SPEAK discipline.
None of them is long-running and none has a preamble: one round trip, then the answer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_FAILED,
    EXECUTION_NOOP,
    EXECUTION_REFUSED,
    RELEASE_PROMOTE_REFUSED_SPEECH,
    TERMINAL_ALREADY,
    TERMINAL_FAILED,
    TERMINAL_UNVERIFIED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    eye_speech,
    record_receipt,
)
from app.explain.classify import QUERY_EYE_STATE, classify
from app.ledger.vocabulary import SUBSYSTEM_DEPLOYMENT, SUBSYSTEM_PRESENCE
from app.logging import get_logger
from app.state.now import SCOPE_ALL, SCOPE_EYE, SCOPES, compose_live_state, record_state_answered
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.intents import Intent, resolve_intent

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext

logger = get_logger("app.voice.realtime_sessions.actions")

CAPABILITY_EYE_ENABLE: Final = "eye.enable"
CAPABILITY_EYE_DISABLE: Final = "eye.disable"
CAPABILITY_RELEASE_PROMOTE: Final = "release.promote"
TOOL_STATE_NOW: Final = "state.now"

LOCAL_ACTIVE: Final = "ACTIVE"
LOCAL_DISABLED: Final = "DISABLED"
LOCAL_ERROR: Final = "ERROR"
LOCAL_STATES: Final[tuple[str, ...]] = (LOCAL_ACTIVE, LOCAL_DISABLED, LOCAL_ERROR)

ERROR_CAPABILITY_MISSING: Final = "capability_missing"
ERROR_STATE_MISMATCH: Final = "state_mismatch"
ERROR_OWNER_AUTHORIZATION_REQUIRED: Final = "owner_authorization_required"

#: How long after the deterministic safety net disabled the eye a following
#: ``eye.disable`` tool call still counts as THE command that closed it (contract §5.3).
SAFETY_NET_WINDOW_S: Final = 30.0


def _require_str(arguments: dict[str, Any], key: str, *, max_len: int) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR, f"argument {key!r} must be a non-empty string"
        )
    return value.strip()[:max_len]


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _action_id(ctx: ToolContext) -> str:
    return ctx.call_id or str(uuid.uuid4())


# ------------------------------------------------------------------ the eye


def local_report(arguments: dict[str, Any]) -> dict[str, Any]:
    """The client's ``observed_after.local`` (contract §5.1), normalised.

    A client with no eye capability at all relays without ``observed_after``; that is
    reported as ``ERROR`` / ``capability_missing`` - the server never sets the durable
    flag on enable for a camera nobody opened, and a disable is not called verified when
    nothing local confirmed the loop stopped.
    """
    raw = arguments.get("observed_after")
    local = raw.get("local") if isinstance(raw, dict) else None
    if not isinstance(local, dict):
        return {
            "state": LOCAL_ERROR,
            "running": False,
            "camera_label": None,
            "error_class": ERROR_CAPABILITY_MISSING,
            "observed_at": None,
            "changed": False,
        }
    state = str(local.get("state") or LOCAL_ERROR).upper()
    if state not in LOCAL_STATES:
        state = LOCAL_ERROR

    def _text(key: str, limit: int) -> str | None:
        value = local.get(key)
        return str(value)[:limit] if isinstance(value, str) and value else None

    return {
        "state": state,
        "running": bool(local.get("running")),
        "camera_label": _text("camera_label", 64),
        "error_class": _text("error_class", 64),
        "observed_at": _text("observed_at", 40),
        "changed": bool(local.get("changed")),
    }


def _safety_net_changed_recently(ctx: ToolContext) -> bool:
    """Did ``record_client_events``' deterministic disable (contract §5.3) close the eye
    in this same turn, or within the last thirty seconds? Then this tool call IS the
    command that closed it, and "already disabled" would be the wrong receipt."""
    note = ctx.context.get("eye_safety")
    if not isinstance(note, dict) or note.get("action") != "disable" or not note.get("changed"):
        return False
    raw = note.get("applied_at")
    if not isinstance(raw, str):
        return False
    try:
        applied = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if applied.tzinfo is None:
        applied = applied.replace(tzinfo=UTC)
    return abs((ctx.now - applied).total_seconds()) <= SAFETY_NET_WINDOW_S


def _eye_action(ctx: ToolContext, arguments: dict[str, Any], *, enable: bool) -> dict[str, Any]:
    from app.presence.eye import disable_eye, enable_eye, is_eye_enabled, latest_eye_event

    capability = CAPABILITY_EYE_ENABLE if enable else CAPABILITY_EYE_DISABLE
    db = _require_db(ctx, capability)
    utterance = _require_str(arguments, "utterance", max_len=1000)
    started = ctx.now
    resolved = resolve_intent(utterance, session_state=ctx.fsm_state)
    matched = (
        resolved.matched
        if resolved.intent in (Intent.EYE_ENABLE, Intent.EYE_DISABLE)
        else utterance[:64]
    )
    reason = f"voice:{matched}"
    local = local_report(arguments)

    # WRITE (idempotent, contract §5.4). Enable only when the camera actually opened:
    # "never tell the Cloud Core perception is on before the camera actually opened".
    was_enabled = is_eye_enabled(db)
    changed = False
    if enable:
        if local["state"] == LOCAL_ACTIVE:
            changed = bool(enable_eye(db, reason=reason))
    else:
        changed = bool(disable_eye(db, reason=reason))

    # READ-BACK.
    now_enabled = is_eye_enabled(db)
    server_matches = now_enabled == enable
    local_matches = local["state"] == (LOCAL_ACTIVE if enable else LOCAL_DISABLED)
    safety_net = (not enable) and _safety_net_changed_recently(ctx)
    changed_now = changed or safety_net or bool(local["changed"])

    error_class: str | None = None
    if local["state"] == LOCAL_ERROR:
        terminal = TERMINAL_FAILED
        execution = EXECUTION_EXECUTED if changed else EXECUTION_FAILED
        error_class = local["error_class"] or ERROR_CAPABILITY_MISSING
    elif server_matches and local_matches and changed_now:
        terminal = TERMINAL_VERIFIED
        execution = EXECUTION_EXECUTED
    elif server_matches and local_matches:
        terminal = TERMINAL_ALREADY
        execution = EXECUTION_NOOP
    else:
        terminal = TERMINAL_UNVERIFIED
        execution = EXECUTION_EXECUTED if changed else EXECUTION_NOOP
        error_class = ERROR_STATE_MISMATCH

    evidence: list[dict[str, Any]] = []
    set_by = latest_eye_event(db)
    if set_by is not None:
        evidence.append({"kind": "ledger_event", "ref": str(set_by.event_id)})
    evidence.append({"kind": "realtime_session", "ref": str(ctx.session_id)})

    receipt = ActionReceipt(
        action_id=_action_id(ctx),
        capability=capability,
        requested_state="active" if enable else "disabled",
        execution_status=execution,
        terminal_status=terminal,
        observed_after={
            "server": {
                "eye_enabled": now_enabled,
                "was_enabled": was_enabled,
                "changed": changed,
                "safety_net": safety_net,
            },
            "local": local,
        },
        evidence_refs=evidence,
        error_class=error_class,
        speech=eye_speech(enable=enable, terminal_status=terminal, error_class=error_class),
        started_at=started,
        completed_at=datetime.now(UTC),
    )
    record_receipt(db, receipt, SUBSYSTEM_PRESENCE)
    ctx.context["last_intent"] = resolved.intent.value
    ctx.context["last_action"] = {
        "capability": capability,
        "terminal_status": terminal,
        "execution_status": execution,
    }
    return receipt.as_dict()


def eye_enable(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    return _eye_action(ctx, arguments, enable=True)


def eye_disable(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    return _eye_action(ctx, arguments, enable=False)


# ------------------------------------------------------------------ release


def release_promote(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Canlıya al." by voice: always refused (contract §2), and still a recorded action.

    Production authority is the owner's, minted only by an authenticated owner command
    through the Approval Center (ADR-0055); a spoken imperative is not that command. The
    refusal is a receipt - ``refused`` / ``owner_authorization_required`` - so the ledger
    shows the owner asked and the system declined, rather than nothing at all.
    """
    utterance = _require_str(arguments, "utterance", max_len=1000)
    resolved = resolve_intent(utterance, session_state=ctx.fsm_state)
    receipt = ActionReceipt(
        action_id=_action_id(ctx),
        capability=CAPABILITY_RELEASE_PROMOTE,
        requested_state="promoted",
        execution_status=EXECUTION_REFUSED,
        terminal_status=TERMINAL_FAILED,
        observed_after={
            "server": {"promoted": False, "authority": "owner_only", "by_voice": False},
            "local": {},
        },
        evidence_refs=[{"kind": "realtime_session", "ref": str(ctx.session_id)}],
        error_class=ERROR_OWNER_AUTHORIZATION_REQUIRED,
        speech=RELEASE_PROMOTE_REFUSED_SPEECH,
        started_at=ctx.now,
        completed_at=datetime.now(UTC),
    )
    if ctx.db is not None:
        record_receipt(ctx.db, receipt, SUBSYSTEM_DEPLOYMENT)
    ctx.context["last_intent"] = resolved.intent.value
    ctx.context["last_action"] = {
        "capability": CAPABILITY_RELEASE_PROMOTE,
        "terminal_status": TERMINAL_FAILED,
        "execution_status": EXECUTION_REFUSED,
    }
    return receipt.as_dict()


# ------------------------------------------------------------------ state.now


def _scope_for(question: str, ctx: ToolContext) -> str:
    resolved = resolve_intent(question, session_state=ctx.fsm_state)
    kind = resolved.query_kind
    if kind is None:
        query = classify(question)
        kind = query.kind if query.matched else None
    return SCOPE_EYE if kind == QUERY_EYE_STATE else SCOPE_ALL


def state_now(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """CURRENT STATE, from the live runtime (contract §4). ``scope`` narrows the answer
    to one subsystem; without it the question decides ("kamera açık mı" -> eye)."""
    db = _require_db(ctx, TOOL_STATE_NOW)
    question = _require_str(arguments, "question", max_len=500)
    scope = arguments.get("scope")
    if scope is not None and scope not in SCOPES:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR, f"scope must be one of {', '.join(SCOPES)}"
        )
    if scope is None:
        scope = _scope_for(question, ctx)
    presence_runtime = ctx.live.get("presence_runtime")
    if presence_runtime is None:
        from app.presence.engine import get_engine

        presence_runtime = get_engine()
    result = compose_live_state(
        db,
        scope=str(scope),
        session_id=ctx.session_id,
        now=ctx.now,
        presence_runtime=presence_runtime,
        broker_runtime=ctx.live.get("broker_runtime"),
        health=ctx.live.get("health"),
    )
    record_state_answered(db, result, session_id=ctx.session_id, now=ctx.now)
    ctx.context["last_intent"] = "explain"
    return result


__all__ = [
    "CAPABILITY_EYE_DISABLE",
    "CAPABILITY_EYE_ENABLE",
    "CAPABILITY_RELEASE_PROMOTE",
    "ERROR_CAPABILITY_MISSING",
    "ERROR_OWNER_AUTHORIZATION_REQUIRED",
    "ERROR_STATE_MISMATCH",
    "SAFETY_NET_WINDOW_S",
    "TOOL_STATE_NOW",
    "eye_disable",
    "eye_enable",
    "local_report",
    "release_promote",
    "state_now",
]
