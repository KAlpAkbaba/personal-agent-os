"""Voice macros by voice (owner note 2 of 2026-09-21, docs/DECISIONS.md ADR-0196).

The owner: "I have a button or a click in my head ... instead of describing every click,
I say 'yeni hareket oluştur', do the three to five things, say 'hareketi bitir', it asks
me for a name, I say 'yeni mail sekmesi', and from then on 'yeni mail sekmesi aç' repeats
them without me describing them."

Seven tools, one conversation:

* ``macro.record_start`` - the session starts keeping every tool call that follows (the
  calls still happen; the owner is doing the thing while teaching it).
* ``macro.record_end`` - stop keeping; ask for the name (a recording with no steps is
  closed and says so).
* ``macro.name`` - the next sentence, saved under ``naming.name_key``; the same name
  again REPLACES the old steps.
* ``macro.cancel`` - drop the recording or the pending name.
* ``macro.run`` - replay: every kept call through the SAME handler and the SAME step-up
  gate it met when it was recorded, in order, stopping at the first call that did not
  succeed and saying which one.
* ``macro.list`` / ``macro.delete`` - read back, remove.

What a macro is NOT: a routine (``app.routines`` fires on time/presence/condition; a
macro fires on the owner's word), and not a model plan (nothing is inferred - the steps
are the calls that were made). The relay captures the steps (``service.handle_tool_call``),
the router names the words (``app.voice.intents``), this module only talks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from app.macros import service as macros_service
from app.macros.naming import name_key
from app.security import step_up as step_up_policy
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_MACRO_RECORD_START: Final = "macro.record_start"
TOOL_MACRO_RECORD_END: Final = "macro.record_end"
TOOL_MACRO_NAME: Final = "macro.name"
TOOL_MACRO_CANCEL: Final = "macro.cancel"
TOOL_MACRO_RUN: Final = "macro.run"
TOOL_MACRO_LIST: Final = "macro.list"
TOOL_MACRO_DELETE: Final = "macro.delete"

MACRO_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_MACRO_RECORD_START,
    TOOL_MACRO_RECORD_END,
    TOOL_MACRO_NAME,
    TOOL_MACRO_CANCEL,
    TOOL_MACRO_RUN,
    TOOL_MACRO_LIST,
    TOOL_MACRO_DELETE,
)

SPEECH_RECORDING_TR: Final = (
    "Hareketi kaydediyorum efendim. Adımları sırayla söyleyin; bitince 'hareketi bitir' deyin."
)
SPEECH_ALREADY_RECORDING_TR: Final = (
    "Zaten bir hareket kaydediyorum efendim; bitirmek için 'hareketi bitir' deyin."
)
SPEECH_NAME_PENDING_TR: Final = (
    "Önce bekleyen harekete bir ad verin efendim; vazgeçmek için 'vazgeç'."
)
SPEECH_NOT_RECORDING_TR: Final = "Kayıtta bir hareket yok efendim."
SPEECH_NO_STEPS_TR: Final = "Hiç adım kaydedilmedi efendim; kaydı kapattım."
SPEECH_ASK_NAME_TR: Final = "{count} adım kaydettim efendim. Bu harekete ne ad vereyim?"
SPEECH_ASK_NAME_DROPPED_TR: Final = (
    "{count} adım kaydettim efendim; {dropped} adım sığmadı. Bu harekete ne ad vereyim?"
)
SPEECH_NAME_NEEDED_TR: Final = "Bir ad duyamadım efendim; bu harekete ne ad vereyim?"
SPEECH_NOTHING_TO_NAME_TR: Final = "Ad bekleyen bir hareket yok efendim."
SPEECH_SAVED_TR: Final = (
    "'{name}' hareketi {count} adımla kaydedildi efendim. Bundan sonra '{name} aç' demeniz yeter."
)
SPEECH_REPLACED_TR: Final = "'{name}' hareketini {count} adımla yeniledim efendim."
SPEECH_CANCELLED_TR: Final = "Hareket kaydını iptal ettim efendim."
SPEECH_UNKNOWN_TR: Final = "'{name}' adında bir hareket yok efendim."
SPEECH_WHICH_TR: Final = "Hangi hareket efendim?"
SPEECH_RAN_TR: Final = "'{name}' hareketini yaptım efendim; {count} adım."
SPEECH_STOPPED_TR: Final = "'{name}' hareketinin {index}. adımında durdum efendim: {why}"
SPEECH_STEP_REFUSED_TR: Final = "{tool} bu cihazda şu an yapılamıyor."
SPEECH_STEP_UNKNOWN_TR: Final = "{tool} aracı artık yok."
SPEECH_STEP_FAILED_TR: Final = "{tool} başarısız oldu."
SPEECH_DELETED_TR: Final = "'{name}' hareketini sildim efendim."
SPEECH_NONE_TR: Final = "Kayıtlı hareketiniz yok efendim."
SPEECH_LIST_TR: Final = "{count} hareketiniz var efendim: {names}."
SPEECH_LIST_MORE_TR: Final = "{count} hareketiniz var efendim; ilk {shown}: {names}."
SPEECH_RUN_WHILE_RECORDING_TR: Final = (
    "Kayıt sürerken bir hareket çalıştırmıyorum efendim; önce 'hareketi bitir' deyin."
)
#: How many names the spoken list reads out (the same reasoning ``tools_routines``
#: gives its own bound).
SPOKEN_LIST_MAX: Final = 8


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _turn(ctx: ToolContext) -> dict[str, Any]:
    record = ctx.context.get("last_utterance")
    return dict(record) if isinstance(record, dict) else {}


def _spoken_macro_name(ctx: ToolContext, arguments: dict[str, Any]) -> str:
    """The owner's own words first (``macro_name`` on the turn), the model's ``name``
    argument only when the router named none."""
    spoken = _turn(ctx).get("macro_name")
    if isinstance(spoken, str) and spoken.strip():
        return spoken.strip()
    raw = arguments.get("name")
    return raw.strip() if isinstance(raw, str) else ""


def _clarification(speech: str) -> dict[str, Any]:
    return {"status": "needs_clarification", "speech": speech, "candidates": []}


# ------------------------------------------------------------------ recording


def macro_record_start(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Yeni hareket oluştur." - from now on every tool call is kept."""
    del arguments
    if macros_service.is_recording(ctx.context):
        return {"status": "recording", "already": True, "speech": SPEECH_ALREADY_RECORDING_TR}
    if macros_service.is_awaiting_name(ctx.context):
        return {"status": "awaiting_name", "speech": SPEECH_NAME_PENDING_TR}
    state = macros_service.begin_recording(ctx.context, now=ctx.now)
    return {
        "status": state["status"],
        "started_at": state["started_at"],
        "speech": SPEECH_RECORDING_TR,
    }


