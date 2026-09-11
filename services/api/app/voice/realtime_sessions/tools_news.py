"""Latest News Mode's voice tools (docs/M26_LATEST_NEWS_MODE_SPEC.md §5, §6, ADR-0092).

Three tools, registered from ``tools.default_registry()`` by ONE added line
(:func:`register_news_tools`), the same discipline ``tools_artifacts``/
``tools_documents`` already establish for their own families: ``news.open`` (a real
mutation: a browser opens, a video plays), ``news.summarize`` (delegates to the
existing research pipeline; never plays anything) and ``news.query_latest`` (the
resolver's own answer; mutates nothing).

The ONE router (``app.voice.intents``) extracts the deterministic part of a NEWS
utterance — the intent itself, and a channel-name HINT (``news_source_ref``, "Show'un
son haberini aç" -> "show'un") — onto ``ctx.context["last_utterance"]``; this module
PREFERS that hint over the model's own ``news_source_id`` argument (the same "owner's
words win" rule ``document_ref``/``artifact_ref`` already follow), matching it against
configured sources' display names — never inventing a channel identity itself (that
was already established, or refused, when the source was configured,
``app.news.identity``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_UNVERIFIED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_NEWS_CLOSED,
    EVENT_TYPE_NEWS_OPENED,
    EVENT_TYPE_NEWS_PLAYBACK_FAILED,
    EVENT_TYPE_NEWS_PLAYBACK_UNVERIFIED,
    EVENT_TYPE_NEWS_RESOLVED,
    EVENT_TYPE_NEWS_SUMMARIZED,
    SUBSYSTEM_NEWS,
)
from app.logging import get_logger
from app.news import sources_service
from app.news.models import CONTENT_TYPES, PLAYBACK_STATUS_PLAYING
from app.news.playback_service import close_playback, open_latest_news
from app.news.resolve_service import NewsResolveError, resolve_for_source
from app.news.sources_service import NewsSourceView
from app.news.summary import summary_topic
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.realtime_sessions.tools_news")

TOOL_NEWS_OPEN: Final = "news.open"
TOOL_NEWS_CLOSE: Final = "news.close"
TOOL_NEWS_SUMMARIZE: Final = "news.summarize"
TOOL_NEWS_QUERY_LATEST: Final = "news.query_latest"

NEWS_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_NEWS_OPEN,
    TOOL_NEWS_CLOSE,
    TOOL_NEWS_SUMMARIZE,
    TOOL_NEWS_QUERY_LATEST,
)

SPEECH_NO_SOURCE_CONFIGURED = "Tanımlı ve kanal kimliği doğrulanmış bir haber kaynağı yok efendim."
SPEECH_SOURCE_NOT_FOUND = "Öyle bir haber kaynağı bulamadım efendim."
SPEECH_NO_ELIGIBLE_VIDEO = "Şu anda açabileceğim uygun bir haber videosu bulamadım efendim."

ERROR_NO_SOURCE = "no_news_source"
ERROR_SOURCE_NOT_FOUND = "news_source_not_found"


def _turn_record(ctx: ToolContext) -> dict[str, Any]:
    """The ONE router's record of this turn — the owner's WORDS, preferred over the
    model's own argument, the same rule every other M19+ tool family follows."""
    return dict(ctx.context.get("last_utterance") or {})


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _match_source_by_ref(sources: list[NewsSourceView], ref: str) -> NewsSourceView | None:
    """Fuzzy-match a spoken channel-name HINT against configured sources' display
    names or their own slug — never against a channel identity (that is a database
    fact, resolved once, never re-guessed per utterance). A Turkish possessive suffix
    on the hint ("show'un") is tolerated by a simple prefix/substring check on both
    sides; anything that does not match at all returns ``None`` (a real refusal, not a
    silent default) so the owner is never handed a DIFFERENT channel than the one they
    named."""
    core = ref.split("'")[0].strip().lower()
    if not core:
        return None
    for source in sources:
        name = source.display_name.lower()
        slug = source.news_source_id.lower()
        first_word = name.split()[0] if name else ""
        if core in name or (first_word and first_word in core):
            return source
        if core in slug or slug in core:
            return source
    return None


