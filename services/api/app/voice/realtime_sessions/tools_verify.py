"""Doğrula modu's two voice tools (card verify-mode).

``research.verify`` - "bunu doğrula: ...": starts the SAME M13 browser research a spoken
"araştır" starts (``app.research.service.start_browser_research``), over a 365-day window (a
fact check is about whatever date the claim names, never the 3-day news default), and writes a
``pending`` row in ``claim_verifications`` beside it. The call stays RUNNING; when the run is
terminal ``research_announcer`` settles the row (``app.research.verify.settle_task``) and the
owner hears the verdict with its source.

``research.verify_recall`` - "geçen hafta neyi doğrulamıştık", "Everest hakkında ne
bulmuştuk": settles whatever has finished, then reads the table back by subject and by the
owner's own calendar week (Istanbul, never UTC midnight).

The trigger is ALWAYS the owner's own sentence: nothing here listens to anyone else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.logging import get_logger
from app.research import verify
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.realtime_sessions.tools_verify")

TOOL_VERIFY: Final = "research.verify"
TOOL_VERIFY_RECALL: Final = "research.verify_recall"

#: ADR decision 6: the REST ceiling; a claim's subject may be months old.
VERIFY_RECENCY_DAYS: Final = 365
NO_CLAIM_TR: Final = "Neyi doğrulamamı istediğinizi anlayamadım; iddiayı söyler misiniz?"
VERIFY_NO_DEVICE_TR: Final = "Doğrulama için tarayıcı yeteneği olan bir cihaz yok."
VERIFY_WORKFLOW_FAILED_TR: Final = "Doğrulamayı başlatamadım; arka plan servisine ulaşamadım."
ERROR_VERIFY_WORKFLOW_START_FAILED: Final = "verify_workflow_start_failed"


def _turn(ctx: ToolContext) -> dict[str, Any]:
    from app.voice.realtime_sessions.tools import _turn_record

    return _turn_record(ctx) or {}


def _spoken_claim(ctx: ToolContext, arguments: dict[str, Any]) -> tuple[str, str]:
    """(said, claim): the model's ``claim`` when there is a model, else the router's reading
    of the owner's own sentence (ADR-0173 local mode; ``intents.research_topic_of``)."""
    claim = str(arguments.get("claim") or "").strip()
    turn = _turn(ctx)
    said = str(turn.get("research_topic") or "").strip()
    if str(turn.get("intent") or "") != "verify_claim":
        said = ""
    if not claim:
        claim = said
    claim = verify.normalise_claim(claim)
    return (said or claim), claim


