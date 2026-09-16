"""The owner's voice over self-evolution (docs/M18_4_SELF_EVOLUTION_SPEC.md §4).

Three tools, one discipline:

- ``evolution.control`` - pause / resume / cancel / hold. The ACTION applied is the one the
  ONE router derived from the owner's words and recorded on the turn (``evolution_action``,
  the ADR-0079 §7 / ADR-0080 rule); the model's ``action`` argument is only a fallback for a
  relay with no fresh utterance. Every outcome is an ``ActionReceipt`` - a pause is a ledger
  row read back, a cancel is a lifecycle transition read back, and "there is nothing to
  cancel" is a refusal with its own error class, never a silent success.
- ``release.rollback`` - always refused, and recorded: a rollback is a production action
  (ADR-0055); the refusal names the last-known-good when the host exported one.
- ``evolution.status`` - the four questions ("what are you building", "what did you last
  fix", "which version is running", "any candidate waiting"), answered from the
  supervisor's status (rows alone) in result-first Turkish. A query: no receipt.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Final

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.config import get_settings
from app.evolution import proposals
from app.evolution import supervisor as evolution_supervisor
from app.explain.classify import (
    QUERY_EVOLUTION_NOW,
    QUERY_LAST_FIX,
    QUERY_PENDING_CANDIDATES,
    QUERY_RUNNING_VERSION,
)
from app.ledger.vocabulary import SUBSYSTEM_DEPLOYMENT, SUBSYSTEM_EVOLUTION
from app.logging import get_logger
from app.release.version import release_model
from app.voice.errors import VoiceError, VoiceErrorClass

logger = get_logger("app.voice.realtime_sessions.tools_evolution")

TOOL_EVOLUTION_CONTROL: Final = "evolution.control"
TOOL_EVOLUTION_STATUS: Final = "evolution.status"
TOOL_RELEASE_ROLLBACK: Final = "release.rollback"
TOOL_CAPABILITY_PROPOSE: Final = "capability.propose"
EVOLUTION_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_EVOLUTION_CONTROL,
    TOOL_EVOLUTION_STATUS,
    TOOL_RELEASE_ROLLBACK,
)

ACTION_PAUSE: Final = "pause"
ACTION_RESUME: Final = "resume"
ACTION_CANCEL: Final = "cancel"
ACTION_HOLD: Final = "hold"
EVOLUTION_ACTIONS: Final[tuple[str, ...]] = (
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_CANCEL,
    ACTION_HOLD,
)

ERROR_NO_ACTION: Final = "no_action"
ERROR_NO_CANDIDATE: Final = "no_candidate"
ERROR_AMBIGUOUS_CANDIDATE: Final = "ambiguous_candidate"
ERROR_OWNER_AUTHORIZATION_REQUIRED: Final = "owner_authorization_required"
ERROR_DEPENDENCY_UNAVAILABLE: Final = "dependency_unavailable"

#: One turn's worth of seconds: the record must be fresher than this to decide the action.
TURN_TTL_S: Final = 600.0

# ------------------------------------------------------------------- speech

PAUSED_TR: Final = (
    "Kendi kendini geliştirmeyi duraklattım efendim; gözetmen yeni fırsat açmayacak, "
    "devam eden hiçbir şey canlıya çıkmaz."
)
RESUMED_TR: Final = (
    "Kendi kendini geliştirmeyi yeniden açtım efendim; gözetmen taramaya devam eder."
)
NO_ACTION_TR: Final = (
    "Bu cümleden ne yapmamı istediğinizi çıkaramadım efendim: duraklat, aç, iptal et ya da "
    "canlıya alma diyebilirsiniz."
)
NO_CANDIDATE_CANCEL_TR: Final = "Şu an iptal edilecek bir geliştirme yok efendim."
NO_CANDIDATE_HOLD_TR: Final = "Şu an canlıya alınmayı bekleyen bir aday yok efendim."
NO_SERVICE_TR: Final = "Evrim motoru bu oturumda bağlı değil efendim; bir şey değiştirmedim."
ROLLBACK_REFUSED_TR: Final = (
    "Önceki sürüme dönmek de canlı sistem kararıdır efendim; sürüm geçişini siz başlatırsınız, "
    "ben kendi başıma dönmem."
)


def _action_id(ctx: Any) -> str:
    return str(ctx.call_id or f"evolution-{uuid.uuid4().hex[:12]}")


def _turn_record(ctx: Any) -> dict[str, Any]:
    record = dict(ctx.context.get("last_utterance") or {})
    raw_at = record.get("at")
    if raw_at:
        try:
            at = datetime.fromisoformat(str(raw_at).replace("Z", "+00:00"))
        except ValueError:
            return {}
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        if (ctx.now - at).total_seconds() > TURN_TTL_S:
            return {}
    return record


def _turn_action(ctx: Any) -> str | None:
    action = _turn_record(ctx).get("evolution_action")
    return action if action in EVOLUTION_ACTIONS else None


def _settings(ctx: Any) -> Any:
    return ctx.live.get("settings") or get_settings()


def _receipt(
    ctx: Any,
    *,
    capability: str,
    requested_state: str,
    execution: str,
    terminal: str,
    server: dict[str, Any],
    speech: str,
    error_class: str | None = None,
    evidence: list[dict[str, str]] | None = None,
    subsystem: str = SUBSYSTEM_EVOLUTION,
) -> dict[str, Any]:
    receipt = ActionReceipt(
        action_id=_action_id(ctx),
        capability=capability,
        requested_state=requested_state,
        execution_status=execution,
        terminal_status=terminal,
        observed_after={"server": server, "local": {}},
        evidence_refs=[{"kind": "realtime_session", "ref": str(ctx.session_id)}]
        + list(evidence or []),
        error_class=error_class,
        speech=speech,
        started_at=ctx.now,
        completed_at=datetime.now(UTC),
        session_id=str(ctx.session_id),
        observed_at=ctx.now,
    )
    if ctx.db is not None:
        record_receipt(ctx.db, receipt, subsystem)
    return receipt.as_dict()


def _candidate_line(candidates: list[dict[str, Any]]) -> str:
    titles = [str(c.get("title") or "")[:60] for c in candidates[:3]]
    return "; ".join(t for t in titles if t)


# ----------------------------------------------------------------- control


def evolution_control(ctx: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    action = _turn_action(ctx)
    if action is None:
        raw = arguments.get("action")
        action = raw if isinstance(raw, str) and raw in EVOLUTION_ACTIONS else None
    utterance = str(arguments.get("utterance") or "")[:200]
    if action is None:
        return _receipt(
            ctx,
            capability=TOOL_EVOLUTION_CONTROL,
            requested_state="unknown",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"action": None},
            speech=NO_ACTION_TR,
            error_class=ERROR_NO_ACTION,
        )
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            "evolution.control needs the ledger; no database on this session",
        )

    if action in (ACTION_PAUSE, ACTION_RESUME):
        paused = action == ACTION_PAUSE
        row = evolution_supervisor.set_paused(
            ctx.db, paused=paused, actor="voice", reason=utterance, now=ctx.now
        )
        observed = evolution_supervisor.is_paused(ctx.db)
        return _receipt(
            ctx,
            capability=TOOL_EVOLUTION_CONTROL,
            requested_state="paused" if paused else "resumed",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED if observed is paused else TERMINAL_FAILED,
            server={"action": action, "paused": observed},
            speech=PAUSED_TR if paused else RESUMED_TR,
            evidence=[{"kind": "ledger_event", "ref": str(row.event_id)}],
        )

    service = ctx.live.get("evolution_service")
    if service is None:
        return _receipt(
            ctx,
            capability=TOOL_EVOLUTION_CONTROL,
            requested_state="rejected",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"action": action},
            speech=NO_SERVICE_TR,
            error_class=ERROR_DEPENDENCY_UNAVAILABLE,
        )
    wanted = (
        evolution_supervisor.BUILDING_STATUSES
        if action == ACTION_CANCEL
        else ("shadow_ready", "owner_approval_required")
    )
    candidates = [o for o in service.list_opportunities(limit=200) if o.get("status") in wanted]
    if not candidates:
        return _receipt(
            ctx,
            capability=TOOL_EVOLUTION_CONTROL,
            requested_state="rejected",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"action": action, "candidates": 0},
            speech=NO_CANDIDATE_CANCEL_TR if action == ACTION_CANCEL else NO_CANDIDATE_HOLD_TR,
            error_class=ERROR_NO_CANDIDATE,
        )
    if len(candidates) > 1:
        return _receipt(
            ctx,
            capability=TOOL_EVOLUTION_CONTROL,
            requested_state="rejected",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={
                "action": action,
                "candidates": [c.get("opportunity_id") for c in candidates[:5]],
            },
            speech=(
                f"Birden fazla aday var efendim: {_candidate_line(candidates)}. "
                "Hangisini kastettiğinizi söylerseniz onu ele alırım."
            ),
            error_class=ERROR_AMBIGUOUS_CANDIDATE,
        )
    target = candidates[0]
    reason = "owner_cancelled" if action == ACTION_CANCEL else "owner_held"
    updated = service.advance(
        target["opportunity_id"], target="rejected", actor="owner", reason=reason
    )
    title = str(updated.get("title") or "")[:80]
    speech = (
        f"'{title}' geliştirmesini iptal ettim efendim; aday reddedildi."
        if action == ACTION_CANCEL
        else f"'{title}' canlıya alınmayacak efendim; aday reddedildi, onay merkezinden düştü."
    )
    return _receipt(
        ctx,
        capability=TOOL_EVOLUTION_CONTROL,
        requested_state="rejected",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED if updated.get("status") == "rejected" else TERMINAL_FAILED,
        server={
            "action": action,
            "opportunity_id": updated.get("opportunity_id"),
            "title": title,
            "status": updated.get("status"),
            "reason": reason,
        },
        speech=speech,
        evidence=[{"kind": "evolution_opportunity", "ref": str(updated.get("opportunity_id"))}],
    )


# ---------------------------------------------------------------- rollback


def release_rollback(ctx: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    del arguments
    release = release_model(_settings(ctx), now=ctx.now)
    lkg = release.get("last_known_good")
    tail = (
        f" Bilinen son iyi sürüm: {lkg}."
        if lkg
        else " Bilinen son iyi sürüm bu sunucuya dışa aktarılmamış."
    )
    return _receipt(
        ctx,
        capability=TOOL_RELEASE_ROLLBACK,
        requested_state="rolled_back",
        execution=EXECUTION_REFUSED,
        terminal=TERMINAL_FAILED,
        server={
            "rolled_back": False,
            "authority": "owner_only",
            "by_voice": False,
            "running_version": release.get("version"),
            "last_known_good": lkg,
        },
        speech=ROLLBACK_REFUSED_TR + tail,
        error_class=ERROR_OWNER_AUTHORIZATION_REQUIRED,
        subsystem=SUBSYSTEM_DEPLOYMENT,
    )


# ------------------------------------------------------------------ status


def _minutes(seconds: float | None) -> str:
    if seconds is None:
        return "bilinmeyen süredir"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} dakikadır"
    hours, rem = divmod(minutes, 60)
    return f"{hours} saat {rem} dakikadır"


def _speech_for(kind: str, status: dict[str, Any] | None) -> str:
    if status is None:
        return (
            "Evrim gözetmeni bu oturumda bağlı değil efendim; bu soruya kayıttan cevap veremiyorum."
        )
    if kind == QUERY_EVOLUTION_NOW:
        if status.get("paused"):
            return (
                "Kendi kendini geliştirme duraklatılmış efendim; şu an hiçbir aday üzerinde "
                "çalışmıyorum."
            )
        building = list(status.get("building") or [])
        if building:
            return (
                f"Şu an {len(building)} aday üzerinde çalışıyorum efendim: "
                f"{_candidate_line(building)}."
            )
        counts = dict(status.get("open_by_priority") or {})
        open_total = sum(int(v) for v in counts.values())
        if open_total:
            named = ", ".join(f"{k} {v}" for k, v in counts.items() if v)
            return (
                f"Şu an üzerinde çalıştığım aday yok efendim; {open_total} açık fırsat "
                f"bekliyor ({named})."
            )
        return "Şu an üzerinde çalıştığım bir şey yok efendim; açık fırsat da yok."
    if kind == QUERY_LAST_FIX:
        fix = status.get("last_fix")
        if not fix:
            return "Kayıtlarda düzeltilmiş bir olay yok efendim."
        error_class = fix.get("error_class") or "sağlık"
        release = fix.get("fixed_release_id")
        when = fix.get("last_seen_at") or "bilinmeyen zamanda"
        tail = f", {release} sürümüyle" if release else ""
        return (
            f"Son düzelttiğim olay {fix.get('component')} bileşeninde {error_class} idi efendim"
            f"{tail}; son görülme {when}."
        )
    if kind == QUERY_RUNNING_VERSION:
        running = dict(status.get("running") or {})
        contracts = dict(running.get("contracts") or {})
        version = running.get("version")
        uptime = _minutes(running.get("uptime_s"))
        if version and version != "unknown":
            return (
                f"Çalışan sürüm {version} efendim; eylem sözleşmesi {contracts.get('action')}, "
                f"arayüz sözleşmesi {contracts.get('ui_state')}; {uptime} ayakta."
            )
        return (
            "Çalışan sürümün kimliği bu sunucuya dışa aktarılmamış efendim; uygulama sürümü "
            f"{running.get('app_version')}, eylem sözleşmesi {contracts.get('action')}, "
            f"arayüz sözleşmesi {contracts.get('ui_state')}; {uptime} ayakta."
        )
    if kind == QUERY_PENDING_CANDIDATES:
        pending = list(status.get("pending_candidates") or [])
        if not pending:
            return "Bekleyen aday sürüm yok efendim."
        return (
            f"Bekleyen {len(pending)} aday var efendim: {_candidate_line(pending)}. "
            "Onay merkezinde bekliyorlar; ben kendi başıma canlıya almam."
        )
    return "Bu soruyu evrim kayıtlarından cevaplayamadım efendim."


def evolution_status(ctx: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    kind = arguments.get("kind")
    if not isinstance(kind, str) or kind not in (
        QUERY_EVOLUTION_NOW,
        QUERY_LAST_FIX,
        QUERY_RUNNING_VERSION,
        QUERY_PENDING_CANDIDATES,
    ):
        kind = _turn_record(ctx).get("query_kind") or QUERY_EVOLUTION_NOW
    runtime = ctx.live.get("evolution_runtime")
    service = ctx.live.get("evolution_service")
    status: dict[str, Any] | None = None
    if runtime is not None and service is not None and ctx.db is not None:
        try:
            status = runtime.supervisor.status(
                ctx.db,
                now=ctx.now,
                evolution_service=service,
                release=release_model(_settings(ctx), now=ctx.now),
            )
        except Exception as exc:  # noqa: BLE001 - an unanswerable question is a fact
            logger.error("evolution_status_failed", error=str(exc)[:200])
            status = None
    return {
        "kind": kind,
        "speech": _speech_for(kind, status),
        "status": status,
        "routed": TOOL_EVOLUTION_STATUS,
    }


# ---------------------------------------------------------------- registry


# ------------------------------------------------ what to say instead of "yapamıyorum"


def capability_propose(ctx: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """The owner asked for something no tool here can serve (owner directive 2026-09-09).

    Everything this needs already existed and nothing called it: ``GapDetector`` walks the
    owner's own resolution order and ``GapRecorder`` writes the trail to ``capability_gaps``,
    which the Evolution Supervisor reads on every tick. Until now an unmet request produced a
    sentence and no row, so it existed only until the owner said it again.

    This is the ONE tool in the family with no "the owner's words win" field to prefer, and
    the reason is the point of the tool: the intent resolver has no intent for a request it
    does not recognise, so nothing upstream extracted the owner's sentence. The model's
    relay is the only account of it there is. The wire key is ``request`` rather than
    anything transcript-shaped because ``service.FORBIDDEN_KEY_PARTS`` refuses those at the
    HTTP boundary with a 422 before this handler runs.

    Nothing here decides anything: the resolution comes from the detector, the row from the
    recorder, and the sentence from the resolution.
    """
    argued = arguments.get("request") if isinstance(arguments.get("request"), str) else ""
    request_text = argued.strip()
    if not request_text:
        return {
            "status": "needs_clarification",
            "speech": "Neyi ekleyeyim efendim?",
            "routed": TOOL_CAPABILITY_PROPOSE,
        }

    runtime = ctx.live.get("evolution_runtime")
    if runtime is None:
        return {
            "recorded": False,
            "speech": proposals.SPEECH_NOT_RECORDED,
            "routed": TOOL_CAPABILITY_PROPOSE,
        }
    try:
        proposal = proposals.propose(runtime.detector, runtime.gaps, request_text=request_text)
    except Exception as exc:  # noqa: BLE001 - an unrecorded request is a fact, not a crash
        logger.error("capability_propose_failed", error=str(exc)[:200])
        return {
            "recorded": False,
            "speech": proposals.SPEECH_NOT_RECORDED,
            "routed": TOOL_CAPABILITY_PROPOSE,
        }
    return {"recorded": True, "routed": TOOL_CAPABILITY_PROPOSE, **proposal.as_dict()}


def register_evolution_tools(reg: Any) -> Any:
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_EVOLUTION_CONTROL,
            description=(
                "Kendi kendini geliştirmeyi duraklat / aç, bir geliştirmeyi iptal et, "
                "bir adayı canlıya alma (tut). Sunucu eylemi sahibin sözünden çıkarır."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "utterance": {"type": "string", "description": "Sahibin cümlesi, aynen."},
                    "action": {
                        "type": "string",
                        "enum": list(EVOLUTION_ACTIONS),
                        "description": "Yalnızca yedek: sunucu sahibin sözünü tercih eder.",
                    },
                },
                "required": ["utterance"],
                "additionalProperties": False,
            },
            handler=evolution_control,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_EVOLUTION_STATUS,
            description=(
                "Şu an ne geliştiriyorsun / son hangi hatayı düzelttin / hangi sürüm çalışıyor / "
                "bekleyen aday sürüm var mı: evrim kayıtlarından cevap."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            QUERY_EVOLUTION_NOW,
                            QUERY_LAST_FIX,
                            QUERY_RUNNING_VERSION,
                            QUERY_PENDING_CANDIDATES,
                        ],
                    },
                },
                "additionalProperties": False,
            },
            handler=evolution_status,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_RELEASE_ROLLBACK,
            description=(
                "Önceki sürüme dön: her zaman reddedilir ve kaydedilir; sürüm geçişi sahibin "
                "canlı sistem kararıdır."
            ),
            parameters={
                "type": "object",
                "properties": {"utterance": {"type": "string"}},
                "required": ["utterance"],
                "additionalProperties": False,
            },
            handler=release_rollback,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CAPABILITY_PROPOSE,
            description=(
                "Sahibin istediği bir şeyi ELİNDEKİ ARAÇLARLA YAPAMIYORSAN bu aracı "
                "çağırırsın. 'Bunu yapamıyorum', 'bu bende yok', 'buna yetkim yok' gibi "
                "bir cümleyi ASLA bu aracı çağırmadan kurmazsın. 'request' alanına sahibin "
                "ne istediğini kendi cümlesiyle yazarsın. Sunucu isteği geliştirme "
                "listesine kaydeder ve ne olacağını söyleyen cümleyi döner; o 'speech' "
                "metnini aynen okursun. Yapabileceğin bir şey için bu aracı çağırmazsın - "
                "önce doğru aracı denersin."
            ),
            parameters={
                "type": "object",
                "properties": {"request": {"type": "string", "maxLength": 2000}},
                "additionalProperties": False,
            },
            handler=capability_propose,
        )
    )
    return reg


__all__ = [
    "ACTION_CANCEL",
    "TOOL_CAPABILITY_PROPOSE",
    "capability_propose",
    "ACTION_HOLD",
    "ACTION_PAUSE",
    "ACTION_RESUME",
    "ERROR_AMBIGUOUS_CANDIDATE",
    "ERROR_NO_ACTION",
    "ERROR_NO_CANDIDATE",
    "EVOLUTION_ACTIONS",
    "EVOLUTION_TOOL_NAMES",
    "TOOL_EVOLUTION_CONTROL",
    "TOOL_EVOLUTION_STATUS",
    "TOOL_RELEASE_ROLLBACK",
    "evolution_control",
    "evolution_status",
    "register_evolution_tools",
    "release_rollback",
]
