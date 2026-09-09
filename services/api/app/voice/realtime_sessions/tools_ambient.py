"""The alarm, display and ambient tools (M18.3 spec §3.8).

Ten tools, registered from ``tools.default_registry()`` by ONE added line
(:func:`register_ambient_tools`), so the manifest stays a manifest and this file stays the
WRITE -> READ-BACK -> SPEAK discipline for a new family of capabilities — the same split
``app.voice.realtime_sessions.actions`` already makes for the eye.

Every ACTION returns an ``app.actions.receipt.ActionReceipt``-shaped result whose ``speech``
is read verbatim; every QUERY returns ``speech`` plus the facts behind it. The sentences
themselves live in ``app.alarms.speech`` (spec §6's table), so the persona, the receipts
and the REST routes all say the same thing and cannot drift.

What these tools deliberately do NOT do: run a wake sequence, turn a display off directly,
or wait on a device inside the tool call. ``alarm.create`` writes rows and arms a routine;
``ambient.test_display`` arms a moment. The clock does the physical work — which is why a
tool call is fast, why the owner's ten-second display test really gives them ten seconds,
and why a device that is slow to answer can never hold a voice turn open.
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
    TERMINAL_ALREADY,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.alarms import service as alarms_service
from app.alarms import speech as alarm_speech
from app.alarms.state import IllegalAlarmTransition
from app.alarms.tr_time import (
    DEFAULT_TIMEZONE,
    UnparsedWhen,
    parse_when_struct,
    parse_when_text,
)
from app.ambient import service as ambient_service
from app.ambient.policy import OWNER_COMMAND_HOLDOFF_S
from app.ledger.vocabulary import SUBSYSTEM_AMBIENT, SUBSYSTEM_ROUTINE
from app.logging import get_logger
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.realtime_sessions.tools_ambient")

# ------------------------------------------------------------------ the tool names

TOOL_ALARM_CREATE: Final = "alarm.create"
TOOL_ALARM_CANCEL: Final = "alarm.cancel"
TOOL_ALARM_SNOOZE: Final = "alarm.snooze"
TOOL_ALARM_STOP: Final = "alarm.stop"
TOOL_ALARM_STATUS: Final = "alarm.status"
TOOL_DISPLAY_OFF: Final = "display.off"
TOOL_DISPLAY_WAKE: Final = "display.wake"
TOOL_DISPLAY_STATUS: Final = "display.status"
TOOL_AMBIENT_SET_POLICY: Final = "ambient.set_policy"
TOOL_AMBIENT_TEST_DISPLAY: Final = "ambient.test_display"
TOOL_AMBIENT_EXPLAIN: Final = "ambient.explain"

AMBIENT_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_ALARM_CREATE,
    TOOL_ALARM_CANCEL,
    TOOL_ALARM_SNOOZE,
    TOOL_ALARM_STOP,
    TOOL_ALARM_STATUS,
    TOOL_DISPLAY_OFF,
    TOOL_DISPLAY_WAKE,
    TOOL_DISPLAY_STATUS,
    TOOL_AMBIENT_SET_POLICY,
    TOOL_AMBIENT_TEST_DISPLAY,
    TOOL_AMBIENT_EXPLAIN,
)

#: Server-side error classes specific to this family (the naming style of
#: ``app.actions.receipt``'s ERROR_* constants).
ERROR_NO_ALARM: Final = "no_alarm"
ERROR_WHEN_UNPARSED: Final = "when_unparsed"
ERROR_NEEDS_MEDIA_CONFIRMATION: Final = "needs_media_confirmation"
ERROR_NOT_RINGING: Final = "not_ringing"
ERROR_NO_CAPABLE_DEVICE: Final = "no_capable_device"


def _db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _action_id(ctx: ToolContext) -> str:
    return ctx.call_id or str(uuid.uuid4())


def _sequence(ctx: ToolContext) -> Any:
    """The process's wake sequence, injected through ``ToolContext.live`` like every other
    live runtime (docs/M18_ACTION_CONTRACT.md §4). ``None`` in a process with no broker:
    the alarm rows still change, and the receipt says the device was not told."""
    return ctx.live.get("wake_sequence")


def _receipt(
    ctx: ToolContext,
    *,
    capability: str,
    requested_state: str,
    execution: str,
    terminal: str,
    server: dict[str, Any],
    speech: str,
    error_class: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    started: datetime | None = None,
) -> dict[str, Any]:
    """Build, record and return one receipt (docs/M18_ACTION_CONTRACT.md §5.5)."""
    now = datetime.now(UTC)
    receipt = ActionReceipt(
        action_id=_action_id(ctx),
        capability=capability,
        requested_state=requested_state,
        execution_status=execution,
        terminal_status=terminal,
        observed_after={"server": server, "local": {}},
        evidence_refs=(evidence or []) + [{"kind": "realtime_session", "ref": str(ctx.session_id)}],
        error_class=error_class,
        speech=speech,
        started_at=started or ctx.now,
        completed_at=now,
        session_id=str(ctx.session_id),
        observed_at=now,
    )
    ambient = capability.startswith(("display.", "ambient."))
    subsystem = SUBSYSTEM_AMBIENT if ambient else SUBSYSTEM_ROUTINE
    if ctx.db is not None:
        record_receipt(ctx.db, receipt, subsystem)
    return receipt.as_dict()


# ------------------------------------------------------------------------ alarms


def alarm_create(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """"Yarın sabah 07:30'da beni uyandır." / "90 saniye sonra test alarmı kur." (spec §3.8).

    Two refusals worth naming, because both are the system declining to invent something:

    * an unparseable "when" is refused, never rounded to a guess — a wake alarm at the
      wrong hour is not a smaller error than no alarm;
    * a RECURRING alarm whose media is a title with no url is refused outright
      (``needs_media_confirmation``) and NOTHING is created, because a repeating alarm that
      silently rings a tone every weekday instead of the song the owner named is a lie that
      repeats. A ONE-SHOT alarm in the same position IS created, with the tone, and the
      speech asks for the link: the owner still wakes up tomorrow.
    """
    db = _db(ctx, TOOL_ALARM_CREATE)
    started = ctx.now
    timezone = str(arguments.get("timezone") or DEFAULT_TIMEZONE)[:64]
    is_test = bool(arguments.get("test"))
    media = arguments.get("media") if isinstance(arguments.get("media"), dict) else None
    when_spoken = arguments.get("when_spoken")
    when = arguments.get("when") if isinstance(arguments.get("when"), dict) else None

    try:
        if isinstance(when_spoken, str) and when_spoken.strip():
            parsed = parse_when_text(when_spoken, now=ctx.now, timezone=timezone)
        elif when:
            parsed = parse_when_struct(when, now=ctx.now, timezone=timezone)
        else:
            raise UnparsedWhen("no 'when' or 'when_spoken' given")
    except UnparsedWhen as exc:
        return _receipt(
            ctx,
            capability=TOOL_ALARM_CREATE,
            requested_state="scheduled",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"error": str(exc)[:200]},
            speech=alarm_speech.ALARM_CREATE_UNPARSED_TR,
            error_class=ERROR_WHEN_UNPARSED,
            started=started,
        )

    unresolved_media = bool(media) and not media.get("url")
    if parsed.weekdays and unresolved_media:
        return _receipt(
            ctx,
            capability=TOOL_ALARM_CREATE,
            requested_state="scheduled",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"recurring": True, "media": {k: v for k, v in media.items() if k != "url"}},
            speech=alarm_speech.ALARM_CREATE_REFUSED_MEDIA_TR,
            error_class=ERROR_NEEDS_MEDIA_CONFIRMATION,
            started=started,
        )

    alarm = alarms_service.create_alarm(
        db,
        when=parsed,
        media=media,
        is_test=is_test,
        label=str(arguments.get("label") or "")[:200] or None,
    )
    speech = alarm_speech.alarm_created_speech(
        local_time=parsed.local_time,
        relative_seconds=parsed.relative_seconds,
        weekdays=parsed.weekdays,
        tomorrow=parsed.matched == "tomorrow",
        is_test=is_test,
    )
    if unresolved_media:
        speech = alarm_speech.ALARM_CREATE_NEEDS_MEDIA_TR
    return {
        **_receipt(
            ctx,
            capability=TOOL_ALARM_CREATE,
            requested_state="scheduled",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server=alarms_service.alarm_dict(alarm),
            speech=speech,
            evidence=[{"kind": "wake_alarm", "ref": str(alarm.id)}],
            started=started,
        ),
        "alarm": alarms_service.alarm_dict(alarm),
    }


def _target_alarm(db: Any, arguments: dict[str, Any], *, ringing_only: bool = False) -> Any:
    raw = arguments.get("alarm_id")
    if isinstance(raw, str) and raw.strip():
        try:
            return alarms_service.get_alarm(db, uuid.UUID(raw.strip()))
        except (ValueError, AttributeError, TypeError):
            return None
    if ringing_only:
        ringing = alarms_service.alarms_ringing(db)
        return ringing[0] if ringing else None
    return alarms_service.next_alarm(db)


def alarm_cancel(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """"Alarmı iptal et." Defaults to the NEXT scheduled alarm (spec §3.8)."""
    db = _db(ctx, TOOL_ALARM_CANCEL)
    alarm = _target_alarm(db, arguments)
    if alarm is None:
        return _receipt(
            ctx,
            capability=TOOL_ALARM_CANCEL,
            requested_state="cancelled",
            execution=EXECUTION_NOOP,
            terminal=TERMINAL_ALREADY,
            server={"alarms": 0},
            speech=alarm_speech.ALARM_QUERY_NONE_TR,
            error_class=ERROR_NO_ALARM,
        )
    try:
        alarms_service.cancel_alarm(db, alarm.id, sequence=_sequence(ctx), reason="voice")
    except IllegalAlarmTransition:
        return _receipt(
            ctx,
            capability=TOOL_ALARM_CANCEL,
            requested_state="cancelled",
            execution=EXECUTION_NOOP,
            terminal=TERMINAL_ALREADY,
            server=alarms_service.alarm_dict(alarm),
            speech=alarm_speech.ALARM_CANCELLED_TR,
        )
    return _receipt(
        ctx,
        capability=TOOL_ALARM_CANCEL,
        requested_state="cancelled",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        server=alarms_service.alarm_dict(alarm),
        speech=alarm_speech.ALARM_CANCELLED_TR,
        evidence=[{"kind": "wake_alarm", "ref": str(alarm.id)}],
    )


def alarm_stop(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """"Alarmı kapat." Idempotent (spec §3.8): nothing ringing is a truthful, calm answer."""
    db = _db(ctx, TOOL_ALARM_STOP)
    alarm = _target_alarm(db, arguments, ringing_only=True)
    if alarm is None:
        return _receipt(
            ctx,
            capability=TOOL_ALARM_STOP,
            requested_state="stopped",
            execution=EXECUTION_NOOP,
            terminal=TERMINAL_ALREADY,
            server={"ringing": 0},
            speech=alarm_speech.ALARM_NOT_RINGING_TR,
            error_class=ERROR_NOT_RINGING,
        )
    alarms_service.stop_alarm(db, alarm.id, sequence=_sequence(ctx), reason="voice")
    return _receipt(
        ctx,
        capability=TOOL_ALARM_STOP,
        requested_state="stopped",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        server=alarms_service.alarm_dict(alarm),
        speech=alarm_speech.ALARM_STOPPED_TR,
        evidence=[{"kind": "wake_alarm", "ref": str(alarm.id)}],
    )


def alarm_snooze(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """"Beş dakika ertele." Only while the alarm is actually ringing (spec §3.8)."""
    db = _db(ctx, TOOL_ALARM_SNOOZE)
    alarm = _target_alarm(db, arguments, ringing_only=True)
    if alarm is None:
        return _receipt(
            ctx,
            capability=TOOL_ALARM_SNOOZE,
            requested_state="snoozed",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"ringing": 0},
            speech=alarm_speech.ALARM_NOT_RINGING_TR,
            error_class=ERROR_NOT_RINGING,
        )
    raw_minutes = arguments.get("minutes")
    valid_minutes = isinstance(raw_minutes, int) and not isinstance(raw_minutes, bool)
    minutes = int(raw_minutes) if valid_minutes else None
    # What the owner SAID outranks what the model passed (corpus a.snooze.1: "10 dakika
    # ertele" arrived with no minutes argument and snoozed for the default five).
    said = _turn_alarm_minutes(ctx)
    if said is not None:
        minutes = said
    try:
        alarms_service.snooze_alarm(db, alarm.id, minutes=minutes, sequence=_sequence(ctx))
    except alarms_service.InvalidAlarmRequest as exc:
        return _receipt(
            ctx,
            capability=TOOL_ALARM_SNOOZE,
            requested_state="snoozed",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"error": str(exc)[:200]},
            speech=alarm_speech.ALARM_NOT_RINGING_TR,
            error_class=ERROR_NOT_RINGING,
        )
    payload = alarms_service.alarm_dict(alarm)
    return _receipt(
        ctx,
        capability=TOOL_ALARM_SNOOZE,
        requested_state="snoozed",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        server=payload,
        speech=alarm_speech.alarm_snoozed_speech(
            minutes=payload["snooze_minutes"], local_time=payload["local_time"]
        ),
        evidence=[{"kind": "wake_alarm", "ref": str(alarm.id)}],
    )


def alarm_status(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """"Sabah alarmım kaçta?" — a QUERY: ``speech`` plus the facts, and no receipt, because
    nothing mutated (docs/M18_ACTION_CONTRACT.md §2)."""
    del arguments
    db = _db(ctx, TOOL_ALARM_STATUS)
    speech, alarm = alarms_service.status_speech(db, now=ctx.now)
    return {
        "speech": speech,
        "alarm": alarms_service.alarm_dict(alarm) if alarm else None,
        "alarms": [alarms_service.alarm_dict(a) for a in alarms_service.list_alarms(db, limit=20)],
    }


# ------------------------------------------------------------------------ display


def display_off(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """"Ekranları kapat." The one machine-state capability, and only ever the display.

    A device REFUSAL (``recent_input`` / ``alarm_active``) is ``execution_status=refused``
    with its own sentence, and a ``recent_input`` refusal additionally starts the cloud's
    input holdoff — the device just told us the owner is at the keyboard, and continuing to
    ask would be the system arguing with a person (spec §1.3, §3.9).
    """
    db = _db(ctx, TOOL_DISPLAY_OFF)
    sequence = _sequence(ctx)
    reason = str(arguments.get("reason") or "owner_command")[:64]
    if sequence is None:
        return _receipt(
            ctx,
            capability=TOOL_DISPLAY_OFF,
            requested_state="off",
            execution=EXECUTION_FAILED,
            terminal=TERMINAL_FAILED,
            server={"reason": "no_device_runtime"},
            speech=alarm_speech.DISPLAY_OFF_NO_DEVICE_TR,
            error_class=ERROR_NO_CAPABLE_DEVICE,
        )
    # The OWNER asked, so the owner's holdoff - not the automatic policy's 120 s.
    #
    # The device refuses while input is recent, which protects a screen someone is working
    # at. Asking for the screen to go off IS recent input, so sending the automatic holdoff
    # made this command refusable for two minutes after the owner issued it and grantable
    # only if they then sat still. Three attempts on 2026-09-09 were refused at 3.8 s, 67.6 s
    # and 88.8 s idle. The ambient module already knew this - its owner-test path passes the
    # short holdoff with a comment saying exactly why - and this path did not.
    step = sequence.display_off(
        db,
        reason=reason,
        holdoff_s=OWNER_COMMAND_HOLDOFF_S,
        action_id=_action_id(ctx),
        now=ctx.now,
    )
    policy = ambient_service.get_policy(db)
    ambient_service.note_display_refusal(step.reason, policy=policy, now=ctx.now)
    speech = alarm_speech.display_off_speech(
        terminal_status=step.receipt.terminal_status,
        error_class=step.error_class,
        refused=step.reason or None,
    )
    return {**step.receipt.as_dict(), "speech": speech}


def display_wake(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """"Ekranları aç." """
    del arguments
    db = _db(ctx, TOOL_DISPLAY_WAKE)
    sequence = _sequence(ctx)
    if sequence is None:
        return _receipt(
            ctx,
            capability=TOOL_DISPLAY_WAKE,
            requested_state="on",
            execution=EXECUTION_FAILED,
            terminal=TERMINAL_FAILED,
            server={"reason": "no_device_runtime"},
            speech=alarm_speech.DISPLAY_OFF_NO_DEVICE_TR,
            error_class=ERROR_NO_CAPABLE_DEVICE,
        )
    step = sequence.display_wake(
        db, reason="owner_command", action_id=_action_id(ctx), now=ctx.now
    )
    # ADR-0079 §5: an explicit wake starts the owner-command holdoff, so a stale AWAY
    # cannot darken the screens the owner just asked for.
    ambient_service.note_owner_display_command(db, now=ctx.now, reason="display_wake")
    speech = alarm_speech.display_wake_speech(
        terminal_status=step.receipt.terminal_status, error_class=step.error_class
    )
    return {**step.receipt.as_dict(), "speech": speech}


def display_status(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """A QUERY answered from the device's own OBSERVED power state, never from what was
    last asked for."""
    del arguments
    statuses = ctx.live.get("device_statuses")
    state = ambient_service.display_state_summary(statuses=statuses)
    return {
        "speech": alarm_speech.display_status_speech(state),
        "display": state,
        "devices": [
            status.as_dict()
            for status in (statuses.all().values() if statuses is not None else [])
        ],
    }


# ------------------------------------------------------------------------ ambient


_POLICY_ARGUMENTS = (
    "auto_off",
    "off_when_asleep",
    "off_when_away",
    "wake_on_return",
    "keep_on",
)

#: How long the turn's recorded utterance still speaks for a tool call (the same window
#: ``app.voice.realtime_sessions.tools.RESEARCH_TURN_TTL_S`` gives a research turn).
_POLICY_TURN_TTL_S = 600.0


def _turn_policy_changes(ctx: ToolContext) -> dict[str, bool] | None:
    """The policy fields the owner's WORDS set on this turn (ADR-0079 §7), from the record
    the ONE router wrote on the session - never from the model's paraphrase."""
    record = dict(ctx.context.get("last_utterance") or {})
    raw_at = record.get("at")
    if raw_at:
        try:
            at = datetime.fromisoformat(str(raw_at).replace("Z", "+00:00"))
        except ValueError:
            at = None
        if at is not None:
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            if (ctx.now - at).total_seconds() > _POLICY_TURN_TTL_S:
                return None
    changes = record.get("policy_changes")
    if not isinstance(changes, dict) or not changes:
        return None
    return {str(k): bool(v) for k, v in changes.items() if isinstance(v, bool)}