def _resolve_target_source(
    ctx: ToolContext, arguments: dict[str, Any], *, tool: str
) -> tuple[NewsSourceView | None, bool]:
    """(source, named_but_not_found). The owner's spoken channel-name hint wins over
    the model's own ``news_source_id`` argument; with neither, the default configured
    source (lowest-priority enabled+resolved) answers. ``named_but_not_found`` is True
    only when a hint or an explicit id named something that does not match ANY
    configured source — the caller then refuses rather than silently opening a
    different channel."""
    db = _require_db(ctx, tool)
    turn = _turn_record(ctx)
    sources = sources_service.list_sources(db, enabled_only=True)
    ref = turn.get("news_source_ref") if isinstance(turn.get("news_source_ref"), str) else None
    if ref:
        matched = _match_source_by_ref(sources, ref)
        return matched, matched is None
    explicit_id = arguments.get("news_source_id")
    if isinstance(explicit_id, str) and explicit_id:
        found = next((s for s in sources if s.news_source_id == explicit_id), None)
        return found, found is None
    return sources_service.default_source(db), False


def _ledger(db: Any, *, event_type: str, action: str, summary: str, detail: dict[str, Any]) -> None:
    if db is None:
        return
    try:
        ledger_service.record(
            db,
            ledger_service.ActivityEvent(
                event_type=event_type,
                subsystem=SUBSYSTEM_NEWS,
                action=action,
                factual_summary=summary,
                occurred_at=datetime.now(UTC),
                detail_json=detail,
                source="live",
                source_ref=f"{action}:{uuid.uuid4()}",
            ),
        )
    except Exception:  # noqa: BLE001 - evidence, never a dependency of the action
        logger.warning("news_ledger_failed", action=action)


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
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    receipt = ActionReceipt(
        action_id=ctx.call_id or str(uuid.uuid4()),
        capability=capability,
        requested_state=requested_state,
        execution_status=execution,
        terminal_status=terminal,
        observed_after={"server": server, "local": {}},
        evidence_refs=[],
        error_class=error_class,
        speech=speech,
        started_at=now,
        completed_at=now,
        session_id=str(ctx.session_id),
        observed_at=now,
    )
    if ctx.db is not None:
        record_receipt(ctx.db, receipt, SUBSYSTEM_NEWS)
    out = receipt.as_dict()
    if extra:
        out.update(extra)
    return out


# ------------------------------------------------------------------ news.open


def news_open(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Haberleri aç.", "Son haberleri aç.", "Show'un son haberini aç." (spec §6):
    resolve the latest eligible video and play it in the browser worker's OWN ``news``
    profile/context (never the research or alarm browser). ``browser.session_open``
    succeeding is NOT proof a video is playing — the receipt below is built from
    ``browser.media_play``'s own ``verified`` field, never upgraded."""
    db = _require_db(ctx, TOOL_NEWS_OPEN)
    source, named_not_found = _resolve_target_source(ctx, arguments, tool=TOOL_NEWS_OPEN)
    if named_not_found:
        return _receipt(
            ctx,
            capability=TOOL_NEWS_OPEN,
            requested_state="opened",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_SOURCE_NOT_FOUND},
            speech=SPEECH_SOURCE_NOT_FOUND,
            error_class=ERROR_SOURCE_NOT_FOUND,
        )
    if source is None or source.channel_id is None:
        return _receipt(
            ctx,
            capability=TOOL_NEWS_OPEN,
            requested_state="opened",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_NO_SOURCE},
            speech=SPEECH_NO_SOURCE_CONFIGURED,
            error_class=ERROR_NO_SOURCE,
        )
    content_type = arguments.get("content_type")
    content_type = content_type if content_type in CONTENT_TYPES else None
    device_action = ctx.live.get("device_action")
    news_provider = ctx.live.get("news_provider")
    outcome = open_latest_news(
        db, device_action, source=source, content_type=content_type, provider=news_provider
    )
    if outcome.ok:
        execution, terminal, event_type = (
            EXECUTION_EXECUTED,
            TERMINAL_VERIFIED,
            EVENT_TYPE_NEWS_OPENED,
        )
    elif outcome.status == PLAYBACK_STATUS_PLAYING:  # pragma: no cover - defensive
        execution, terminal, event_type = (
            EXECUTION_EXECUTED,
            TERMINAL_UNVERIFIED,
            EVENT_TYPE_NEWS_PLAYBACK_UNVERIFIED,
        )
    elif outcome.error_class == "playback_unverified":
        execution, terminal, event_type = (
            EXECUTION_EXECUTED,
            TERMINAL_UNVERIFIED,
            EVENT_TYPE_NEWS_PLAYBACK_UNVERIFIED,
        )
    else:
        execution, terminal, event_type = (
            EXECUTION_REFUSED,
            TERMINAL_FAILED,
            EVENT_TYPE_NEWS_PLAYBACK_FAILED,
        )
    _ledger(
        db,
        event_type=event_type,
        action=TOOL_NEWS_OPEN,
        summary=f"news.open -> {outcome.status} ({source.news_source_id})",
        detail={
            "news_source_id": source.news_source_id,
            "context_id": outcome.context_id,
            "video_id": outcome.video_id,
            "status": outcome.status,
        },
    )
    return _receipt(
        ctx,
        capability=TOOL_NEWS_OPEN,
        requested_state="opened",
        execution=execution,
        terminal=terminal,
        server={
            "context_id": outcome.context_id,
            "news_source_id": source.news_source_id,
            "video_id": outcome.video_id,
            "status": outcome.status,
        },
        speech=outcome.speech,
        error_class=outcome.error_class,
        extra={
            "context_id": outcome.context_id,
            "news_source_id": source.news_source_id,
            "channel_id": source.channel_id,
            "video_id": outcome.video_id,
            "title": outcome.title,
            "published_at": outcome.published_at,
        },
    )


