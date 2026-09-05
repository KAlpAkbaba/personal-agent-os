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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.narration.commands import State
from app.narration.engine import PARAGRAPH_LIST
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.intents import (
    Intent,
    apply_to_narration,
    resolve_intent,
    speech_budget,
    speech_from,
)
from app.voice.providers import cloud_tool_name
from app.voice.realtime import RealtimeState
from app.voice.realtime_sessions.sideband import SB_NARRATION_CURSOR, SB_PLAN_CHANGED

logger = get_logger("app.voice.realtime_sessions.tools")

RESEARCH_PREAMBLE_TR = (
    "Bakıyorum. OpenAI, Anthropic, Google ve önemli açık kaynak gelişmelerini karşılaştıracağım."
)


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

    def push(self, event: str, payload: dict[str, Any]) -> None:
        self.pushes.append((event, dict(payload)))

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
    topic = _require_str(arguments, "topic", max_len=500)
    scope = str(arguments.get("scope") or "genel")[:500]
    plan_id = str(uuid.uuid4())
    plan = {
        "plan_id": plan_id,
        "kind": "research",
        "topic": topic,
        "scope": scope,
        "status": "running",
        "revision": 1,
        "redirects": [],
        "steps": ["kaynakları topla", "karşılaştır", "yönetici özeti çıkar"],
        "created_at": ctx.now.isoformat().replace("+00:00", "Z"),
    }
    ctx.context["plan"] = plan
    return {"status": "running", "plan_id": plan_id, "topic": topic, "scope": scope}


def plan_redirect(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    instruction = _require_str(arguments, "instruction", max_len=500)
    plan = ctx.context.get("plan")
    if not plan:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            "no open plan to redirect; start one first (research.start)",
        )
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
    from app.explain.service import explain_to_briefing
    from app.voice.realtime_sessions.models import RealtimeSessionRow

    # "Teknik anlat" / "detaylandır" / "özetle" / "hepsini oku" with a briefing already
    # attached are moves through that briefing, not a new one: the provider may route them
    # here, so they are honoured the same way narration.control does, and the durable
    # record carries the normalised intent rather than the wording.
    resolved = resolve_intent(question, session_state=ctx.fsm_state)
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
        }

    record = explain_to_briefing(
        ctx.db, question, level=level, now=ctx.now, device_id=ctx.device_id
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
    return {**record.as_dict(), "intent": resolved.to_dict()}


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
                "Sahibin sistemin kendisiyle ilgili HER sorusunu KAYITLI KANITTAN "
                "yanıtlar: son ne yaptın, bugün neler yaptın, ne başarısız oldu, sorun var "
                "mı, araştırma motoru ne durumda, kanıtı ne, araştırmayı detaylandır, "
                "teknik olarak ne değişti, ne öğrendin, son hatalardan ne öğrendin, kendi "
                "üzerinde ne geliştiriyorsun, gece kendi üzerinde ne geliştirdin, hazır "
                "modüllerin neler, canlıya alınmayı bekleyen ne var, bu özelliği neden "
                "geliştirdin, test sonuçlarını anlat, hedeflerin ne durumda, şu anda hangi "
                "hedeflerin var, kendi sisteminde şu anda ne görüyorsun, kendi kodun "
                "hakkında ne biliyorsun, hangi modüllerin var. "
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
    return reg


__all__ = [
    "RESEARCH_PREAMBLE_TR",
    "ToolContext",
    "ToolHandler",
    "ToolRegistry",
    "ToolSpec",
    "activity_explain",
    "default_registry",
]