def _turn_alarm_minutes(ctx: ToolContext) -> int | None:
    """The snooze minutes the owner SAID on this turn ("on dakika ertele"), from the
    record the ONE router wrote - preferred over the model's ``minutes`` argument."""
    record = dict(ctx.context.get("last_utterance") or {})
    raw_at = record.get("at")
    if raw_at:
        try:
            at = datetime.fromisoformat(str(raw_at).replace("Z", "+00:00"))
        except ValueError:
            at = None
        if at is not None:
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            if (ctx.now - at).total_seconds() > _POLICY_TURN_TTL_S:
                return None
    minutes = record.get("alarm_minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        return None
    return minutes


#: The sentence for the FIRST preference the change touched, in the order that matters
#: to the owner: keep-on outranks the switch, the switch outranks its halves.
_POLICY_SPEECH: tuple[tuple[str, str, str], ...] = (
    ("keep_on", alarm_speech.AMBIENT_KEEP_ON_TR, alarm_speech.AMBIENT_KEEP_ON_OFF_TR),
    (
        "auto_off_enabled",
        alarm_speech.AMBIENT_AUTO_OFF_ON_TR,
        alarm_speech.AMBIENT_AUTO_OFF_OFF_TR,
    ),
    (
        "off_when_asleep",
        alarm_speech.AMBIENT_OFF_WHEN_ASLEEP_ON_TR,
        alarm_speech.AMBIENT_OFF_WHEN_ASLEEP_OFF_TR,
    ),
    (
        "off_when_away",
        alarm_speech.AMBIENT_OFF_WHEN_AWAY_ON_TR,
        alarm_speech.AMBIENT_OFF_WHEN_AWAY_OFF_TR,
    ),
    (
        "wake_on_return",
        alarm_speech.AMBIENT_WAKE_ON_RETURN_ON_TR,
        alarm_speech.AMBIENT_WAKE_ON_RETURN_OFF_TR,
    ),
)


def _policy_speech(applied: dict[str, Any], requested: dict[str, Any]) -> str:
    """What changed, in the owner's own terms; a repeat that changed nothing still
    confirms the preference that stands ("Siz yokken ekranları kapatmayacağım")."""
    for field_name, on_tr, off_tr in _POLICY_SPEECH:
        if field_name in applied:
            return on_tr if applied[field_name] else off_tr
    for field_name, on_tr, off_tr in _POLICY_SPEECH:
        if field_name in requested and field_name != "auto_off_enabled":
            return on_tr if requested[field_name] else off_tr
    if "auto_off_enabled" in requested:
        return (
            alarm_speech.AMBIENT_AUTO_OFF_ON_TR
            if requested["auto_off_enabled"]
            else alarm_speech.AMBIENT_AUTO_OFF_OFF_TR
        )
    return alarm_speech.AMBIENT_POLICY_UPDATED_TR


def ambient_set_policy(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """"Uyurken ekranları kapat." / "Otomatik ekran kapatmayı aç." (spec §3.8, §6).

    ADR-0079 §7: when the turn's recorded utterance carries the fields the owner's words
    set, THOSE are applied - the model's booleans are only used when no such record
    exists (a client that reports no utterances). "Uyuduğumda ekranları kapatma." is
    therefore never turned into an off by a mistranslated argument.
    """
    db = _db(ctx, TOOL_AMBIENT_SET_POLICY)
    derived = _turn_policy_changes(ctx)
    changes: dict[str, Any] = dict(derived) if derived else {}
    if not changes:
        for name in _POLICY_ARGUMENTS:
            value = arguments.get(name)
            if isinstance(value, bool):
                changes["auto_off_enabled" if name == "auto_off" else name] = value
    if not changes:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"ambient.set_policy needs at least one of {_POLICY_ARGUMENTS}",
        )
    policy, applied = ambient_service.set_policy(db, changes, source="voice", now=ctx.now)
    speech = _policy_speech(applied, changes)
    return _receipt(
        ctx,
        capability=TOOL_AMBIENT_SET_POLICY,
        requested_state="updated",
        execution=EXECUTION_EXECUTED if applied else EXECUTION_NOOP,
        terminal=TERMINAL_VERIFIED if applied else TERMINAL_ALREADY,
        server={
            "policy": policy.as_dict(),
            "changed": applied,
            "requested": changes,
            "derived_from_turn": bool(derived),
        },
        speech=speech,
    )


def ambient_explain(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """"Ekranları neden kapattın?" / "Neden açık bıraktın?" / "Şu an ekran politikası ne?"
    (ADR-0079 §12). A QUERY: the live decision, the presence assertion behind it, the
    holdoffs, the latest input and the latest display receipt - and nothing invented."""
    del arguments
    db = _db(ctx, TOOL_AMBIENT_EXPLAIN)
    facts = ambient_service.explain(db, now=ctx.now)
    return {**facts, "routed": "ambient_policy"}


def ambient_test_display(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """"Ekran uyku otomasyonunu test et." (spec §8.2).

    Arms the moment; the clock issues the real ``display.off`` receipt when it arrives. The
    delay is the point of the test — the owner has to be able to take their hand off the
    keyboard and then wake the screen with it.
    """
    _db(ctx, TOOL_AMBIENT_TEST_DISPLAY)
    raw = arguments.get("delay_seconds")
    delay = (
        int(raw)
        if isinstance(raw, int) and not isinstance(raw, bool)
        else ambient_service.DEFAULT_TEST_DELAY_S
    )
    delay = max(1, min(delay, ambient_service.MAX_TEST_DELAY_S))
    at = ambient_service.schedule_display_test(delay_seconds=delay, now=ctx.now)
    return _receipt(
        ctx,
        capability=TOOL_AMBIENT_TEST_DISPLAY,
        requested_state="scheduled",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        server={"scheduled_at": at.isoformat(), "delay_seconds": delay},
        speech=alarm_speech.AMBIENT_TEST_STARTED_TR.format(seconds=delay),
    )


# ------------------------------------------------------------------ registration

_WHEN_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "relative_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
        "date": {"type": "string", "maxLength": 32},
        "time": {"type": "string", "maxLength": 5},
        "weekdays": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6}},
    },
    "additionalProperties": False,
}