# ------------------------------------------------------------- news.close (cleanup)


def news_close(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Haberi kapat." — cleanup closes ONLY the named context's own session (spec
    §5), never another one and never the alarm/research browser."""
    db = _require_db(ctx, TOOL_NEWS_CLOSE)
    # NOT "context_id": the relay refuses any argument key that normalizes to contain
    # "text" (app.voice.realtime_sessions.service.FORBIDDEN_KEY_PARTS - transcripts and
    # credentials never ride a tool call), and "con-TEXT-_id" trips it. "playback_id"
    # names the exact same NewsPlaybackContextRow id without the collision.
    raw = arguments.get("playback_id")
    try:
        context_id = uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return _receipt(
            ctx,
            capability=TOOL_NEWS_CLOSE,
            requested_state="closed",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": "invalid_playback_id"},
            speech="Hangi haberi kapatacağımı bulamadım efendim.",
            error_class="invalid_playback_id",
        )
    device_action = ctx.live.get("device_action")
    outcome = close_playback(db, device_action, context_id=context_id)
    _ledger(
        db,
        event_type=EVENT_TYPE_NEWS_CLOSED,
        action=TOOL_NEWS_CLOSE,
        summary=f"news.close -> {outcome.status}",
        detail={"context_id": outcome.context_id},
    )
    return _receipt(
        ctx,
        capability=TOOL_NEWS_CLOSE,
        requested_state="closed",
        execution=EXECUTION_EXECUTED if outcome.ok else EXECUTION_REFUSED,
        terminal=TERMINAL_VERIFIED if outcome.ok else TERMINAL_FAILED,
        server={"context_id": outcome.context_id, "status": outcome.status},
        speech=outcome.speech,
        error_class=outcome.error_class,
    )


# ------------------------------------------------------------------- news.summarize


def news_summarize(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Haberleri özetle.", "Bugünkü haberleri özetle." (spec §6): a current-events
    SUMMARY through the EXISTING research pipeline — never a second research engine,
    and this tool never opens a browser or plays anything."""
    db = _require_db(ctx, TOOL_NEWS_SUMMARIZE)
    source, _ = _resolve_target_source(ctx, arguments, tool=TOOL_NEWS_SUMMARIZE)
    topic = summary_topic(source)

    artifacts_runtime = ctx.live.get("artifacts_runtime")
    voice_runtime = ctx.live.get("voice_runtime")
    broker_runtime = ctx.live.get("broker_runtime")
    if artifacts_runtime is None or voice_runtime is None or broker_runtime is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            "news.summarize needs the artifact runtime, the broker runtime and the "
            "realtime voice runtime",
        )

    from app.research import service as research_service

    started = research_service.start_browser_research(
        db,
        broker_runtime,
        input=topic,
        recency_days=1,
        source=research_service.SOURCE_VOICE,
        session_id=ctx.session_id,
        tool_call_id=ctx.call_id,
    )
    if started.error is not None:
        raise VoiceError(
            VoiceErrorClass.CAPABILITY_MISSING,
            started.error,
            details={"speech": started.error, "task_id": str(started.task_id)},
        )

    session_id = ctx.session_id
    call_id = ctx.call_id
    task_id = started.task_id
    workflow_id = started.workflow_id
    max_sources = artifacts_runtime.settings.research_default_max_sources
    synthesis = artifacts_runtime.settings.research_default_synthesis
    search_provider = artifacts_runtime.settings.research_search_provider

    async def _start_workflow_followup() -> None:
        try:
            client = await research_service.connect_temporal(artifacts_runtime)
            await research_service.start_browser_research_workflow(
                client,
                artifacts_runtime,
                task_id=task_id,
                workflow_id=workflow_id,
                input=topic,
                recency_days=1,
                max_sources=max_sources,
                synthesis=synthesis,
                search_provider=search_provider,
            )
        except Exception as exc:  # noqa: BLE001 - reported as a failed tool call, never raised here
            logger.exception("news_summarize_workflow_start_failed", task_id=str(task_id))
            from app.voice.realtime_sessions.models import RealtimeSessionRow
            from app.voice.realtime_sessions.service import complete_tool_call_system

            # Phase 8: the task opened for this run must not stay CREATED for ever.
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
                            "error_class": "news_summarize_workflow_start_failed",
                            "message": "Haberleri özetleyemedim; arka plan servisine ulaşamadım.",
                            "speech": "Haberleri özetleyemedim; arka plan servisine ulaşamadım.",
                        },
                        sideband=voice_runtime.sideband,
                        trace_id=None,
                    )

    ctx.add_followup(_start_workflow_followup)
    _ledger(
        db,
        event_type=EVENT_TYPE_NEWS_SUMMARIZED,
        action=TOOL_NEWS_SUMMARIZE,
        summary=f"news.summarize -> task {task_id}",
        detail={"task_id": str(task_id), "topic": topic},
    )
    return {
        "status": "running",
        "task_id": str(task_id),
        "workflow_id": workflow_id,
        "topic": topic,
        "device": started.device,
        "speech": "Bakıyorum efendim, güncel haberleri araştırıyorum.",
    }


