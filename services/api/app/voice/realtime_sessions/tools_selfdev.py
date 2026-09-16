"""The owner's voice over the self-development queue (B35 req 622, 623).

Three tools - ``selfdev.defect`` ("Şu bug'ı kendin düzelt"), ``selfdev.feature`` ("Şu
özelliği kendine ekle") and ``selfdev.status`` ("Kendinde ne düzeltiyorsun?") -
registered from ``tools.default_registry()`` by ONE added line, the same discipline
``tools_documents`` / ``tools_native`` follow.

A spoken assignment is a QUEUED row and a receipt saying so: the run happens on the
development machine's worker, the candidate waits for the owner, and nothing is promoted.
The owner's own words are what the row carries (``selfdev_request`` from the ONE router,
preferred over the model's ``content`` argument, the rule every family follows).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_FAILED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.evolution.authority import AuthorityError
from app.ledger.vocabulary import SUBSYSTEM_SELFDEV
from app.logging import get_logger
from app.selfdev.models import DEFECT_KIND_BUG, DEFECT_KIND_FEATURE, SOURCE_OWNER_VOICE
from app.selfdev.service import ERROR_INVALID_DEFECT, ERROR_SELFDEV_DISABLED, SelfDevService
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.realtime_sessions.tools_selfdev")

TOOL_SELFDEV_DEFECT: Final = "selfdev.defect"
TOOL_SELFDEV_FEATURE: Final = "selfdev.feature"
TOOL_SELFDEV_STATUS: Final = "selfdev.status"
SELFDEV_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_SELFDEV_DEFECT,
    TOOL_SELFDEV_FEATURE,
    TOOL_SELFDEV_STATUS,
)

SPEECH_QUEUED_BUG: Final = (
    "Kuyruğa aldım efendim: {title}. Kendi dalında düzeltip test edeceğim; "
    "aday hazır olunca onayınızı isteyeceğim, kendi başıma canlıya almam."
)
SPEECH_QUEUED_FEATURE: Final = (
    "Kuyruğa aldım efendim: {title}. Kendi dalında ekleyip test edeceğim; "
    "aday hazır olunca onayınızı isteyeceğim, kendi başıma canlıya almam."
)
SPEECH_NO_REQUEST: Final = "Neyi düzeltmemi istediğinizi anlayamadım; kısaca söyler misiniz?"
SPEECH_DISABLED: Final = "Kendini geliştirme bu sunucuda kapalı; kuyruğa bir şey almadım."
SPEECH_NO_SERVICE: Final = "Kendini geliştirme kuyruğuna bu oturumdan ulaşamıyorum."
SPEECH_STATUS_EMPTY: Final = "Şu an kendimde bir şey düzeltmiyorum; kuyruk boş."


def _turn_record(ctx: ToolContext) -> dict[str, Any]:
    return dict(ctx.context.get("last_utterance") or {})


def _request_text(ctx: ToolContext, arguments: dict[str, Any]) -> str | None:
    turn = _turn_record(ctx)
    for key in ("selfdev_request",):
        value = turn.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("content", "description", "title"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _service(ctx: ToolContext, tool: str) -> SelfDevService:
    service = ctx.live.get("selfdev_service")
    if service is None:
        raise VoiceError(
            VoiceErrorClass.PROVIDER_UNAVAILABLE,
            f"{tool}: no selfdev service in this session",
            tool=tool,
        )
    return service


def _receipt(
    ctx: ToolContext,
    *,
    capability: str,
    requested_state: str,
    execution: str,
    terminal: str,
    speech: str,
    server: dict[str, Any] | None = None,
    error_class: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    receipt = ActionReceipt(
        action_id=str(ctx.call_id or uuid.uuid4()),
        capability=capability,
        requested_state=requested_state,
        execution_status=execution,
        terminal_status=terminal,
        observed_after={"server": dict(server or {}), "local": {}},
        evidence_refs=[],
        error_class=error_class,
        speech=speech,
        started_at=now,
        completed_at=now,
        session_id=str(ctx.session_id),
        observed_at=now,
    )
    if ctx.db is not None:
        record_receipt(ctx.db, receipt, SUBSYSTEM_SELFDEV)
    out = receipt.as_dict()
    if extra:
        out.update(extra)
    return out


def _title_of(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first[:120] or "sahibin isteği"


def _assign(ctx: ToolContext, arguments: dict[str, Any], *, kind: str, tool: str) -> dict[str, Any]:
    requested_state = "queued"
    text = _request_text(ctx, arguments)
    if not text:
        return _receipt(
            ctx,
            capability=tool,
            requested_state=requested_state,
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            speech=SPEECH_NO_REQUEST,
            server={"reason": ERROR_INVALID_DEFECT},
            error_class=ERROR_INVALID_DEFECT,
        )
    service = _service(ctx, tool)
    if ctx.db is None:
        return _receipt(
            ctx,
            capability=tool,
            requested_state=requested_state,
            execution=EXECUTION_FAILED,
            terminal=TERMINAL_FAILED,
            speech=SPEECH_NO_SERVICE,
            server={"reason": "no_db"},
            error_class="dependency_unavailable",
        )
    evidence = str(arguments.get("evidence") or "").strip()
    try:
        row = service.intake(
            ctx.db,
            title=_title_of(text),
            evidence=(text + ("\n\n" + evidence if evidence else "")),
            source=SOURCE_OWNER_VOICE,
            kind=kind,
            session_id=str(ctx.session_id),
        )
    except AuthorityError as exc:
        reason = str(exc.details.get("reason") or "") if hasattr(exc, "details") else ""
        code = ERROR_SELFDEV_DISABLED if reason == ERROR_SELFDEV_DISABLED else "not_authorized"
        return _receipt(
            ctx,
            capability=tool,
            requested_state=requested_state,
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            speech=SPEECH_DISABLED,
            server={"reason": code},
            error_class=code,
        )
    speech = SPEECH_QUEUED_BUG if kind == DEFECT_KIND_BUG else SPEECH_QUEUED_FEATURE
    entry = service.entry(row)
    return _receipt(
        ctx,
        capability=tool,
        requested_state=requested_state,
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        speech=speech.format(title=row.title),
        server={
            "defect_id": str(row.id),
            "state": row.state,
            "kind": row.kind,
            "source": row.source,
            "promoted": False,
        },
        extra={"defect": entry},
    )


def selfdev_defect(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Şu bug'ı kendin düzelt." (req 622): a queued bug, never a live edit."""
    return _assign(ctx, arguments, kind=DEFECT_KIND_BUG, tool=TOOL_SELFDEV_DEFECT)