def research_verify(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    said, claim = _spoken_claim(ctx, arguments)
    if not claim:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR, NO_CLAIM_TR, details={"speech": NO_CLAIM_TR}
        )
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            "research.verify needs the database; no database on this session",
        )
    artifacts_runtime = ctx.live.get("artifacts_runtime")
    voice_runtime = ctx.live.get("voice_runtime")
    broker_runtime = ctx.live.get("broker_runtime")
    if artifacts_runtime is None or voice_runtime is None or broker_runtime is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            "research.verify needs the artifact runtime, the broker runtime and the "
            "realtime voice runtime",
        )

    from app.devices import aliases as device_aliases
    from app.research import service as research_service
    from app.research.policy import MODE_QUICK
    from app.voice.realtime_sessions.tools import _voice_max_sources

    named_devices = device_aliases.targets_of_turn(_turn(ctx) or None)
    max_sources = _voice_max_sources(
        MODE_QUICK, artifacts_runtime.settings.research_default_max_sources
    )
    started = research_service.start_browser_research(
        ctx.db,
        broker_runtime,
        input=claim,
        recency_days=VERIFY_RECENCY_DAYS,
        max_sources=max_sources,
        trace_id=None,
        source=research_service.SOURCE_VOICE,
        session_id=ctx.session_id,
        tool_call_id=ctx.call_id,
        session_device_ids=ctx.live.get("session_device_ids"),
        named_devices=named_devices,
    )
    if started.error is not None:
        speech = started.error if named_devices else VERIFY_NO_DEVICE_TR
        raise VoiceError(
            VoiceErrorClass.CAPABILITY_MISSING,
            VERIFY_NO_DEVICE_TR,
            details={"speech": speech, "task_id": str(started.task_id)},
        )
    row = verify.start_pending(ctx.db, said=said, claim=claim, task_id=started.task_id, now=ctx.now)

    session_id = ctx.session_id
    call_id = ctx.call_id
    task_id = started.task_id
    workflow_id = started.workflow_id
    settings = artifacts_runtime.settings
    workflow_target = named_devices[0] if named_devices else None

    async def _start_workflow_followup() -> None:
        try:
            client = await research_service.connect_temporal(artifacts_runtime)
            await research_service.start_browser_research_workflow(
                client,
                artifacts_runtime,
                task_id=task_id,
                workflow_id=workflow_id,
                input=claim,
                target_device=workflow_target,
                recency_days=VERIFY_RECENCY_DAYS,
                max_sources=max_sources,
                synthesis=settings.research_default_synthesis,
                search_provider=settings.research_search_provider,
                mode=MODE_QUICK,
            )
        except Exception as exc:  # noqa: BLE001 - reported as a failed tool call, never raised here
            logger.exception("voice_verify_workflow_start_failed", task_id=str(task_id))
            from app.voice.realtime_sessions.models import RealtimeSessionRow
            from app.voice.realtime_sessions.service import complete_tool_call_system

            with artifacts_runtime.session() as db_task:
                research_service.fail_unstarted_research(
                    db_task, task_id, detail=f"{type(exc).__name__}: {exc}"
                )
            with voice_runtime.session() as db2:
                row2 = db2.get(RealtimeSessionRow, session_id)
                if row2 is not None:
                    complete_tool_call_system(
                        db2,
                        row2,
                        call_id=call_id,
                        result=None,
                        error={
                            "error_class": ERROR_VERIFY_WORKFLOW_START_FAILED,
                            "message": VERIFY_WORKFLOW_FAILED_TR,
                            "speech": VERIFY_WORKFLOW_FAILED_TR,
                        },
                        sideband=voice_runtime.sideband,
                        trace_id=None,
                    )

    ctx.add_followup(_start_workflow_followup)
    return {
        "status": "running",
        "speech": verify.STARTED_TR,
        "verification_id": str(row.id),
        "claim": claim,
        "task_id": str(task_id),
        "workflow_id": workflow_id,
        "device": started.device,
        "recency_days": VERIFY_RECENCY_DAYS,
    }


def research_verify_recall(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            "research.verify_recall needs the database; no database on this session",
        )
    phrase = str(arguments.get("query") or "").strip()
    if not phrase:
        turn = _turn(ctx)
        if str(turn.get("intent") or "") == "verify_recall":
            phrase = str(turn.get("research_topic") or "").strip()
    from app.briefing.service import local_now

    since, until = verify.recall_window(phrase, local_now(ctx.now))
    subject = verify.recall_subject(phrase)
    verify.settle_pending(ctx.db, now=ctx.now)
    rows = verify.search(ctx.db, text=subject, since=since, until=until)
    return {
        "speech": verify.recall_speech(rows),
        "subject": subject,
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "verifications": [verify.row_as_dict(r) for r in rows[:5]],
        "count": len(rows),
    }


def register_verify_tools(reg: ToolRegistry) -> ToolRegistry:
    """Registered one by one with literal names (``app.selfmodel.indexer`` reads them)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name="research.verify",
            description=(
                "Sahibin duyduğu bir iddiayı doğrular: 'bunu doğrula: ...', '... olduğu doğru "
                "mu?', 'şunu kontrol et: ...'. Kaynak tarar; hüküm (doğru / yanlış / kısmen / "
                "belirsiz), kaynak ve karşı argüman araştırma bitince söylenir. Yalnız sahibin "
                "kendi cümlesiyle çağrılır. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "claim": {
                        "type": "string",
                        "description": "Doğrulanacak iddia, sahibin söylediği gibi.",
                    }
                },
                "required": ["claim"],
                "additionalProperties": False,
            },
            handler=research_verify,
            long_running=True,
            preamble=verify.STARTED_TR,
        )
    )
    reg.register(
        ToolSpec(
            name="research.verify_recall",
            description=(
                "Daha önce doğrulananları geri okur: 'geçen hafta neyi doğrulamıştık', "
                "'Everest hakkında ne bulmuştuk'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Sahibin cümlesi: konu ve/veya zaman (geçen hafta, dün).",
                    }
                },
                "additionalProperties": False,
            },
            handler=research_verify_recall,
        )
    )
    return reg