# --------------------------------------------------------------- news.query_latest


def news_query_latest(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Son haber ne zaman yüklenmiş?", "Şu an hangi haber videosunu açacaksın?"
    (spec §6): the resolver's own decision, WITHOUT opening anything."""
    db = _require_db(ctx, TOOL_NEWS_QUERY_LATEST)
    source, named_not_found = _resolve_target_source(ctx, arguments, tool=TOOL_NEWS_QUERY_LATEST)
    if named_not_found:
        return _receipt(
            ctx,
            capability=TOOL_NEWS_QUERY_LATEST,
            requested_state="queried",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_SOURCE_NOT_FOUND},
            speech=SPEECH_SOURCE_NOT_FOUND,
            error_class=ERROR_SOURCE_NOT_FOUND,
        )
    if source is None or source.channel_id is None:
        return _receipt(
            ctx,
            capability=TOOL_NEWS_QUERY_LATEST,
            requested_state="queried",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_NO_SOURCE},
            speech=SPEECH_NO_SOURCE_CONFIGURED,
            error_class=ERROR_NO_SOURCE,
        )
    content_type = arguments.get("content_type")
    content_type = content_type if content_type in CONTENT_TYPES else None
    news_provider = ctx.live.get("news_provider")
    try:
        outcome = resolve_for_source(db, source, content_type=content_type, provider=news_provider)
    except NewsResolveError as exc:
        return _receipt(
            ctx,
            capability=TOOL_NEWS_QUERY_LATEST,
            requested_state="queried",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": exc.error_class},
            speech=SPEECH_NO_ELIGIBLE_VIDEO,
            error_class=exc.error_class,
        )
    result = outcome.result
    if result.selected is None:
        speech = SPEECH_NO_ELIGIBLE_VIDEO
    else:
        published = result.selected.published_at.isoformat()
        speech = f"{result.selected.title}; {published} tarihinde yüklenmiş."
    _ledger(
        db,
        event_type=EVENT_TYPE_NEWS_RESOLVED,
        action=TOOL_NEWS_QUERY_LATEST,
        summary=f"news.query_latest -> {result.reason}",
        detail={"news_source_id": source.news_source_id, "reason": result.reason},
    )
    return _receipt(
        ctx,
        capability=TOOL_NEWS_QUERY_LATEST,
        requested_state="queried",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        server={
            "news_source_id": source.news_source_id,
            "reason": result.reason,
            "answered_by": result.answered_by,
            "ambiguous": result.ambiguous,
        },
        speech=speech,
        extra={
            "resolution_id": outcome.row_id,
            "news_source_id": source.news_source_id,
            "channel_id": source.channel_id,
            "answered_by": result.answered_by,
            "video_id": result.selected.video_id if result.selected else None,
            "title": result.selected.title if result.selected else None,
            "published_at": result.selected.published_at.isoformat() if result.selected else None,
        },
    )