_MEDIA_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "maxLength": 2000},
        "title": {"type": "string", "maxLength": 200},
        "remembered": {"type": "string", "maxLength": 200},
    },
    "additionalProperties": False,
}


def register_ambient_tools(reg: ToolRegistry) -> ToolRegistry:
    """Register all ten tools (module docstring: ONE line in ``default_registry``)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_ALARM_CREATE,
            description=(
                "Uyandırma alarmı KURAR: 'yarın sabah 07:30'da beni uyandır', 'saat 08:00'e "
                "alarm kur', 'her hafta içi 07:15'te beni uyandır', '90 saniye sonra test "
                "alarmı kur' denince HER ZAMAN bu araç çağrılır. Saati sen hesaplama; "
                "sahibin söylediği zaman ifadesini 'when_spoken' alanına aynen ver. "
                "Müzik istenirse bağlantıyı 'media.url', adı 'media.title' olarak ver. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "when_spoken": {"type": "string", "maxLength": 300},
                    "when": _WHEN_SCHEMA,
                    "media": _MEDIA_SCHEMA,
                    "test": {"type": "boolean"},
                    "label": {"type": "string", "maxLength": 200},
                    "timezone": {"type": "string", "maxLength": 64},
                },
                "additionalProperties": False,
            },
            handler=alarm_create,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ALARM_CANCEL,
            description=(
                "Kurulu alarmı İPTAL EDER ('alarmı iptal et'). alarm_id verilmezse sıradaki "
                "alarm iptal edilir. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"alarm_id": {"type": "string", "maxLength": 64}},
                "additionalProperties": False,
            },
            handler=alarm_cancel,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ALARM_SNOOZE,
            description=(
                "Çalan alarmı ERTELER ('beş dakika ertele', 'on dakika ertele'). Varsayılan "
                "beş dakika. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "minutes": {"type": "integer", "minimum": 1, "maximum": 60},
                    "alarm_id": {"type": "string", "maxLength": 64},
                },
                "additionalProperties": False,
            },
            handler=alarm_snooze,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ALARM_STOP,
            description=(
                "Çalan alarmı DURDURUR: 'alarmı kapat', 'alarmı durdur', 'alarmı sustur' "
                "denince HER ZAMAN bu araç çağrılır; sohbetle yanıtlanmaz. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"alarm_id": {"type": "string", "maxLength": 64}},
                "additionalProperties": False,
            },
            handler=alarm_stop,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ALARM_STATUS,
            description=(
                "Kurulu alarmları SÖYLER ('sabah alarmım kaçta', 'alarmım var mı'). "
                "Kayıttan okur, tahmin etmez. Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=alarm_status,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DISPLAY_OFF,
            description=(
                "Ekranları KAPATIR ('ekranları kapat', 'ekranı kapat'). Yalnızca ekran "
                "gücünü kapatır; bilgisayarı uyutmaz, kilitlemez, kapatmaz. Cihaz az önce "
                "klavye kullanıldığı için reddederse bunu olduğu gibi söylersin. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"reason": {"type": "string", "maxLength": 64}},
                "additionalProperties": False,
            },
            handler=display_off,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DISPLAY_WAKE,
            description=(
                "Ekranları AÇAR ('ekranları aç', 'ekranı aç'). Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=display_wake,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DISPLAY_STATUS,
            description=(
                "Ekranların AÇIK mı KAPALI mı olduğunu cihazın kendi bildirdiği duruma "
                "bakarak söyler. Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=display_status,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_AMBIENT_SET_POLICY,
            description=(
                "Otomatik ekran kapatma ayarını değiştirir: 'uyurken ekranları kapat' "
                "(off_when_asleep), 'ben yokken ekranları kapat' (off_when_away), "
                "'otomatik ekran kapatmayı aç/kapat' (auto_off), 'ben geri geldiğimde "
                "ekranı aç' (wake_on_return), 'ekranı açık tut' (keep_on). Olumsuzlar da "
                "ayardır: 'uyuduğumda ekranları kapatma', 'ben yokken ekranları kapatma'. "
                "Sunucu sahibin sözlerinden ayarı kendisi çıkarır. Dönen 'speech' metnini "
                "aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "auto_off": {"type": "boolean"},
                    "off_when_asleep": {"type": "boolean"},
                    "off_when_away": {"type": "boolean"},
                    "wake_on_return": {"type": "boolean"},
                    "keep_on": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            handler=ambient_set_policy,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_AMBIENT_EXPLAIN,
            description=(
                "Ekran otomasyonunu AÇIKLAR ('ekranları neden kapattın', 'neden açık "
                "bıraktın', 'şu an ekran politikası ne'): canlı karar, varlık değerlendirmesi, "
                "bekleme süreleri, son klavye kullanımı ve son ekran işlemi - hepsi kayıttan. "
                "Dönen 'speech' metnini aynen oku; neden uydurma."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=ambient_explain,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_AMBIENT_TEST_DISPLAY,
            description=(
                "Ekran uyku otomasyonunu TEST EDER ('ekran uyku otomasyonunu test et'): "
                "birkaç saniye sonra ekranlar gerçekten kapanır, bir tuşa basınca açılır. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "delay_seconds": {"type": "integer", "minimum": 1, "maximum": 120}
                },
                "additionalProperties": False,
            },
            handler=ambient_test_display,
        )
    )
    return reg


__all__ = [
    "AMBIENT_TOOL_NAMES",
    "ERROR_NEEDS_MEDIA_CONFIRMATION",
    "ERROR_NOT_RINGING",
    "ERROR_NO_ALARM",
    "ERROR_NO_CAPABLE_DEVICE",
    "ERROR_WHEN_UNPARSED",
    "TOOL_ALARM_CANCEL",
    "TOOL_ALARM_CREATE",
    "TOOL_ALARM_SNOOZE",
    "TOOL_ALARM_STATUS",
    "TOOL_ALARM_STOP",
    "TOOL_AMBIENT_SET_POLICY",
    "TOOL_AMBIENT_TEST_DISPLAY",
    "TOOL_DISPLAY_OFF",
    "TOOL_DISPLAY_STATUS",
    "TOOL_DISPLAY_WAKE",
    "alarm_cancel",
    "alarm_create",
    "alarm_snooze",
    "alarm_status",
    "alarm_stop",
    "ambient_set_policy",
    "ambient_test_display",
    "display_off",
    "display_status",
    "display_wake",
    "register_ambient_tools",
]
