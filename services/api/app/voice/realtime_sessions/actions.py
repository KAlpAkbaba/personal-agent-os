"""The grounded action and live-state tools (docs/M18_ACTION_CONTRACT.md §2, §4, §5).

``eye.enable`` / ``eye.disable`` / ``release.promote`` are ACTIONS: each returns an
:class:`app.actions.receipt.ActionReceipt` built from what the handler READ BACK after
acting - the durable flag re-read from the ledger, the client's own report of its
camera - and the model reads the receipt's ``speech`` verbatim. ``state.now`` is the
QUERY tool for "what is true now"; it delegates to :func:`app.state.now.compose_live_state`.

The tool is the ONE canonical mutation path for the eye (contract §5.3). Nothing else on
the Cloud Core changes the durable flag on the owner's voice: on 2026-09-06 (owner
session 3eb6fee7) a second, hidden path - an utterance hook in ``record_client_events`` -
closed the camera 7 ms after the tool call with no receipt of its own, while every
receipt said ``failed``. There is no such hook any more; a mutation without a receipt is
the defect, not a safety margin.

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
    ERROR_CAPABILITY_MISSING,
    ERROR_STREAM_CREATED_BUT_TRACK_ENDED,
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
    bound_action_trace,
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

TRACK_LIVE: Final = "live"
TRACK_ENDED: Final = "ended"
TRACK_STATES: Final[tuple[str, ...]] = (TRACK_LIVE, TRACK_ENDED)

#: Server-side error classes (the client's own are in ``app.actions.receipt``).
ERROR_STATE_MISMATCH: Final = "state_mismatch"  # local and server read-backs disagree
ERROR_DURABLE_WRITE_FAILED: Final = "durable_write_failed"  # the flag write raised
ERROR_READ_BACK_FAILED: Final = "read_back_failed"  # the flag could not be re-read
ERROR_OWNER_AUTHORIZATION_REQUIRED: Final = "owner_authorization_required"


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

    ``media_track_ready_state`` (``live`` | ``ended`` | None) and ``action_trace`` (a
    bounded list of short step names) are optional evidence; their absence never fails
    the call. The trace is returned under ``action_trace`` for the receipt, not echoed
    inside the local block.
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
            "media_track_ready_state": None,
            "action_trace": [],
        }
    state = str(local.get("state") or LOCAL_ERROR).upper()
    if state not in LOCAL_STATES:
        state = LOCAL_ERROR

    def _text(key: str, limit: int) -> str | None:
        value = local.get(key)
        return str(value)[:limit] if isinstance(value, str) and value else None

    track = _text("media_track_ready_state", 16)
    if track is not None:
        track = track.lower()
        if track not in TRACK_STATES:
            track = None

    return {
        "state": state,
        "running": bool(local.get("running")),
        "camera_label": _text("camera_label", 64),
        "error_class": _text("error_class", 64),
        "observed_at": _text("observed_at", 40),
        "changed": bool(local.get("changed")),
        "media_track_ready_state": track,
        "action_trace": bound_action_trace(local.get("action_trace")),
    }


#: What the turn record calls the two camera intents (``ResolvedIntent.intent.value``).
_EYE_TURN_INTENTS: Final[dict[bool, str]] = {
    True: Intent.EYE_ENABLE.value,
    False: Intent.EYE_DISABLE.value,
}
#: The phrase recorded as the durable reason when the owner's own sentence is not an
#: argument of this call. It names the ACT, never a transcript: the relay keeps no text.
_EYE_MATCHED_TR: Final[dict[bool, str]] = {True: "kamerayı aç", False: "kamerayı kapat"}


def _eye_matched(ctx: ToolContext, arguments: dict[str, Any], *, enable: bool) -> str:
    """The owner's words behind this camera action, for the durable reason.

    The model's path passes ``utterance``. The LOCAL mode (ADR-0173) has no model to write
    one: the deterministic router named the tool from the owner's own sentence and the
    browser posts it with empty arguments, and this used to be refused on the missing
    argument - so "kamerayı aç" did nothing at all in the local mode (owner, 2026-09-20).

    With no ``utterance``, the TURN RECORD is the authority: it is the router's own reading
    of the sentence the owner just said. A turn that did not ask for the camera is not a
    camera command, whatever tool was called, and the missing-argument refusal stands.
    """
    utterance = arguments.get("utterance")
    if isinstance(utterance, str) and utterance.strip():
        said = utterance.strip()[:1000]
        resolved = resolve_intent(said, session_state=ctx.fsm_state)
        if resolved.intent in (Intent.EYE_ENABLE, Intent.EYE_DISABLE):
            return resolved.matched
        return said[:64]
    turn = dict(ctx.context.get("last_utterance") or {})
    if str(turn.get("intent") or "") == _EYE_TURN_INTENTS[enable]:
        return _EYE_MATCHED_TR[enable]
    raise VoiceError(
        VoiceErrorClass.VALIDATION_ERROR,
        "argument 'utterance' must be a non-empty string when this turn did not ask for the camera",
    )


def _eye_action(ctx: ToolContext, arguments: dict[str, Any], *, enable: bool) -> dict[str, Any]:
    from app.presence.eye import disable_eye, enable_eye, is_eye_enabled, latest_eye_event

    capability = CAPABILITY_EYE_ENABLE if enable else CAPABILITY_EYE_DISABLE
    db = _require_db(ctx, capability)
    started = ctx.now
    matched = _eye_matched(ctx, arguments, enable=enable)
    reason = f"voice:{matched}"
    local = local_report(arguments)
    trace = list(local.pop("action_trace"))
    track = local["media_track_ready_state"]

    # What the browser itself says about the camera. An enable whose stream was created
    # and whose track is already ENDED is not an open camera, whatever ``state`` says.
    if enable:
        track_ended = local["state"] == LOCAL_ACTIVE and track == TRACK_ENDED
        physical_ok = local["state"] == LOCAL_ACTIVE and not track_ended
        failed_locally = local["state"] == LOCAL_ERROR or track_ended
    else:
        track_ended = False
        physical_ok = local["state"] == LOCAL_DISABLED
        failed_locally = local["state"] == LOCAL_ERROR

    # WRITE (idempotent, contract §5.4). Enable only when the camera actually opened:
    # "never tell the Cloud Core perception is on before the camera actually opened". A
    # disable is written regardless of what the browser reported: privacy is the server's
    # side too, and the receipt records whether the write happened.
    was_enabled: bool | None
    try:
        was_enabled = bool(is_eye_enabled(db))
    except Exception as exc:  # noqa: BLE001 - reported on the receipt, never hidden
        logger.warning("eye_action_read_before_failed", error=type(exc).__name__)
        was_enabled = None
    changed = False
    write_error: str | None = None
    try:
        # The action's identity rides the durable row (contract §5.5): when the browser's
        # own write already flipped the flag this call changes nothing, and when it did
        # not, the row written here still names the action that caused it.
        if enable:
            if physical_ok:
                changed = bool(
                    enable_eye(
                        db, reason=reason, action_id=ctx.call_id, session_id=str(ctx.session_id)
                    )
                )
        else:
            changed = bool(
                disable_eye(
                    db, reason=reason, action_id=ctx.call_id, session_id=str(ctx.session_id)
                )
            )
    except Exception as exc:  # noqa: BLE001 - the receipt says the record is unverified
        logger.error(
            "eye_action_durable_write_failed",
            capability=capability,
            error=type(exc).__name__,
        )
        write_error = type(exc).__name__
        # No rollback here: the caller flushed this tool call's own row before dispatch
        # and owns the transaction. If the session is poisoned the read-back below fails
        # and the receipt says so.

    # READ-BACK.
    now_enabled: bool | None
    try:
        now_enabled = bool(is_eye_enabled(db))
    except Exception as exc:  # noqa: BLE001 - an unreadable flag is "unverified", not a crash
        logger.warning("eye_action_read_back_failed", error=type(exc).__name__)
        now_enabled = None
    observed_at = datetime.now(UTC)
    server_matches = now_enabled is not None and now_enabled == enable
    changed_now = changed or bool(local["changed"])

    error_class: str | None = None
    if failed_locally:
        terminal = TERMINAL_FAILED
        execution = EXECUTION_EXECUTED if changed else EXECUTION_FAILED
        if track_ended:
            error_class = local["error_class"] or ERROR_STREAM_CREATED_BUT_TRACK_ENDED
        else:
            error_class = local["error_class"] or ERROR_CAPABILITY_MISSING
    elif physical_ok and server_matches and changed_now:
        terminal = TERMINAL_VERIFIED
        execution = EXECUTION_EXECUTED
    elif physical_ok and server_matches:
        terminal = TERMINAL_ALREADY
        execution = EXECUTION_NOOP
    else:
        # The camera did what was asked but the record does not agree (write raised, flag
        # unreadable, or the flag simply disagrees) - or the browser reports the camera
        # still in the OLD state. Both are "unverified"; the speech tells them apart.
        terminal = TERMINAL_UNVERIFIED
        if write_error is not None:
            execution = EXECUTION_FAILED
            error_class = ERROR_DURABLE_WRITE_FAILED
        elif now_enabled is None:
            execution = EXECUTION_EXECUTED if changed else EXECUTION_NOOP
            error_class = ERROR_READ_BACK_FAILED
        else:
            execution = EXECUTION_EXECUTED if changed else EXECUTION_NOOP
            error_class = ERROR_STATE_MISMATCH

    evidence: list[dict[str, Any]] = []
    try:
        set_by = latest_eye_event(db)
    except Exception:  # noqa: BLE001 - evidence, not a dependency
        set_by = None
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
                "write_error": write_error,
            },
            "local": local,
        },
        evidence_refs=evidence,
        error_class=error_class,
        speech=eye_speech(
            enable=enable,
            terminal_status=terminal,
            error_class=error_class,
            physical_ok=physical_ok,
        ),
        started_at=started,
        completed_at=datetime.now(UTC),
        session_id=str(ctx.session_id),
        observed_at=observed_at,
        action_trace=trace,
    )
    record_receipt(db, receipt, SUBSYSTEM_PRESENCE)
    ctx.context["last_intent"] = _EYE_TURN_INTENTS[enable]
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
        session_id=str(ctx.session_id),
        observed_at=ctx.now,
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
    "ERROR_DURABLE_WRITE_FAILED",
    "ERROR_OWNER_AUTHORIZATION_REQUIRED",
    "ERROR_READ_BACK_FAILED",
    "ERROR_STATE_MISMATCH",
    "TOOL_STATE_NOW",
    "TRACK_ENDED",
    "TRACK_LIVE",
    "eye_disable",
    "eye_enable",
    "local_report",
    "release_promote",
    "state_now",
]