# ------------------------------------------------------------------ registration


def register_news_tools(reg: ToolRegistry) -> ToolRegistry:
    """Register all three tools (module docstring: ONE line in ``default_registry``)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_NEWS_OPEN,
            description=(
                "Yapılandırılmış bir haber kaynağının EN SON uygun videosunu açar ve "
                "oynatır: 'haberleri aç', 'son haberleri aç', 'show'un son haberini "
                "aç'. Hangi kaynak ve hangi video olduğunu SUNUCU çözer (gerçek yükleme "
                "zamanına göre, arama sıralamasına göre DEĞİL); 'news_source_id' "
                "alanına sahibin söylediği kanal adını (varsa) ver. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "news_source_id": {"type": "string", "maxLength": 64},
                    "content_type": {
                        "type": "string",
                        "enum": list(CONTENT_TYPES),
                    },
                },
                "additionalProperties": False,
            },
            handler=news_open,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_NEWS_CLOSE,
            description=(
                "Az önce açılan haber videosunu kapatır: 'haberi kapat', 'videoyu "
                "kapat'. 'playback_id' alanına news.open'ın döndürdüğü context_id "
                "değerini ver. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"playback_id": {"type": "string", "maxLength": 64}},
                "required": ["playback_id"],
                "additionalProperties": False,
            },
            handler=news_close,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_NEWS_SUMMARIZE,
            description=(
                "Güncel haberleri ARAŞTIRMA hattı üzerinden ÖZETLER; hiçbir video "
                "AÇMAZ ve OYNATMAZ: 'haberleri özetle', 'bugünkü haberleri özetle'. "
                "Dönen 'speech' metnini aynen oku; sonuç arka planda hazırlanır."
            ),
            parameters={
                "type": "object",
                "properties": {"news_source_id": {"type": "string", "maxLength": 64}},
                "additionalProperties": False,
            },
            handler=news_summarize,
            long_running=True,
            preamble="Bakıyorum efendim, güncel haberleri araştırıyorum.",
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_NEWS_QUERY_LATEST,
            description=(
                "Bir şey AÇMADAN, en son uygun haber videosunun ne zaman yüklendiğini "
                "ve hangisi olduğunu söyler: 'son haber ne zaman yüklenmiş?', 'şu an "
                "hangi haber videosunu açacaksın?'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "news_source_id": {"type": "string", "maxLength": 64},
                    "content_type": {"type": "string", "enum": list(CONTENT_TYPES)},
                },
                "additionalProperties": False,
            },
            handler=news_query_latest,
        )
    )
    return reg


__all__ = [
    "NEWS_TOOL_NAMES",
    "TOOL_NEWS_CLOSE",
    "TOOL_NEWS_OPEN",
    "TOOL_NEWS_QUERY_LATEST",
    "TOOL_NEWS_SUMMARIZE",
    "news_close",
    "news_open",
    "news_query_latest",
    "news_summarize",
    "register_news_tools",
]
