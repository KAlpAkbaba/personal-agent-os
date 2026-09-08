"""Capability Genesis's voice tools (docs/M24_CAPABILITY_GENESIS_SPEC.md §6).
Four tools, registered from ``tools.default_registry()`` by ONE added line
(:func:`register_genesis_tools`), the same discipline every M19+ family
establishes for its own tools.

The ONE router (``app.voice.intents``) resolves the TARGET the owner named
against ``app.genesis.catalogue.GenesisInterfaceCatalogue`` and the OPERATION
against that target's own verb aliases, onto ``ctx.context["last_utterance"]``
(``capability_target_name``/``capability_target_url``/``capability_operation``)
— this module PREFERS those over the model's own arguments, the same
"owner's words win" rule every other M19+ tool family follows. A target the
catalogue does not know (``"google'ı bir artır"`` — not a registered local
application) is refused HERE, before any research happens at all (spec §7's
own negative case).

``capability.approve``/``capability.cancel`` resolve WHICH run through the
durable ``awaiting_approval`` state bound to THIS session
(``GenesisService.find_awaiting_approval``) — never from the model's own
``run`` argument, the M21 confirmation-gate discipline
(``app.actions.confirmation_gate``) applied here for the first time outside
mail/calendar.

Receipts say exactly one of the spec §6 six truths, in result-first Turkish;
numbers spoken are numbers read back (the M22 rule).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from app.actions.confirmation_gate import CONFIRM_SOURCE_VOICE, Confirmation
from app.evolution.errors import EvolutionError
from app.genesis.service import GenesisService
from app.ledger import service as ledger_service
from app.ledger.vocabulary import SUBSYSTEM_GENESIS
from app.logging import get_logger
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.intents import Intent

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.realtime_sessions.tools_genesis")

TOOL_CAPABILITY_REQUEST: Final = "capability.request"
TOOL_CAPABILITY_STATUS: Final = "capability.status"
TOOL_CAPABILITY_APPROVE: Final = "capability.approve"
TOOL_CAPABILITY_CANCEL: Final = "capability.cancel"

CAPABILITY_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_CAPABILITY_REQUEST,
    TOOL_CAPABILITY_STATUS,
    TOOL_CAPABILITY_APPROVE,
    TOOL_CAPABILITY_CANCEL,
)

ERROR_NOT_LOCAL_APPLICATION: Final = "not_a_local_application"
ERROR_NO_OPERATION: Final = "operation_not_understood"

SPEECH_NOT_LOCAL_APPLICATION: Final = "Bunu yapamam efendim; bu yerel bir uygulama değil."
SPEECH_NO_TARGET: Final = "Hangi uygulamayı diyorsunuz efendim?"
SPEECH_NO_OPERATION: Final = "Ne yapmamı istersiniz efendim?"
SPEECH_NOTHING_TO_APPROVE: Final = "Onaylayacağım bir şey yok efendim."
SPEECH_NOTHING_TO_CANCEL: Final = "Vazgeçeceğim bir şey yok efendim."
SPEECH_NOTHING_ASKED: Final = "Henüz yeni bir yetenek istenmedi efendim."

_ERROR_CLASS_TR: Final[dict[str, str]] = {
    "dependency_unavailable": "cevap vermiyor",
    "postcondition_failed": "cevap beklenen şekilde değildi",
    "validation_error": "isteği anlayamadım",
    "rate_limited": "çok kısa sürede çok denedim",
    "review_rejected": "kendi denetimimden geçemedi",
    "not_superior": "eskisinden daha iyi çıkmadı",
    "generation_failed": "yazılamadı",
    "evaluation_failed": "sınavı geçemedi",
    "supply_chain_rejected": "güvenlik denetiminden geçemedi",
}

_STATE_TR: Final[dict[str, str]] = {
    "capability_missing": "henüz yapamıyorum, deniyorum",
    "researching": "araştırıyorum",
    "designing": "tasarlıyorum",
    "building": "bağdaştırıcıyı yazıyorum",
    "testing": "sınıyorum",
    "classifying": "değerlendiriyorum",
    "awaiting_approval": "hazır; onayınızı bekliyor",
    "rolling_out": "deniyorum",
    "registering": "kaydediyorum",
    "available": "hazır",
    "used": "kullanıyorum",
    "verified": "kullanılabilir",
    "failed": "başarısız oldu",
    "cancelled": "vazgeçildi",
}


def _service(ctx: ToolContext, tool: str) -> GenesisService:
    service = ctx.live.get("genesis_service")
    if service is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the genesis service"
        )
    return service


def _turn_record(ctx: ToolContext) -> dict[str, Any]:
    """The ONE router's record of this turn — the owner's WORDS, preferred
    over the model's own argument, the same rule every M19+ tool family
    follows."""
    return dict(ctx.context.get("last_utterance") or {})


def _clarification(speech: str) -> dict[str, Any]:
    return {"status": "needs_clarification", "speech": speech, "candidates": []}


def _ledger(ctx: ToolContext, *, action: str, summary: str, detail: dict[str, Any]) -> None:
    if ctx.db is None:
        return
    try:
        ledger_service.record(
            ctx.db,
            ledger_service.ActivityEvent(
                event_type="action.receipt",
                subsystem=SUBSYSTEM_GENESIS,
                action=action,
                factual_summary=summary,
                occurred_at=datetime.now(UTC),
                detail_json=detail,
                source="live",
                source_ref=f"{action}:{ctx.call_id or uuid.uuid4()}",
            ),
        )
    except Exception:  # noqa: BLE001 - evidence, never a dependency of the tool
        logger.warning("genesis_tool_ledger_failed", action=action)


def _numeric_readback(output: dict[str, Any] | None) -> str | None:
    """The first numeric field in a dispatch/read-back output — spec §6's own
    example reads the changed number back ("...şimdi 4."), never a guess."""
    for value in (output or {}).values():
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return str(value)
    return None


def _receipt_for_result(target_label: str, result: dict[str, Any]) -> str:
    """Exactly one of spec §6's six truths, chosen from the run's OWN final
    state (this service is synchronous: by the time a receipt is built the
    run has already reached a terminal or awaiting_approval state — see
    app.genesis.service's own module docstring on why EvolutionPipeline-style
    async resumption was not reused here)."""
    if not result.get("new_run", True) and result.get("state") in ("verified", "used"):
        number = _numeric_readback(result.get("output"))
        if number is not None:
            return f"Yaptım efendim: {target_label} şimdi {number}."
        return f"Yaptım efendim: {target_label}."
    state = result.get("state")
    if state == "failed":
        reason = _ERROR_CLASS_TR.get(result.get("error_class") or "", "olmadı")
        return f"Olmadı efendim: testler geçmedi — {target_label} {reason}."
    if state == "awaiting_approval":
        return "Hazır; onayınızı bekliyor efendim."
    if state in ("verified", "used"):
        evidence = result.get("evidence") or {}
        number = _numeric_readback((evidence.get("dispatch") or {}).get("output"))
        if number is None:
            number = _numeric_readback(evidence.get("read_back"))
        if number is not None:
            return f"Artık yapabiliyorum efendim: {target_label} şimdi {number}."
        return f"Artık yapabiliyorum efendim: {target_label}."
    return (
        f"Bunu henüz yapamıyorum efendim: {target_label} için bir bağdaştırıcım yok. "
        "Yapmayı deniyorum."
    )


# --------------------------------------------------------------- capability.request


def capability_request(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Sayaç kutusunu bir artır.", "Test lambasını aç.", "Sayaç kaç?" (spec §6)."""
    service = _service(ctx, TOOL_CAPABILITY_REQUEST)
    turn = _turn_record(ctx)

    target_name = turn.get("capability_target_name")
    target_url = turn.get("capability_target_url")
    if not (isinstance(target_name, str) and target_name and isinstance(target_url, str)):
        return {
            "status": "succeeded",
            "execution_status": "refused",
            "error_class": ERROR_NOT_LOCAL_APPLICATION,
            "speech": SPEECH_NOT_LOCAL_APPLICATION,
        }

    operation = turn.get("capability_operation")
    if not (isinstance(operation, str) and operation):
        raw_op = arguments.get("operation")
        operation = str(raw_op) if isinstance(raw_op, str) and raw_op else None
    if not operation:
        return _clarification(SPEECH_NO_OPERATION)

    raw_arguments = arguments.get("arguments")
    call_arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}

    try:
        result = service.request(
            interface_name=target_name,
            interface_url=target_url,
            operation_id=operation,
            arguments=call_arguments,
            session_id=str(ctx.session_id),
            turn=turn.get("turn") if isinstance(turn.get("turn"), int) else None,
        )
    except EvolutionError as exc:
        speech = f"Olmadı efendim: {_ERROR_CLASS_TR.get(str(exc.error_class), 'olmadı')}."
        _ledger(
            ctx,
            action=TOOL_CAPABILITY_REQUEST,
            summary=f"capability.request -> refused ({exc.error_class})",
            detail={
                "target": target_name,
                "operation": operation,
                "error_class": str(exc.error_class),
            },
        )
        return {
            "status": "succeeded",
            "execution_status": "refused",
            "error_class": str(exc.error_class),
            "speech": speech,
        }

    speech = _receipt_for_result(target_name, result)
    _ledger(
        ctx,
        action=TOOL_CAPABILITY_REQUEST,
        summary=f"capability.request -> {result.get('state')}",
        detail={
            "target": target_name,
            "operation": operation,
            "run_id": result.get("id"),
            "state": result.get("state"),
        },
    )
    return {
        "status": "succeeded",
        "execution_status": "executed",
        "speech": speech,
        "run": result,
    }