def macro_record_end(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Hareketi bitir." - stop keeping, ask for the name."""
    del arguments
    if macros_service.is_awaiting_name(ctx.context):
        return {"status": "awaiting_name", "speech": SPEECH_NAME_NEEDED_TR}
    state = macros_service.end_recording(ctx.context)
    if state is None:
        return {"status": "idle", "speech": SPEECH_NOT_RECORDING_TR}
    steps = state.get("steps") or []
    if not steps:
        return {"status": "idle", "step_count": 0, "speech": SPEECH_NO_STEPS_TR}
    dropped = int(state.get("dropped") or 0) + int(state.get("dropped_secret") or 0)
    speech = (
        SPEECH_ASK_NAME_DROPPED_TR.format(count=len(steps), dropped=dropped)
        if dropped
        else SPEECH_ASK_NAME_TR.format(count=len(steps))
    )
    return {"status": "awaiting_name", "step_count": len(steps), "speech": speech}


def macro_name(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """The sentence after "ne ad vereyim?" - saved under that name."""
    db = _require_db(ctx, TOOL_MACRO_NAME)
    if not macros_service.is_awaiting_name(ctx.context):
        return {"status": "idle", "speech": SPEECH_NOTHING_TO_NAME_TR}
    name = _spoken_macro_name(ctx, arguments)
    if not name or not name_key(name):
        # Still awaiting: the owner is asked again, nothing is dropped.
        return _clarification(SPEECH_NAME_NEEDED_TR)
    steps = macros_service.take_pending(ctx.context) or []
    if not steps:
        return {"status": "idle", "speech": SPEECH_NO_STEPS_TR}
    row, replaced = macros_service.save_macro(db, name=name, steps=steps, now=ctx.now)
    speech = (
        SPEECH_REPLACED_TR.format(name=row.name, count=row.step_count)
        if replaced
        else SPEECH_SAVED_TR.format(name=row.name, count=row.step_count)
    )
    return {
        "status": "saved",
        "macro_id": str(row.macro_id),
        "name": row.name,
        "name_key": row.name_key,
        "step_count": row.step_count,
        "replaced": replaced,
        "tools": [str(s.get("tool")) for s in row.steps_json],
        "speech": speech,
    }


def macro_cancel(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Hareketi iptal et." / "Vazgeç." while recording or naming."""
    del arguments
    state = macros_service.cancel_recording(ctx.context)
    if state is None:
        return {"status": "idle", "speech": SPEECH_NOT_RECORDING_TR}
    return {
        "status": "cancelled",
        "step_count": len(state.get("steps") or []),
        "speech": SPEECH_CANCELLED_TR,
    }


# ------------------------------------------------------------------- replay


def _step_outcome(tool: str, result: Any) -> tuple[bool, str]:
    """(went well, why not) for one replayed call, judged the way the relay judges a
    call of its own (``terminal_status_for``) plus the receipt shapes a handler returns."""
    from app.voice.realtime_sessions.tools import terminal_status_for

    status, error_class = terminal_status_for(tool, result)
    body = result if isinstance(result, dict) else {}
    speech = str(body.get("speech") or "")
    if body.get("status") == "needs_clarification":
        return False, speech or SPEECH_STEP_FAILED_TR.format(tool=tool)
    # An action's receipt is a SUCCEEDED call whatever happened (M18 contract §5.5) -
    # so the receipt itself is read: a refused or failed execution, or a terminal state
    # the device could not verify, is a step that did not do what it was recorded doing.
    if body.get("execution_status") in ("refused", "failed") or body.get("status") == "refused":
        return False, speech or SPEECH_STEP_FAILED_TR.format(tool=tool)
    if body.get("terminal_status") == "failed" or body.get("error_class"):
        return False, speech or SPEECH_STEP_FAILED_TR.format(tool=tool)
    if status == "failed":
        return False, speech or SPEECH_STEP_FAILED_TR.format(tool=tool) + (
            f" ({error_class})" if error_class else ""
        )
    return True, speech


def macro_run(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Yeni mail sekmesi aç." - the kept calls, in order, through the same handlers.

    Each step meets the same step-up gate a fresh call would (``step_up.evaluate`` with
    the device-trust fact the relay derived), so a macro is never a way around it. The
    replay stops at the first step that did not succeed and the sentence names it: a
    macro that went on after a failed click would be clicking somewhere else.
    """
    db = _require_db(ctx, TOOL_MACRO_RUN)
    if macros_service.is_recording(ctx.context):
        return {"status": "recording", "speech": SPEECH_RUN_WHILE_RECORDING_TR}
    words = _spoken_macro_name(ctx, arguments)
    if not words:
        return _clarification(SPEECH_WHICH_TR)
    row = macros_service.find_macro_by_words(db, words)
    if row is None:
        return {"status": "unknown", "name": words, "speech": SPEECH_UNKNOWN_TR.format(name=words)}
    registry: ToolRegistry | None = ctx.live.get("tool_registry")
    if registry is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            "macro.run needs the tool registry on the session",
        )
    device_trusted = bool(ctx.live.get("device_trusted", False))
    original_turn = ctx.context.get("last_utterance")
    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    outcomes: list[dict[str, Any]] = []
    stopped_at: int | None = None
    why = ""
    steps = [s for s in (row.steps_json or []) if isinstance(s, dict)]
    try:
        for index, step in enumerate(steps, start=1):
            tool = str(step.get("tool") or "")
            spec = registry.get(tool)
            if spec is None:
                stopped_at, why = index, SPEECH_STEP_UNKNOWN_TR.format(tool=tool)
                outcomes.append(
                    {"index": index, "tool": tool, "ok": False, "reason": "unknown_tool"}
                )
                break
            decision = step_up_policy.evaluate(
                db,
                tool=spec.name,
                owner_session_id=ctx.owner_session_id,
                device_trusted=device_trusted,
                now=ctx.now,
            )
            step_up_policy.record(decision, owner_session_id=ctx.owner_session_id)
            if not decision.allowed:
                stopped_at, why = index, decision.speech or SPEECH_STEP_REFUSED_TR.format(tool=tool)
                outcomes.append(
                    {"index": index, "tool": tool, "ok": False, "reason": "step_up_required"}
                )
                break
            # The turn record the handler will read: the one it read when recorded,
            # stamped now so its freshness guard sees a live turn.
            ctx.context["last_utterance"] = {
                **dict(step.get("turn") or {}),
                "at": now_iso,
                "macro_replay": row.name_key,
            }
            try:
                result = spec.handler(ctx, dict(step.get("arguments") or {}))
            except VoiceError as exc:
                detail_speech = exc.details.get("speech") if isinstance(exc.details, dict) else None
                stopped_at = index
                why = str(detail_speech or SPEECH_STEP_FAILED_TR.format(tool=tool))
                outcomes.append(
                    {"index": index, "tool": tool, "ok": False, "reason": exc.error_class.value}
                )
                break
            ok, step_speech = _step_outcome(tool, result)
            outcomes.append(
                {
                    "index": index,
                    "tool": tool,
                    "ok": ok,
                    "speech": step_speech[:200],
                    "action_id": (result or {}).get("action_id")
                    if isinstance(result, dict)
                    else None,
                }
            )
            if not ok:
                stopped_at, why = index, step_speech or SPEECH_STEP_FAILED_TR.format(tool=tool)
                break
    finally:
        # A later follow-up must see the owner's OWN last sentence, not a replayed one.
        if original_turn is None:
            ctx.context.pop("last_utterance", None)
        else:
            ctx.context["last_utterance"] = original_turn
    macros_service.mark_run(row, now=ctx.now)
    if stopped_at is None:
        speech = SPEECH_RAN_TR.format(name=row.name, count=len(steps))
        status = "succeeded"
    else:
        speech = SPEECH_STOPPED_TR.format(name=row.name, index=stopped_at, why=why)
        status = "stopped"
    return {
        "status": status,
        "macro_id": str(row.macro_id),
        "name": row.name,
        "name_key": row.name_key,
        "step_count": len(steps),
        "steps_completed": sum(1 for o in outcomes if o.get("ok")),
        "stopped_at": stopped_at,
        "steps": outcomes,
        "speech": speech,
    }


# ------------------------------------------------------------- list / delete


def macro_list(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Hangi hareketlerim var?" """
    del arguments
    db = _require_db(ctx, TOOL_MACRO_LIST)
    rows = macros_service.list_macros(db)
    if not rows:
        return {"macros": [], "count": 0, "speech": SPEECH_NONE_TR}
    names = [row.name for row in rows]
    shown = names[:SPOKEN_LIST_MAX]
    speech = (
        SPEECH_LIST_MORE_TR.format(count=len(rows), shown=len(shown), names=", ".join(shown))
        if len(rows) > len(shown)
        else SPEECH_LIST_TR.format(count=len(rows), names=", ".join(shown))
    )
    return {
        "macros": [
            {
                "macro_id": str(row.macro_id),
                "name": row.name,
                "name_key": row.name_key,
                "step_count": row.step_count,
                "run_count": row.run_count,
            }
            for row in rows
        ],
        "count": len(rows),
        "speech": speech,
    }


def macro_delete(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Yeni mail sekmesi hareketini sil." """
    db = _require_db(ctx, TOOL_MACRO_DELETE)
    words = _spoken_macro_name(ctx, arguments)
    if not words:
        return _clarification(SPEECH_WHICH_TR)
    row = macros_service.delete_macro(db, name_key(words))
    if row is None:
        return {"status": "unknown", "name": words, "speech": SPEECH_UNKNOWN_TR.format(name=words)}
    return {
        "status": "deleted",
        "macro_id": str(row.macro_id),
        "name": row.name,
        "speech": SPEECH_DELETED_TR.format(name=row.name),
    }


# ------------------------------------------------------------------ registry


def register_macro_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    no_args = {"type": "object", "properties": {}, "additionalProperties": False}
    name_arg = {
        "type": "object",
        "properties": {"name": {"type": "string", "maxLength": 80}},
        "additionalProperties": False,
    }
    reg.register(
        ToolSpec(
            name=TOOL_MACRO_RECORD_START,
            description=(
                "HAREKET KAYDINI BAŞLATIR: 'yeni hareket oluştur', 'yeni hareket başlat', "
                "'hareket kaydet' denince. Bundan sonra yapılan her araç çağrısı hem yapılır "
                "hem hareket olarak saklanır; sahip 'hareketi bitir' deyince ad sorulur. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters=no_args,
            handler=macro_record_start,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MACRO_RECORD_END,
            description=(
                "HAREKET KAYDINI BİTİRİR: 'hareketi bitir', 'hareketi tamamla' denince. "
                "Kaydedilen adım sayısını söyler ve harekete AD sorar; sahibin bir sonraki "
                "cümlesi addır (macro.name). Dönen 'speech' metnini aynen oku."
            ),
            parameters=no_args,
            handler=macro_record_end,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MACRO_NAME,
            description=(
                "Ad sorulduktan sonra sahibin söylediği AD ile hareketi kaydeder. Adı "
                "sahibin cümlesinden SUNUCU okur; 'name' yalnız yönlendirici okuyamadıysa. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters=name_arg,
            handler=macro_name,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MACRO_CANCEL,
            description=(
                "Süren hareket kaydını ya da ad bekleyen hareketi İPTAL eder: 'hareketi "
                "iptal et', ad sorulmuşken 'vazgeç'. Dönen 'speech' metnini aynen oku."
            ),
            parameters=no_args,
            handler=macro_cancel,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MACRO_RUN,
            description=(
                "KAYITLI BİR HAREKETİ ÇALIŞTIRIR: sahip kayıtlı bir hareketin adını söyleyince "
                "('yeni mail sekmesi aç', 'yeni mail sekmesi hareketini yap'). Adımlar "
                "kaydedildikleri sırayla, aynı araçlarla yapılır; ilk başarısız adımda durur "
                "ve hangisi olduğunu söyler. Dönen 'speech' metnini aynen oku."
            ),
            parameters=name_arg,
            handler=macro_run,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MACRO_LIST,
            description=(
                "Kayıtlı hareketleri sayar ve adlarını okur: 'hangi hareketlerim var', "
                "'hareketleri listele'. Dönen 'speech' metnini aynen oku."
            ),
            parameters=no_args,
            handler=macro_list,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MACRO_DELETE,
            description=(
                "Kayıtlı bir hareketi SİLER: '<ad> hareketini sil'. Dönen 'speech' metnini "
                "aynen oku."
            ),
            parameters=name_arg,
            handler=macro_delete,
        )
    )
    return reg


__all__ = [
    "MACRO_TOOL_NAMES",
    "TOOL_MACRO_CANCEL",
    "TOOL_MACRO_DELETE",
    "TOOL_MACRO_LIST",
    "TOOL_MACRO_NAME",
    "TOOL_MACRO_RECORD_END",
    "TOOL_MACRO_RECORD_START",
    "TOOL_MACRO_RUN",
    "macro_cancel",
    "macro_delete",
    "macro_list",
    "macro_name",
    "macro_record_end",
    "macro_record_start",
    "macro_run",
    "register_macro_tools",
]
