"""The owner's voice over operator missions (B39 req 127-130).

One tool, ``operator.mission``, with an ``action``: ``start`` ("Chrome'u aç ve YouTube'a
gir", "Ayarlarda Bluetooth'u aç", "... önce göster"), ``approve`` ("Evet, başla" while the
plan waits), ``pause`` / ``resume`` / ``cancel`` while it runs, ``status`` ("Ne yapıyorsun?").
Registered from ``tools.default_registry()`` by ONE added line.

A start is a persisted, planned row and a followup that starts the durable workflow
(``Client.start_workflow`` only exists as a coroutine and this handler is synchronous -
the same reason ``executive.start`` defers). The owner's own sentence is what is planned
(``mission_request`` from the ONE router, preferred over the model's ``content`` argument,
the rule every family follows). With ``preview`` the plan is SPOKEN and nothing runs until
the owner says yes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_NOOP,
    EXECUTION_REFUSED,
    TERMINAL_ALREADY,
    TERMINAL_FAILED,
    TERMINAL_UNVERIFIED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.ledger.vocabulary import SUBSYSTEM_OPERATOR
from app.logging import get_logger
from app.operator import mission_service
from app.operator.capabilities import CAPABILITY_MISSION
from app.operator.mission import (
    MISSION_AWAITING_APPROVAL,
    MISSION_PAUSED,
    PREVIEW_MARKERS,
)
from app.operator.mission_models import SOURCE_VOICE
from app.operator.mission_service import MissionServiceError

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.realtime_sessions.tools_mission")

#: The registered name, spelled here as a literal so the self-model indexer (which reads
#: this module with ``ast``) sees it; ``test_operator_b39`` holds it equal to the capability.
TOOL_MISSION: Final = "operator.mission"

MISSION_ACTIONS: Final[tuple[str, ...]] = (
    "start",
    "approve",
    "pause",
    "resume",
    "cancel",
    "status",
)

SPEECH_NO_REQUEST: Final = (
    "Ne yapmamı istediğinizi tam anlayamadım efendim; adım adım söyler misiniz?"
)
SPEECH_NO_DB: Final = "Bu görevi başlatmak için veritabanına ihtiyacım var efendim."
SPEECH_NOTHING_ACTIVE: Final = "Şu an yürüyen bir görev yok efendim."
SPEECH_STARTED: Final = "Başlıyorum efendim: {plan}."
SPEECH_APPROVED: Final = "Tamam efendim, başlıyorum."
SPEECH_PAUSED: Final = "Duraklattım efendim; bu adımdan sonra bekleyeceğim."
SPEECH_RESUMED: Final = "Devam ediyorum efendim."
#: What only the owner's own words may do (see ``_control``).
OWNER_ONLY_ACTIONS: Final[frozenset[str]] = frozenset({"approve", "resume"})
#: Declared the way test_owner_error_language reads every class (no annotation).
ERROR_OWNER_WORD_REQUIRED = "owner_word_required"
SPEECH_WAITING_FOR_OWNER: Final = "Görev sizin cevabınızı bekliyor efendim."
SPEECH_ALREADY_MOVING: Final = "Görev zaten yürüyor efendim; onay gerekmiyor."
SPEECH_CANCELLED: Final = "Görevi iptal ettim efendim."


def _turn_record(ctx: ToolContext) -> dict[str, Any]:
    return dict(ctx.context.get("last_utterance") or {})


def _request_text(ctx: ToolContext, arguments: dict[str, Any]) -> str | None:
    turn = _turn_record(ctx)
    value = turn.get("mission_request")
    if isinstance(value, str) and value.strip():
        return value.strip()
    for key in ("content", "goal", "directive"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _receipt(
    ctx: ToolContext,
    *,
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
        capability=CAPABILITY_MISSION,
        requested_state=requested_state,
        execution_status=execution,
        terminal_status=terminal,
        observed_after={"server": dict(server or {}), "local": {}},
        evidence_refs=[{"kind": "realtime_session", "ref": str(ctx.session_id)}],
        error_class=error_class,
        speech=speech,
        started_at=ctx.now,
        completed_at=now,
        session_id=str(ctx.session_id),
        observed_at=now,
    )
    if ctx.db is not None:
        record_receipt(ctx.db, receipt, SUBSYSTEM_OPERATOR)
    out = receipt.as_dict()
    if extra:
        out.update(extra)
    return out


def _clarification(speech: str, *, error_class: str = "clarification_needed") -> dict[str, Any]:
    return {
        "status": "needs_clarification",
        "speech": speech,
        "candidates": [],
        "error_class": error_class,
    }


def _plan_sentence(row: Any) -> str:
    steps = row.mission_json.get("steps") or []
    return "; ".join(f"{i + 1}. {s.get('label_tr', '')}" for i, s in enumerate(steps))


def _start(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    text = _request_text(ctx, arguments)
    if not text:
        return _clarification(SPEECH_NO_REQUEST, error_class="invalid_argument")
    if ctx.db is None:
        return _clarification(SPEECH_NO_DB, error_class="dependency_unavailable")
    preview_arg = arguments.get("preview")
    preview = bool(preview_arg) if isinstance(preview_arg, bool) else None
    if preview is None and any(m in text.lower() for m in PREVIEW_MARKERS):
        preview = True
    try:
        row = mission_service.start_mission_db(
            ctx.db, text=text, preview=preview, source=SOURCE_VOICE, session_id=str(ctx.session_id)
        )
    except MissionServiceError as exc:
        return _clarification(exc.speech, error_class=exc.error_class)

    artifacts_runtime = ctx.live.get("artifacts_runtime")
    mission_id = row.id
    task_queue = (
        artifacts_runtime.settings.temporal_task_queue if artifacts_runtime else "pagentos-core"
    )

    async def _start_workflow_followup() -> None:
        try:
            from app.research.service import connect_temporal

            client = await connect_temporal(artifacts_runtime)
            await mission_service.start_mission_workflow(client, mission_id, task_queue=task_queue)
        except Exception as exc:  # noqa: BLE001 - reported via the row, never raised here
            logger.exception("voice_mission_workflow_start_failed", mission_id=str(mission_id))
            if artifacts_runtime is not None:
                with artifacts_runtime.session() as db_run:
                    mission_service.fail_unstarted(
                        db_run, mission_id, detail=f"{type(exc).__name__}: {exc}"
                    )

    ctx.add_followup(_start_workflow_followup)
    plan = _plan_sentence(row)
    if row.status == MISSION_AWAITING_APPROVAL:
        speech = f"Planım şu efendim: {plan}. Başlayayım mı?"
        terminal = TERMINAL_UNVERIFIED
    else:
        speech = SPEECH_STARTED.format(plan=plan)
        terminal = TERMINAL_UNVERIFIED
    return _receipt(
        ctx,
        requested_state="started",
        execution=EXECUTION_EXECUTED,
        terminal=terminal,
        speech=speech,
        server={
            "mission_id": str(row.id),
            "status": row.status,
            "step_count": row.step_count,
            "preview": row.preview,
        },
        extra={"mission_id": str(row.id), "status": row.status},
    )


def _signal_followup(ctx: ToolContext, coro_factory: Any, mission_id: uuid.UUID) -> None:
    artifacts_runtime = ctx.live.get("artifacts_runtime")

    async def _followup() -> None:
        try:
            from app.research.service import connect_temporal

            client = await connect_temporal(artifacts_runtime)
            await coro_factory(client, mission_id)
        except Exception:  # noqa: BLE001 - the row already carries the owner's word
            logger.exception("voice_mission_signal_failed", mission_id=str(mission_id))

    ctx.add_followup(_followup)


def mission_control(ctx: ToolContext, action: str) -> dict[str, Any]:
    """The mission's control words, reachable from ``operator.cancel`` / ``operator.status``
    too (B39 req 129: one "Dur." for whatever the operator is doing)."""
    return _control(ctx, action)


def _control(ctx: ToolContext, action: str) -> dict[str, Any]:
    if ctx.db is None:
        return _clarification(SPEECH_NO_DB, error_class="dependency_unavailable")
    row = mission_service.active_mission(ctx.db)
    if row is None:
        return _receipt(
            ctx,
            requested_state=action,
            execution=EXECUTION_NOOP,
            terminal=TERMINAL_ALREADY,
            speech=SPEECH_NOTHING_ACTIVE,
            server={"active": False},
        )
    if action in OWNER_ONLY_ACTIONS and _turn_record(ctx).get("mission_action") != action:
        # A plan waiting for approval and an escalation asking "Nasıl devam edeyim?" both
        # wait for the OWNER. The router resolves their "Evet, başla" / "Devam et" to this
        # action only when the words were said and the mission is in that state; a model
        # passing action=resume on its own is not the owner answering. Production,
        # 2026-09-18: a step escalated at 19:00:30 and the model resumed it at 19:00:31 -
        # one second, no owner word between. Stopping (pause/cancel) and asking (status)
        # stay open to the model: those only ever make it do less.
        waiting = (row.mission_json.get("escalation") or {}).get("speech")
        if not waiting and row.status not in (MISSION_AWAITING_APPROVAL, MISSION_PAUSED):
            # Production 2026-09-19 09:40:01: the model "approved" a mission that was already
            # moving, and the owner heard "Görev sizin cevabınızı bekliyor" - untrue, nothing
            # waited for them. A yes to a mission that needs none is a no-op, said as one.
            return _receipt(
                ctx,
                requested_state=action,
                execution=EXECUTION_NOOP,
                terminal=TERMINAL_ALREADY,
                speech=SPEECH_ALREADY_MOVING,
                server={"mission_id": str(row.id), "status": row.status},
            )
        return _receipt(
            ctx,
            requested_state=action,
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            speech=str(waiting or SPEECH_WAITING_FOR_OWNER),
            server={"mission_id": str(row.id), "status": row.status},
            error_class=ERROR_OWNER_WORD_REQUIRED,
        )
    if action == "status":
        mission = row.mission_json
        steps = mission.get("steps") or []
        current = steps[row.current_step] if row.current_step < len(steps) else None
        if row.status == MISSION_AWAITING_APPROVAL:
            speech = f"Planı onayınızı bekliyor efendim: {_plan_sentence(row)}."
        elif row.status == MISSION_PAUSED and mission.get("escalation"):
            speech = str(mission["escalation"].get("speech") or "Durdum efendim.")
        elif row.status == MISSION_PAUSED:
            speech = f"Duraklatıldı efendim; {row.current_step + 1}. adımda bekliyorum."
        elif current is not None:
            label = current.get("label_tr", "")
            speech = f"{row.goal} işindeyim efendim; {row.current_step + 1}. adım: {label}."
        else:
            speech = f"{row.goal} işindeyim efendim."
        return {"speech": speech, "mission": mission_service.mission_dict(row)}

    db_half = {
        "approve": mission_service.approve_db,
        "pause": mission_service.pause_db,
        "resume": mission_service.resume_db,
        "cancel": mission_service.cancel_db,
    }[action]
    signal_half = {
        "approve": mission_service.approve_signal,
        "pause": mission_service.pause_signal,
        "resume": mission_service.resume_signal,
        "cancel": mission_service.cancel_signal,
    }[action]
    try:
        row = db_half(ctx.db, row.id)
    except MissionServiceError as exc:
        return _receipt(
            ctx,
            requested_state=action,
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            speech=exc.speech,
            server={"mission_id": str(row.id), "status": row.status},
            error_class=exc.error_class,
        )
    _signal_followup(ctx, signal_half, row.id)
    speech = {
        "approve": SPEECH_APPROVED,
        "pause": SPEECH_PAUSED,
        "resume": SPEECH_RESUMED,
        "cancel": SPEECH_CANCELLED,
    }[action]
    return _receipt(
        ctx,
        requested_state=action,
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED
        if action == "cancel" and row.status == "cancelled"
        else TERMINAL_UNVERIFIED,
        speech=speech,
        server={"mission_id": str(row.id), "status": row.status},
        extra={"mission_id": str(row.id), "status": row.status},
    )


def operator_mission(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    turn = _turn_record(ctx)
    action = str(arguments.get("action") or turn.get("mission_action") or "start").strip().lower()
    if action not in MISSION_ACTIONS:
        return _clarification(
            "Bu görev eylemini bilmiyorum efendim.", error_class="invalid_argument"
        )
    if action == "start":
        return _start(ctx, arguments)
    return _control(ctx, action)


def register_mission_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_MISSION,
            description=(
                "ÇOK ADIMLI bir masaüstü GÖREVİ başlatır ya da yönetir: 'Chrome'u aç ve "
                "YouTube'a gir', 'Ayarlarda Bluetooth'u aç', 'Dosya Gezgini'nde İndirilenler "
                "klasörünü aç', 'VS Code'da main.py dosyasını aç', 'Word'e merhaba yaz' - ve "
                "'... önce göster' denince plan önce söylenir. 'action' alanına start/approve/"
                "pause/resume/cancel/status'tan birini ver; 'content' alanına sahibin cümlesini "
                "aynen yaz. Tek adımlı basit istekler için (yalnız 'Not Defteri'ni aç') bu aracı "
                "DEĞİL ilgili operator.* aracını kullan. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(MISSION_ACTIONS)},
                    "content": {"type": "string", "maxLength": 600},
                    "preview": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            handler=operator_mission,
        )
    )
    return reg


__all__ = [
    "MISSION_ACTIONS",
    "TOOL_MISSION",
    "mission_control",
    "operator_mission",
    "register_mission_tools",
]