def selfdev_feature(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Şu özelliği kendine ekle." (req 623): a queued feature, same path, same gate."""
    return _assign(ctx, arguments, kind=DEFECT_KIND_FEATURE, tool=TOOL_SELFDEV_FEATURE)


def _state_tr(state: str) -> str:
    return {
        "queued": "kuyrukta",
        "claimed": "alındı",
        "running": "çalışıyor",
        "awaiting_owner": "onayınızı bekliyor",
        "approved": "onaylandı",
        "rejected": "reddedildi",
        "refused": "reddettim",
        "quarantined": "karantinada",
        "failed": "başarısız",
    }.get(state, state)


def selfdev_status(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Kendinde ne düzeltiyorsun?": the queue's rows, spoken, newest first."""
    service = _service(ctx, TOOL_SELFDEV_STATUS)
    if ctx.db is None:
        return _receipt(
            ctx,
            capability=TOOL_SELFDEV_STATUS,
            requested_state="read",
            execution=EXECUTION_FAILED,
            terminal=TERMINAL_FAILED,
            speech=SPEECH_NO_SERVICE,
            error_class="dependency_unavailable",
        )
    rows = service.list(ctx.db, limit=5)
    status = service.status(ctx.db)
    if not rows:
        speech = SPEECH_STATUS_EMPTY
    else:
        parts = [f"{row.title} ({_state_tr(row.state)})" for row in rows[:3]]
        speech = "Kuyrukta: " + "; ".join(parts) + "."
        if status["awaiting_owner"]:
            speech += f" {status['awaiting_owner']} aday onayınızı bekliyor."
    return _receipt(
        ctx,
        capability=TOOL_SELFDEV_STATUS,
        requested_state="read",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        speech=speech,
        server={"counts": status["counts"], "budget": status["budget"]},
        extra={"defects": [service.entry(row) for row in rows]},
    )


def register_selfdev_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_SELFDEV_DEFECT,
            description=(
                "Sahibin tarif ettiği bir hatayı sistemin KENDİ kodunda düzeltmesi için "
                "kuyruğa al ('Şu bug'ı kendin düzelt'). Kendi dalında düzeltir, test eder, "
                "sahibin onayını bekler; canlıya almaz."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Hatanın tarifi, sahibin sözleriyle.",
                    },
                    "evidence": {"type": "string", "description": "Varsa hata mesajı / belirti."},
                },
                "additionalProperties": False,
            },
            handler=selfdev_defect,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_SELFDEV_FEATURE,
            description=(
                "Sahibin istediği bir özelliği sistemin KENDİNE eklemesi için kuyruğa al "
                "('Şu özelliği kendine ekle'). Aynı yol: dal, test, sahip onayı; canlıya almaz."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Özelliğin tarifi."},
                    "evidence": {"type": "string"},
                },
                "additionalProperties": False,
            },
            handler=selfdev_feature,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_SELFDEV_STATUS,
            description="Kendini geliştirme kuyruğu: ne düzeltiliyor, hangi aday onay bekliyor.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=selfdev_status,
        )
    )
    return reg
