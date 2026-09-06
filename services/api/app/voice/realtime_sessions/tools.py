"""The tool manifest a realtime session may call (M12 spec §4, §6).

A tool is a Cloud Core capability exposed to the provider through the client
relay. Every handler runs in Cloud Core under the owner session that created
the voice session (spec §9); the provider never holds owner authority.

``long_running`` tools return ``{"status": "running", "preamble": ...}`` at
once — the Turkish preamble the assistant speaks while the work continues —
and complete later via the sideband (``service.complete_tool_call``). A
mid-task redirect ("Sadece OpenAI kısmına bak") is ``plan.redirect`` on the
SAME plan: cancel-and-replan, never a disconnected conversation.

Handlers are small and deterministic; the real research pipeline is M13 and
plugs in behind ``research.start`` without changing this contract.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.narration.commands import State
from app.narration.engine import PARAGRAPH_LIST
from app.state.now import SCOPES
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.intents import (
    RESEARCH_CLASSES_BOUND_TO_A_RUN,
    Intent,
    apply_to_narration,
    resolve_intent,
    speech_budget,
    speech_from,
)
from app.voice.providers import cloud_tool_name
from app.voice.realtime import RealtimeState
from app.voice.realtime_sessions import actions
from app.voice.realtime_sessions.sideband import SB_NARRATION_CURSOR, SB_PLAN_CHANGED
from app.voice.realtime_sessions.tools_ambient import register_ambient_tools

logger = get_logger("app.voice.realtime_sessions.tools")

RESEARCH_PREAMBLE_TR = (
    "Bakıyorum. OpenAI, Anthropic, Google ve önemli açık kaynak gelişmelerini karşılaştıracağım."
)

#: docs/DECISIONS.md ADR-0067 amendment (M18.2 follow-up): the honest answer when the
#: pipeline finds no browser-capable device, and when a mid-run redirect is asked for
#: on a run the pipeline has no signal to redirect. Named here (not inline) so the unit
#: tests assert the exact sentence rather than a substring.
RESEARCH_START_NO_DEVICE_TR = "Araştırma için tarayıcı yeteneği olan bir cihaz yok."
RESEARCH_START_DB_MISSING_TR = "research.start needs the database; no database on this session"
PLAN_REDIRECT_REFUSED_TR = (
    "Bu araştırma çalışırken kapsamı değiştiremiyorum; bitince yeni bir araştırma başlatabilirim."
)
RESEARCH_START_WORKFLOW_FAILED_TR = "Araştırmayı başlatamadım; arka plan servisine ulaşamadım."

#: Server-side error classes specific to research.start (contract-facing; mirrors the
#: naming style of app.actions.receipt's ERROR_* constants).
ERROR_NO_CAPABLE_DEVICE = "no_capable_device"
ERROR_RESEARCH_WORKFLOW_START_FAILED = "research_workflow_start_failed"

#: docs/DECISIONS.md ADR-0075. The owner asked "Teknik anlat." after a completed
#: research and the model called activity.explain AND research.start - a second crawl
#: nobody asked for. The honest refusal, in the same shape as plan.redirect's
#: (ADR-0067 amendment): the tool CALL succeeds, and what it returns says plainly that
#: no research was started and what is being answered instead.
REASON_RESEARCH_FOLLOWUP_TURN = "research_followup_turn"
RESEARCH_FOLLOWUP_REFUSED_TR = (
    "Yeni bir araştırma başlatmadım efendim; son araştırmanın sonuçlarını anlatıyorum."
)

#: The tools that can cause a crawl. The guard is keyed on this set rather than on one
#: tool name so a future crawl-starting tool inherits the refusal by being added here,
#: and so the model's CHOICE of tool cannot route around it.
CRAWL_STARTING_TOOLS: frozenset[str] = frozenset({"research.start"})


@dataclass
class ToolContext:
    """What a handler may see and touch. ``context`` is the session's
    ``context_json`` (mutated in place; the service persists it); ``pushes``
    collects sideband messages the service delivers after the transaction."""

    session_id: uuid.UUID
    owner_session_id: uuid.UUID
    device_id: uuid.UUID | None
    client_kind: str
    context: dict[str, Any]
    db: Session | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    pushes: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    #: The provider's call id: an action receipt's ``action_id`` (contract §5.5).
    call_id: str | None = None
    #: In-process live runtimes a handler may read (``presence_runtime``,
    #: ``broker_runtime``, ``health``, ``artifacts_runtime``, ``voice_runtime``),
    #: injected by the service the way the World Model routes inject theirs - never
    #: imported as singletons here (contract §4).
    live: dict[str, Any] = field(default_factory=dict)
    #: Work a handler cannot do synchronously inside this DB transaction because it is
    #: only ever a coroutine (M18.2 follow-up to ADR-0067: starting a Temporal workflow
    #: from ``research.start``). The realtime-session ROUTE awaits each of these, in
    #: order, once ``handle_tool_call``'s transaction has committed and the tool-call
    #: response has been built - never inside the transaction itself, and never by the
    #: handler directly (a sync handler cannot await anything).
    followups: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    def push(self, event: str, payload: dict[str, Any]) -> None:
        self.pushes.append((event, dict(payload)))

    def add_followup(self, followup: Callable[[], Awaitable[None]]) -> None:
        self.followups.append(followup)

    @property
    def fsm_state(self) -> RealtimeState | None:
        raw = self.context.get("fsm_state")
        try:
            return RealtimeState(raw) if raw else None
        except ValueError:
            return None


ToolHandler = Callable[[ToolContext, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    long_running: bool = False
    preamble: str | None = None

    def manifest_entry(self) -> dict[str, Any]:
        entry = {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "long_running": self.long_running,
        }
        if self.preamble:
            entry["preamble"] = self.preamble
        return entry


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        """By Cloud Core name, or by the vendor spelling a client relays verbatim
        (``research__start`` -> ``research.start``; see ``vendor_tool_name``)."""
        spec = self._tools.get(name)
        if spec is None:
            spec = self._tools.get(cloud_tool_name(name))
        return spec

    def names(self) -> list[str]:
        return sorted(self._tools)

    def manifest(self) -> list[dict[str, Any]]:
        return [self._tools[n].manifest_entry() for n in self.names()]


# ------------------------------------------------------------------ handlers


def _require_str(arguments: dict[str, Any], key: str, *, max_len: int = 2000) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR, f"argument {key!r} must be a non-empty string"
        )
    return value.strip()[:max_len]


def clock_now(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"now": ctx.now.isoformat().replace("+00:00", "Z"), "timezone": "UTC"}


def voice_intent(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    utterance = _require_str(arguments, "utterance", max_len=1000)
    resolved = resolve_intent(utterance, session_state=ctx.fsm_state)
    ctx.context["last_intent"] = resolved.intent.value
    return resolved.to_dict()


def research_start(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """Starts the REAL M13 research pipeline (docs/DECISIONS.md ADR-0067 amendment):
    the same task-creation + device-selection ``app.research.routes.create_research``
    performs, run here inside this tool call's own DB transaction, so a spoken
    "Araştır" and a REST-initiated research are the same run from the first row
    written on — never a fabricated local plan the pipeline never heard about.

    ``Client.start_workflow`` only exists as a coroutine; this handler is sync (it
    runs inside ``handle_tool_call``'s transaction), so starting the workflow is
    handed to ``ctx.followups`` for the realtime-session ROUTE to await once this
    transaction has committed. A failure there completes the RUNNING call as FAILED
    with a truthful Turkish ``speech`` (never leaves it running forever). No capable
    device is an immediate, truthful failure raised from HERE — never a "running"
    the pipeline will never make good on.
    """
    topic = _require_str(arguments, "topic", max_len=500)
    scope = str(arguments.get("scope") or "genel")[:500]
    if ctx.db is None:
        raise VoiceError(VoiceErrorClass.DEPENDENCY_UNAVAILABLE, RESEARCH_START_DB_MISSING_TR)
    artifacts_runtime = ctx.live.get("artifacts_runtime")
    voice_runtime = ctx.live.get("voice_runtime")
    broker_runtime = ctx.live.get("broker_runtime")
    if artifacts_runtime is None or voice_runtime is None or broker_runtime is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            "research.start needs the artifact runtime, the broker runtime and the "
            "realtime voice runtime",
        )

    from app.research import service as research_service
    from app.research.dates import default_window, parse_recency_window
    from app.research.plan import DEFAULT_RECENCY_DAYS
    from app.research.policy import derive_mode_from_utterance

    # "son üç gündeki ..." -> 3 (app.research.dates, the same Turkish relative-date
    # parser the REST plan stage uses); an int day count is the only shape the
    # request's own recency_days override understands, so an hour/week/month phrase
    # is left to the workflow's own build_plan to parse from the topic text instead
    # of being lossily rounded into days here.
    window = parse_recency_window(topic, now=ctx.now) or default_window(
        ctx.now, days=DEFAULT_RECENCY_DAYS
    )
    recency_days = window.amount if window.unit == "day" else None

    # M18.2 (ADR-0068, owner rule 1): "never silently choose DEEP" — the mode comes
    # from the owner's own words only. research.start's schema has no separate
    # "utterance" argument (only topic/scope), so both are read together: the
    # owner's explicit "kapsamlı/derinlemesine/detaylı araştır" or "geniş/
    # karşılaştırmalı" most often lands in one of the two once the model extracts a
    # topic — never guessed at, never chosen because the run happens to look big.
    mode = derive_mode_from_utterance(f"{topic} {scope}")

    started = research_service.start_browser_research(
        ctx.db,
        broker_runtime,
        input=topic,
        recency_days=recency_days,
        max_sources=artifacts_runtime.settings.research_default_max_sources,
        trace_id=None,
        source=research_service.SOURCE_VOICE,
        session_id=ctx.session_id,
        tool_call_id=ctx.call_id,
    )
    if started.error is not None:
        raise VoiceError(
            VoiceErrorClass.CAPABILITY_MISSING,
            RESEARCH_START_NO_DEVICE_TR,
            details={"speech": RESEARCH_START_NO_DEVICE_TR, "task_id": str(started.task_id)},
        )

    plan = {
        "plan_id": str(started.task_id),
        "kind": "research",
        "topic": topic,
        "scope": scope,
        "status": "running",
        "revision": 1,
        "redirects": [],
        "steps": ["kaynakları topla", "karşılaştır", "yönetici özeti çıkar"],
        "created_at": ctx.now.isoformat().replace("+00:00", "Z"),
        "task_id": str(started.task_id),
        "workflow_id": started.workflow_id,
        "recency_days": recency_days or DEFAULT_RECENCY_DAYS,
        "device": started.device,
        "mode": mode,
    }
    ctx.context["plan"] = plan

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
                recency_days=recency_days,
                max_sources=max_sources,
                synthesis=synthesis,
                search_provider=search_provider,
                mode=mode,
            )
        except Exception:  # noqa: BLE001 - reported as a failed tool call, never raised here
            logger.exception("voice_research_workflow_start_failed", task_id=str(task_id))
            from app.voice.realtime_sessions.models import RealtimeSessionRow
            from app.voice.realtime_sessions.service import complete_tool_call_system

            with voice_runtime.session() as db2:
                row2 = db2.get(RealtimeSessionRow, session_id)
                if row2 is not None:
                    complete_tool_call_system(
                        db2,
                        row2,
                        call_id=call_id,
                        result=None,
                        error={
                            "error_class": ERROR_RESEARCH_WORKFLOW_START_FAILED,
                            "message": RESEARCH_START_WORKFLOW_FAILED_TR,
                            "speech": RESEARCH_START_WORKFLOW_FAILED_TR,
                        },
                        sideband=voice_runtime.sideband,
                        trace_id=None,
                    )

    ctx.add_followup(_start_workflow_followup)
    return {
        "status": "running",
        "plan_id": str(started.task_id),
        "topic": topic,
        "scope": scope,
        "task_id": str(started.task_id),
        "workflow_id": workflow_id,
        "device": started.device,
        "mode": mode,
    }


#: How long a resolved utterance still speaks for the turn a tool call belongs to. A
#: provider round trip is seconds; ten minutes is generous and bounded, so a follow-up
#: turn from an hour ago can never block a research the owner asks for now.
RESEARCH_TURN_TTL_S = 600.0


def research_followup_refusal(
    db: Session,
    *,
    tool_name: str,
    last_utterance: dict[str, Any] | None,
    plan: dict[str, Any] | None = None,
    last_research: dict[str, Any] | None = None,
    now: datetime | None = None,
    ttl_s: float = RESEARCH_TURN_TTL_S,
) -> dict[str, Any] | None:
    """The refusal payload for a crawl asked for on a research FOLLOW-UP turn, or None.

    docs/DECISIONS.md ADR-0075. This runs in the server's own tool relay
    (``service.handle_tool_call``), BEFORE any handler, so it does not depend on the
    model choosing the right tool - which is exactly what failed in the owner's run.
    It is keyed on the last utterance the ONE router resolved for this session (the
    same record the ``voice_intent_resolved`` audit row is written from), never on
    re-reading the Turkish here: there is no second phrase table.

    Refuses only when all three hold:

    * the tool would start a crawl (:data:`CRAWL_STARTING_TOOLS`);
    * the latest resolved utterance of this session is a
      ``research_technical_explanation`` or ``research_followup`` and is recent enough
      to be this turn (an intervening "yeniden araştır" overwrites the record, so a
      retry is never shadowed by an older follow-up);
    * a COMPLETED research is actually bound - or the context is AMBIGUOUS, which is
      still a follow-up turn and still not a reason to crawl; the refusal then carries
      the clarifying question as its speech.

    A crawl with no completed research anywhere is not refused: refusing then would
    leave the owner with neither a research nor an explanation.
    """
    if tool_name not in CRAWL_STARTING_TOOLS:
        return None
    record = dict(last_utterance or {})
    klass = record.get("research_class")
    if klass not in RESEARCH_CLASSES_BOUND_TO_A_RUN:
        return None
    now = now or datetime.now(UTC)
    at = _parsed_at(record.get("at"))
    if at is not None and (now - at).total_seconds() > ttl_s:
        return None

    from app.explain.research_context import bind_completed_research

    binding = bind_completed_research(db, last_research=last_research, plan=plan, now=now)
    if binding.context is None and not binding.ambiguous:
        return None
    speech = (
        RESEARCH_FOLLOWUP_REFUSED_TR
        if binding.context is not None
        else binding.clarifying_question()
    )
    return {
        "status": "refused",
        "reason": REASON_RESEARCH_FOLLOWUP_TURN,
        "research_class": klass,
        "research_job_id": binding.research_job_id,
        "research_artifact_id": binding.artifact_id,
        "binding_basis": binding.basis,
        "ambiguous": binding.ambiguous,
        "utterance_t_ms": record.get("t_ms"),
        "utterance_turn": record.get("turn"),
        "message": speech,
        "speech": speech,
    }


def _parsed_at(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def plan_redirect(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    instruction = _require_str(arguments, "instruction", max_len=500)
    plan = ctx.context.get("plan")
    if not plan:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            "no open plan to redirect; start one first (research.start)",
        )
    if plan.get("kind") == "research":
        # The M13 pipeline (app.research.browser_workflow.BrowserResearchWorkflow) has
        # no signal or query to change a running run's scope (docs/DECISIONS.md
        # ADR-0067 amendment: checked, not assumed). Claiming a redirect that never
        # reached the workflow would be exactly the false completion this action
        # contract exists to refuse, so the plan is returned UNCHANGED and nothing is
        # pushed over the sideband - nothing changed.
        return {
            "status": "refused",
            "message": PLAN_REDIRECT_REFUSED_TR,
            "speech": PLAN_REDIRECT_REFUSED_TR,
            "plan": plan,
        }
    plan = dict(plan)
    plan["scope"] = instruction
    plan["revision"] = int(plan.get("revision", 1)) + 1
    plan["redirects"] = [*plan.get("redirects", []), instruction][-20:]
    ctx.context["plan"] = plan
    ctx.push(
        SB_PLAN_CHANGED,
        {
            "plan_id": plan["plan_id"],
            "revision": plan["revision"],
            "scope": plan["scope"],
            "status": plan["status"],
        },
    )
    return {"status": "replanned", "plan": plan}


def narration_control(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """Resolve a Turkish narration intent and, when a narration session is
    attached, move its durable cursor / speed through the M4 engine so the
    position stays consistent across devices."""
    utterance = _require_str(arguments, "utterance", max_len=1000)
    narration_id = ctx.context.get("narration_session_id")
    if not narration_id or ctx.db is None:
        resolved = resolve_intent(utterance, session_state=ctx.fsm_state)
        ctx.context["last_intent"] = resolved.intent.value
        return {"intent": resolved.to_dict(), "narration": None}

    # Lazy imports keep the tool registry importable without the artifact stack.
    from app.artifacts import service as artifact_service
    from app.narration import service as narration_service
    from app.narration.engine import build_plan
    from app.narration.routes import _pack_state, _unpack_state

    row = narration_service.get_session(ctx.db, uuid.UUID(str(narration_id)))
    if row is None:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "attached narration session not found")
    version = artifact_service.get_version(ctx.db, row.artifact_id, row.artifact_version)
    if version is None:
        version = artifact_service.get_current_version(ctx.db, row.artifact_id)
    body_md = version.canonical_body if version else ""
    plan = build_plan(
        body_md,
        artifact_id=str(row.artifact_id),
        version=row.artifact_version,
        pronunciation=narration_service.pronunciation_map(ctx.db),
    )
    state = _unpack_state(row)
    resolved = resolve_intent(utterance, session_state=ctx.fsm_state, narration=state)
    bridged = apply_to_narration(resolved, state, plan)
    packed = _pack_state(bridged.state)
    narration_service.update_cursor(
        ctx.db,
        row.id,
        cursor=packed,
        state=bridged.state.state.value,
        speed=bridged.state.speed,
        device_id=ctx.device_id,
    )
    if bridged.presentation:
        ctx.context["presentation"] = bridged.presentation
    ctx.context["last_intent"] = resolved.intent.value
    chunk = plan.chunk_at(bridged.state.cursor) if bridged.state.cursor else None
    cursor_payload = {
        "narration_session_id": str(row.id),
        "cursor": bridged.state.cursor.as_dict() if bridged.state.cursor else None,
        "state": bridged.state.state.value,
        "speed": bridged.state.speed,
        "action": bridged.action,
    }
    ctx.push(SB_NARRATION_CURSOR, cursor_payload)
    # What the provider should say now (M16 spec §3.1): from the new cursor to the end
    # of its section, so "devam" resumes at the exact sentence and "ikinci madde" reads
    # item two onward. Paused means silence; an explanation reads only that item.
    speech = ""
    if bridged.state.state == State.READING:
        # Narration is for listening (owner UX result 2026-09-04): the level's budget bounds
        # what is said now, at a sentence boundary; the cursor keeps the position and
        # "devam et" reads the next chunk. Only "hepsini oku" lifts the budget.
        speech = speech_from(
            plan,
            bridged.state.cursor,
            whole_section=ctx.context.get("presentation") != "full",
            max_chars=speech_budget(ctx.context.get("presentation")),
        )
    elif bridged.state.state == State.EXPLAINING:
        speech = _item_speech(plan, bridged.state.cursor)
    _ledger_note(
        ctx,
        event_type=(
            "voice.narration.paused"
            if bridged.action == "paused"
            else "voice.narration.resumed"
            if resolved.intent == Intent.RESUME
            else None
        ),
        narration_session_id=str(row.id),
        detail={
            "action": bridged.action,
            "intent": resolved.intent.value,
            "cursor": cursor_payload["cursor"],
        },
    )
    return {
        "intent": resolved.to_dict(),
        "narration": {
            **bridged.to_dict(),
            "current_chunk": (
                {"chunk_id": chunk.chunk_id, "kind": chunk.kind, "text": chunk.text}
                if chunk
                else None
            ),
        },
        "speech": speech,
    }


def _item_speech(plan: Any, cursor: Any) -> str:
    """Only the item under ``cursor`` (its paragraph), for explain-then-return."""
    if cursor is None:
        return ""
    para = plan.paragraphs.get(cursor.paragraph_id)
    if para is not None and para.kind == PARAGRAPH_LIST:
        # a list is one paragraph with one chunk per entry: the item is that entry
        return " ".join(
            ch.text.strip()
            for ch in plan.chunks
            if ch.cursor.paragraph_id == cursor.paragraph_id
            and ch.cursor.sentence_index == cursor.sentence_index
            and ch.text.strip()
        )
    return " ".join(
        ch.text.strip()
        for ch in plan.chunks
        if ch.cursor.paragraph_id == cursor.paragraph_id and ch.text.strip()
    )


def _ledger_note(
    ctx: ToolContext, *, event_type: str | None, narration_session_id: str, detail: dict[str, Any]
) -> None:
    """Record a narration transition in the activity ledger, never failing the tool."""
    if event_type is None or ctx.db is None:
        return
    try:
        from app.ledger import service as ledger_service
        from app.ledger.service import ActivityEvent
    except ImportError:
        return
    try:
        ledger_service.record(
            ctx.db,
            ActivityEvent(
                event_type=event_type,
                subsystem="voice",
                status="completed",
                severity="info",
                action=detail.get("action") or event_type,
                occurred_at=ctx.now,
                factual_summary=(
                    "Anlatım duraklatıldı."
                    if event_type.endswith("paused")
                    else "Anlatım kaldığı yerden sürdü."
                ),
                detail_json=detail,
                source="live",
                source_ref=f"voice_narration:{narration_session_id}:{ctx.now.isoformat()}",
                evidence_refs=[
                    {"kind": "narration_session", "ref": narration_session_id},
                    {"kind": "realtime_session", "ref": str(ctx.session_id)},
                ],
            ),
        )
    except Exception:  # noqa: BLE001 - the ledger is evidence, not a dependency
        logger.warning("voice_ledger_note_failed", event_type=event_type)


def activity_explain(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """'Son yaptıklarını anlat': evidence first, then a briefing the provider reads
    verbatim (``speech``), with a narration session attached so dur / devam / ikinci
    madde / teknik anlat move through the same durable cursor (M16 spec §3.1)."""
    question = _require_str(arguments, "question", max_len=500)
    level = arguments.get("level")
    if level is not None and level not in ("executive", "detailed", "technical", "full"):
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            "level must be executive, detailed, technical or full",
        )
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            "activity.explain needs the ledger; no database on this session",
        )
    from app.explain.classify import LIVE_STATE_KINDS, QUERY_EYE_STATE
    from app.explain.research_context import bind_completed_research, has_completed_research
    from app.explain.service import explain_to_briefing
    from app.voice.realtime_sessions.models import RealtimeSessionRow

    # "Teknik anlat" / "detaylandır" / "özetle" / "hepsini oku" with a briefing already
    # attached are moves through that briefing, not a new one: the provider may route them
    # here, so they are honoured the same way narration.control does, and the durable
    # record carries the normalised intent rather than the wording.
    #
    # ADR-0075: the router also decides, from the SAME utterance, whether this is a
    # question about a research that has already finished. That is what binds the answer
    # to a job instead of leaving it to whatever is latest - and what the guard in
    # ``service.handle_tool_call`` reads to refuse a second crawl on this turn.
    resolved = resolve_intent(
        question,
        session_state=ctx.fsm_state,
        has_completed_research=has_completed_research(ctx.db, now=ctx.now),
    )
    briefing_ctx = dict(ctx.context.get("briefing") or {})
    if ctx.context.get("narration_session_id") and resolved.intent in (
        Intent.DETAIL,
        Intent.TECHNICAL,
        Intent.SUMMARIZE,
        Intent.FULL,
    ):
        moved = narration_control(ctx, {"utterance": question})
        return {
            **moved,
            "level": _level_for(ctx.context.get("presentation")),
            "routed": "narration",
            # The briefing being moved through is already bound to a run; say which,
            # so every answer about a research carries the same identity.
            "research_job_id": briefing_ctx.get("research_job_id"),
            "research_artifact_id": briefing_ctx.get("research_artifact_id"),
        }

    binding = None
    if resolved.research_class in RESEARCH_CLASSES_BOUND_TO_A_RUN:
        binding = bind_completed_research(
            ctx.db,
            last_research=ctx.context.get("last_research"),
            plan=ctx.context.get("plan"),
            now=ctx.now,
        )
        if binding.ambiguous:
            # Two plausible completed researches and nothing linking either one to this
            # conversation: ONE short question, no guess, and emphatically no crawl
            # (ADR-0075). Recorded on the tool call like any other answer.
            ctx.context["last_intent"] = resolved.intent.value
            return {
                "status": "needs_clarification",
                "reason": "ambiguous_research_context",
                "speech": binding.clarifying_question(),
                "intent": resolved.to_dict(),
                "level": "executive",
                "narration_session_id": None,
                "routed": "research_context",
                **binding.as_dict(),
            }

    # CURRENT STATE is not the ledger's to answer (docs/M18_ACTION_CONTRACT.md §3): a
    # world_state / eye_state question goes to the same live composer state.now uses, so
    # the spoken answer is identical whichever tool the model picked. No briefing artifact
    # and no narration session are attached for these: persisting an artifact version and
    # a narration row for a three-sentence answer that is stale within seconds is not
    # cheap, and "ikinci madde" has no meaning over it. The routing is recorded in the
    # result so session_activity can say which path served the question.
    live_kind = resolved.query_kind
    if live_kind is None:
        from app.explain.classify import classify

        query = classify(question)
        live_kind = query.kind if query.matched else None
    if live_kind in LIVE_STATE_KINDS:
        live = actions.state_now(
            ctx,
            {"question": question, "scope": "eye" if live_kind == QUERY_EYE_STATE else "all"},
        )
        return {
            **live,
            "intent": resolved.to_dict(),
            "level": "executive",
            "narration_session_id": None,
            "routed": "state.now",
        }

    record = explain_to_briefing(
        ctx.db,
        question,
        level=level,
        now=ctx.now,
        device_id=ctx.device_id,
        research_job_id=binding.research_job_id if binding is not None else None,
    )
    row = ctx.db.get(RealtimeSessionRow, ctx.session_id)
    if row is not None and record.narration_session_id is not None:
        row.narration_session_id = record.narration_session_id
    if record.narration_session_id is not None:
        ctx.context["narration_session_id"] = str(record.narration_session_id)
    ctx.context["presentation"] = {
        "executive": "summary",
        "detailed": "detail",
        "technical": "technical",
        "full": "full",
    }[record.level]
    ctx.context["last_intent"] = "explain"
    ctx.context["briefing"] = {
        "artifact_id": str(record.artifact_id),
        "kind": record.briefing.query.kind,
        "level": record.level,
        # ADR-0075: the completed research this briefing was built from travels with the
        # session, so a later cursor move through the same briefing names the same run.
        "research_job_id": record.briefing.research_job_id,
        "research_artifact_id": record.briefing.research_artifact_id,
    }
    ctx.push(
        SB_NARRATION_CURSOR,
        {
            "narration_session_id": (
                str(record.narration_session_id) if record.narration_session_id else None
            ),
            "cursor": record.cursor.as_dict() if record.cursor else None,
            "state": "READING",
            "speed": 1.0,
            "action": "explain",
        },
    )
    _explained_note(ctx, record)
    out: dict[str, Any] = {**record.as_dict(), "intent": resolved.to_dict()}
    if binding is not None:
        out["research_binding"] = binding.as_dict()
    return out


def _level_for(presentation: Any) -> str:
    return {
        "summary": "executive",
        "detail": "detailed",
        "technical": "technical",
        "full": "full",
    }.get(str(presentation or "summary"), "executive")


def _explained_note(ctx: ToolContext, record: Any) -> None:
    if ctx.db is None:
        return
    try:
        from app.ledger import service as ledger_service
        from app.ledger.service import ActivityEvent
    except ImportError:
        return
    try:
        counts = record.briefing.counts()
        ledger_service.record(
            ctx.db,
            ActivityEvent(
                event_type="voice.explained",
                subsystem="voice",
                status="completed",
                severity="info",
                action="activity.explain",
                occurred_at=ctx.now,
                factual_summary=(
                    f"Sahibe {record.level} düzeyinde etkinlik özeti anlatıldı: "
                    f"{counts['facts']} olgu, {counts['uncertainties']} belirsizlik."
                ),
                detail_json={"kind": record.briefing.query.kind, "level": record.level, **counts},
                source="live",
                source_ref=f"voice_explained:{ctx.session_id}:{record.artifact_id}",
                evidence_refs=[
                    {"kind": "artifact", "ref": str(record.artifact_id)},
                    {"kind": "realtime_session", "ref": str(ctx.session_id)},
                ],
            ),
        )
    except Exception:  # noqa: BLE001
        logger.warning("voice_ledger_note_failed", event_type="voice.explained")


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="clock.now",
            description="Şu anki zamanı (UTC) döndürür.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=clock_now,
        )
    )
    reg.register(
        ToolSpec(
            name="voice.intent",
            description="Sahibin söylediği kısa bir komutu (dur, devam, tekrar oku, ...) çözümler.",
            parameters={
                "type": "object",
                "properties": {"utterance": {"type": "string", "maxLength": 1000}},
                "required": ["utterance"],
                "additionalProperties": False,
            },
            handler=voice_intent,
        )
    )
    reg.register(
        ToolSpec(
            name="narration.control",
            description=(
                "Bağlı belge anlatımını komutla yönetir (kaldığı yer, madde, hız, "
                "özet/detay/teknik); dönen 'speech' metni aynen okunur."
            ),
            parameters={
                "type": "object",
                "properties": {"utterance": {"type": "string", "maxLength": 1000}},
                "required": ["utterance"],
                "additionalProperties": False,
            },
            handler=narration_control,
        )
    )
    reg.register(
        ToolSpec(
            name="activity.explain",
            description=(
                "Sahibin sistemin GEÇMİŞİ, öğrendikleri, hedefleri, kendi kodu ve yetkisiyle "
                "ilgili sorularını KAYITLI KANITTAN yanıtlar: son ne yaptın, bugün neler "
                "yaptın, ne başarısız oldu, sorun var mı, araştırma motoru ne durumda, "
                "kanıtı ne, araştırmayı detaylandır, teknik olarak ne değişti, ne öğrendin, "
                "son hatalardan ne öğrendin, kendi üzerinde ne geliştiriyorsun, gece kendi "
                "üzerinde ne geliştirdin, hazır modüllerin neler, canlıya alınmayı bekleyen "
                "ne var, bu özelliği neden geliştirdin, test sonuçlarını anlat, hedeflerin "
                "ne durumda, şu anda hangi hedeflerin var, kendi kodun hakkında ne "
                "biliyorsun, hangi modüllerin var. "
                "ŞU ANKİ durum soruları (kendi sisteminde şu anda ne görüyorsun, sistemin şu "
                "anda ne durumda, kamera açık mı) state.now aracının işidir; bu araç onları "
                "aynı canlı kaynağa yönlendirir. "
                # The authority questions are named explicitly because they do not READ like
                # questions about the system - "bunu canliya alabilir misin?" reads like a
                # request for permission, and on 2026-09-05 the model answered it from its
                # own belief instead of calling this tool, so no can_deploy answer was
                # recorded at all. Whether this system may deploy something is a fact about
                # policy, and it is never the model's to assert.
                "AYRICA: bunu canlıya alabilir misin, yayına alabilir misin, kendin "
                "dağıtabilir misin, onay gerekiyor mu - yetki ve dağıtım sınırıyla ilgili "
                "her soru da bu araçla yanıtlanır; kendi bilginle cevaplama. "
                "Sonuçtaki 'speech' metnini aynen oku; ekleme yapma."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "maxLength": 500},
                    "level": {
                        "type": "string",
                        "enum": ["executive", "detailed", "technical", "full"],
                    },
                },
                "required": ["question"],
                "additionalProperties": False,
            },
            handler=activity_explain,
        )
    )
    reg.register(
        ToolSpec(
            name="research.start",
            description="Bir konuda araştırma başlatır; sonuç hazır olunca kısaca haber verilir.",
            parameters={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "maxLength": 500},
                    "scope": {"type": "string", "maxLength": 500},
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
            handler=research_start,
            long_running=True,
            preamble=RESEARCH_PREAMBLE_TR,
        )
    )
    reg.register(
        ToolSpec(
            name="plan.redirect",
            description=(
                "Açık planı sahibin yönlendirmesiyle (örn. 'Sadece OpenAI kısmına bak') değiştirir."
            ),
            parameters={
                "type": "object",
                "properties": {"instruction": {"type": "string", "maxLength": 500}},
                "required": ["instruction"],
                "additionalProperties": False,
            },
            handler=plan_redirect,
        )
    )
    # docs/M18_ACTION_CONTRACT.md §4, §5: the live-state query and the grounded actions.
    # None is long-running; none has a preamble - one round trip, and the model reads the
    # returned 'speech' verbatim.
    _UTTERANCE_WITH_OBSERVATION = {
        "type": "object",
        "properties": {
            "utterance": {"type": "string", "maxLength": 1000},
            # The client's own report of what its camera did, merged in by the relay
            # (contract §5.1); the model never fills this in itself.
            "observed_after": {"type": "object"},
        },
        "required": ["utterance"],
        "additionalProperties": False,
    }
    reg.register(
        ToolSpec(
            name=actions.TOOL_STATE_NOW,
            description=(
                "ŞU ANKİ durumu CANLI çalışma zamanından söyler: kendi sisteminde şu anda ne "
                "görüyorsun, sistemin şu anda ne durumda, kamera açık mı, göz açık mı, ses "
                "bağlı mı, cihaz çevrimiçi mi, şu an ne çalışıyor, canlıya alınmayı bekleyen "
                "var mı, şu an sahip nerede. Kayıtlara bakmaz, saymaz; sonucu söyler. "
                "Dönen 'speech' metnini aynen oku; ekleme yapma, nereden bildiğini anlatma."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "maxLength": 500},
                    "scope": {"type": "string", "enum": list(SCOPES)},
                },
                "required": ["question"],
                "additionalProperties": False,
            },
            handler=actions.state_now,
        )
    )
    reg.register(
        ToolSpec(
            name=actions.CAPABILITY_EYE_ENABLE,
            description=(
                "Active Eye'ı (kamerayı) AÇAR: 'gözünü aç', 'kamerayı aç', 'beni izle', "
                "'beni tekrar izle', 'gözünü tekrar aç' denince HER ZAMAN bu araç çağrılır; "
                "sohbetle yanıtlanmaz. Dönen 'speech' metni aynen okunur; araç açtım demeden "
                "açtım denmez."
            ),
            parameters=_UTTERANCE_WITH_OBSERVATION,
            handler=actions.eye_enable,
        )
    )
    reg.register(
        ToolSpec(
            name=actions.CAPABILITY_EYE_DISABLE,
            description=(
                "Active Eye'ı (kamerayı) KAPATIR: 'gözünü kapat', 'kamerayı kapat', 'beni "
                "izleme' denince HER ZAMAN bu araç çağrılır; sohbetle yanıtlanmaz. Dönen "
                "'speech' metni aynen okunur; araç kapattım demeden kapattım denmez."
            ),
            parameters=_UTTERANCE_WITH_OBSERVATION,
            handler=actions.eye_disable,
        )
    )
    reg.register(
        ToolSpec(
            name=actions.CAPABILITY_RELEASE_PROMOTE,
            description=(
                "'Canlıya al', 'yayına al' gibi bir DAĞITIM emrini işler. Sesle canlıya alma "
                "her zaman reddedilir ve reddin kaydı tutulur; dönen 'speech' metni aynen "
                "okunur. Kendi bilginle 'aldım' ya da 'alamam' deme; aracı çağır."
            ),
            parameters={
                "type": "object",
                "properties": {"utterance": {"type": "string", "maxLength": 1000}},
                "required": ["utterance"],
                "additionalProperties": False,
            },
            handler=actions.release_promote,
        )
    )
    # M18.3 spec §3.8: the alarm, display and ambient tools. One line, by design — the
    # manifest stays a manifest and `tools_ambient` stays the receipt discipline.
    register_ambient_tools(reg)
    return reg


__all__ = [
    "CRAWL_STARTING_TOOLS",
    "ERROR_NO_CAPABLE_DEVICE",
    "ERROR_RESEARCH_WORKFLOW_START_FAILED",
    "PLAN_REDIRECT_REFUSED_TR",
    "REASON_RESEARCH_FOLLOWUP_TURN",
    "RESEARCH_FOLLOWUP_REFUSED_TR",
    "RESEARCH_PREAMBLE_TR",
    "RESEARCH_START_DB_MISSING_TR",
    "RESEARCH_START_NO_DEVICE_TR",
    "RESEARCH_START_WORKFLOW_FAILED_TR",
    "RESEARCH_TURN_TTL_S",
    "ToolContext",
    "ToolHandler",
    "ToolRegistry",
    "ToolSpec",
    "activity_explain",
    "default_registry",
    "plan_redirect",
    "research_followup_refusal",
    "research_start",
]