# ---------------------------------------------------------------- capability.status


def capability_status(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Yeni yetenek ne durumda?", "Onu yapabiliyor musun artık?" (spec §6) — a QUERY;
    mutates nothing."""
    service = _service(ctx, TOOL_CAPABILITY_STATUS)
    turn = _turn_record(ctx)
    target_name = turn.get("capability_target_name")
    if not (isinstance(target_name, str) and target_name):
        raw = arguments.get("target")
        target_name = str(raw) if isinstance(raw, str) and raw else None

    run: dict[str, Any] | None = None
    if target_name:
        operation = turn.get("capability_operation")
        capability_id = f"{target_name}.{operation}" if operation else None
        if capability_id:
            run = service.status(capability_id=capability_id)
    if run is None:
        recent = service.list(limit=1)
        run = recent[0] if recent else None

    if run is None:
        return {"status": "succeeded", "execution_status": "noop", "speech": SPEECH_NOTHING_ASKED}

    label = run["capability_id"]
    state = run.get("state")
    if state == "failed":
        reason = _ERROR_CLASS_TR.get(run.get("error_class") or "", "olmadı")
        speech = f"{label}: olmadı efendim — {reason}."
    else:
        speech = f"{label}: {_STATE_TR.get(state, state)} efendim."
    return {
        "status": "succeeded",
        "execution_status": "noop",
        "speech": speech,
        "run": run,
    }


# --------------------------------------------------------------- capability.approve


def capability_approve(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Onaylıyorum.", "Bu uygulamayı yetkilendir." (spec §6) — honours ONLY a
    Confirmation the ONE router recorded for THIS session + a turn strictly
    after the park (module docstring); the ``run`` argument is never trusted
    for WHICH run — resolved from the durable awaiting_approval state instead."""
    del arguments
    service = _service(ctx, TOOL_CAPABILITY_APPROVE)
    pending = service.find_awaiting_approval(str(ctx.session_id))
    if pending is None:
        return _clarification(SPEECH_NOTHING_TO_APPROVE)

    turn = _turn_record(ctx)
    confirmation = Confirmation(
        source=CONFIRM_SOURCE_VOICE,
        session_id=str(ctx.session_id),
        turn=turn.get("turn") if isinstance(turn.get("turn"), int) else None,
        owner_intent_ok=turn.get("intent") == Intent.CAPABILITY_APPROVE,
    )
    try:
        result = service.approve(uuid.UUID(pending["id"]), confirmation)
    except EvolutionError as exc:
        return {
            "status": "succeeded",
            "execution_status": "refused",
            "error_class": str(exc.error_class),
            "speech": "Onayınızı bu şekilde kabul edemiyorum efendim.",
        }
    speech = _receipt_for_result(pending["capability_id"], result)
    _ledger(
        ctx,
        action=TOOL_CAPABILITY_APPROVE,
        summary=f"capability.approve -> {result.get('state')}",
        detail={"run_id": result.get("id"), "state": result.get("state")},
    )
    return {"status": "succeeded", "execution_status": "executed", "speech": speech, "run": result}


# ---------------------------------------------------------------- capability.cancel


def capability_cancel(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Vazgeç, yapma." while a run is awaiting approval (spec §6)."""
    del arguments
    service = _service(ctx, TOOL_CAPABILITY_CANCEL)
    pending = service.find_awaiting_approval(str(ctx.session_id))
    if pending is None:
        return _clarification(SPEECH_NOTHING_TO_CANCEL)
    result = service.cancel(uuid.UUID(pending["id"]))
    _ledger(
        ctx,
        action=TOOL_CAPABILITY_CANCEL,
        summary="capability.cancel -> cancelled",
        detail={"run_id": result.get("id")},
    )
    return {
        "status": "succeeded",
        "execution_status": "executed",
        "speech": f"Vazgeçtim efendim: {pending['capability_id']}.",
        "run": result,
    }


# ------------------------------------------------------------------ registration


def register_genesis_tools(reg: ToolRegistry) -> ToolRegistry:
    """Register all four tools (module docstring: ONE line in ``default_registry``)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_CAPABILITY_REQUEST,
            description=(
                "Sahibin adını söylediği yerel bir uygulamada bir işlem yapar: "
                "'sayaç kutusunu bir artır', 'test lambasını aç', 'sayaç kaç'. Hangi "
                "uygulama ve hangi işlem olduğunu SUNUCU çözer (owner'ın sözleri); "
                "'arguments' alanına sahibin söylediği sayı/değerleri ver. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "maxLength": 200},
                    "operation": {"type": "string", "maxLength": 64},
                    "arguments": {"type": "object"},
                },
                "additionalProperties": False,
            },
            handler=capability_request,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CAPABILITY_STATUS,
            description=(
                "Yeni bir yeteneğin ne durumda olduğunu sorar: 'yeni yetenek ne "
                "durumda?', 'onu yapabiliyor musun artık?'. Dönen 'speech' metnini "
                "aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=capability_status,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CAPABILITY_APPROVE,
            description=(
                "Bekleyen bir yetenek onayını verir: 'onaylıyorum', 'bu uygulamayı "
                "yetkilendir'. Hangi onay olduğunu SUNUCU çözer. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=capability_approve,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CAPABILITY_CANCEL,
            description=(
                "Bekleyen bir yetenek isteğinden vazgeçer: 'vazgeç, yapma'. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=capability_cancel,
        )
    )
    return reg


__all__ = [
    "CAPABILITY_TOOL_NAMES",
    "TOOL_CAPABILITY_APPROVE",
    "TOOL_CAPABILITY_CANCEL",
    "TOOL_CAPABILITY_REQUEST",
    "TOOL_CAPABILITY_STATUS",
    "capability_approve",
    "capability_cancel",
    "capability_request",
    "capability_status",
    "register_genesis_tools",
]
