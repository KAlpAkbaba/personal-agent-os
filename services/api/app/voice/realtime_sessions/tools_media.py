"""The owner's voice over media playback (ADR-0112): ``media.play`` / ``media.stop``.

"YouTube'dan 'Doğum günün kutlu olsun Kadir' aç." / "Durdur."

Thin on purpose. Every decision that matters -- which video, whether it is really
playing, what to say -- belongs to ``app.media.playback_service`` and
``app.media.resolve``, the one place the device is reached and the one place the
choice is made. This module turns a turn into a call and a result into a receipt.

**The owner's WORDS beat the model's argument**, the rule every M19+ tool family
follows: the router's record of the utterance is what gets searched, and the
model's ``query`` is the fallback for the turns where the router has nothing.
Otherwise the model would get to paraphrase a song title the owner said exactly.
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
    EVENT_TYPE_MEDIA_FAILED,
    EVENT_TYPE_MEDIA_OPENED,
    EVENT_TYPE_MEDIA_STOPPED,
    EVENT_TYPE_MEDIA_UNVERIFIED,
    SUBSYSTEM_MEDIA,
)
from app.logging import get_logger
from app.media.models import PLAYBACK_STATUS_PLAYING, PLAYBACK_STATUS_UNVERIFIED
from app.media.playback_service import (
    ERROR_PLAYBACK_UNVERIFIED,
    play_request,
    stop_playback,
)
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.realtime_sessions.tools_media")

TOOL_MEDIA_PLAY: Final = "media.play"
TOOL_MEDIA_STOP: Final = "media.stop"

MAX_QUERY_CHARS: Final = 200


def _turn_record(ctx: ToolContext) -> dict[str, Any]:
    return dict(ctx.context.get("last_utterance") or {})


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


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
        record_receipt(ctx.db, receipt, SUBSYSTEM_MEDIA)
    return receipt.as_dict()


def _ledger(ctx: ToolContext, *, event_type: str, summary: str, detail: dict[str, Any]) -> None:
    if ctx.db is None:
        return
    try:
        ledger_service.record(
            ctx.db,
            ledger_service.ActivityEvent(
                event_type=event_type,
                subsystem=SUBSYSTEM_MEDIA,
                action=event_type.split(".", 1)[-1],
                factual_summary=summary[:500],
                occurred_at=datetime.now(UTC),
                evidence_refs=[{"kind": "realtime_session", "ref": str(ctx.session_id)}],
                detail_json=detail,
                source="live",
                # Required, and idempotent on the playback it describes: a retried
                # turn must not add a second row for the same thing happening once.
                source_ref=f"owner_media:{detail.get('playback_id') or ctx.call_id}:{event_type}",
            ),
        )
    except Exception:  # noqa: BLE001 - a ledger note must never break the answer
        logger.warning("media_ledger_note_failed", event_type=event_type)


def _spoken_request(ctx: ToolContext, arguments: dict[str, Any]) -> str:
    """What to search for: the owner's own words first."""
    turn = _turn_record(ctx)
    spoken = turn.get("media_query")
    if isinstance(spoken, str) and spoken.strip():
        return spoken.strip()[:MAX_QUERY_CHARS]
    argued = arguments.get("query")
    if isinstance(argued, str) and argued.strip():
        return argued.strip()[:MAX_QUERY_CHARS]
    return ""


# ------------------------------------------------------------------- media.play


def media_play(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "YouTube'dan 'Doğum günün kutlu olsun' aç.", "Şu şarkıyı aç." (ADR-0112)."""
    db = _require_db(ctx, TOOL_MEDIA_PLAY)
    request_text = _spoken_request(ctx, arguments)
    outcome = play_request(
        db,
        ctx.live.get("device_action"),
        request_text=request_text,
    )

    if outcome.status == PLAYBACK_STATUS_PLAYING:
        execution, terminal, event_type = (
            EXECUTION_EXECUTED,
            TERMINAL_VERIFIED,
            EVENT_TYPE_MEDIA_OPENED,
        )
    elif outcome.status == PLAYBACK_STATUS_UNVERIFIED:
        # The browser opened and the worker could not prove the element moved.
        # UNVERIFIED, never VERIFIED: this is the exact distinction the news
        # spec draws and the one the 2026-09-09 typing defect blurred.
        execution, terminal, event_type = (
            EXECUTION_EXECUTED,
            TERMINAL_UNVERIFIED,
            EVENT_TYPE_MEDIA_UNVERIFIED,
        )
    else:
        execution, terminal, event_type = (
            EXECUTION_REFUSED,
            TERMINAL_FAILED,
            EVENT_TYPE_MEDIA_FAILED,
        )

    _ledger(
        ctx,
        event_type=event_type,
        summary=(
            f"Sahibin istediği medya: {outcome.title or request_text} ({outcome.status})."
            if outcome.title or request_text
            else f"Sahibin istediği medya ({outcome.status})."
        ),
        detail={
            "playback_id": outcome.playback_id,
            "status": outcome.status,
            "video_id": outcome.video_id,
            "error_class": outcome.error_class,
            "request_text": request_text,
        },
    )
    return _receipt(
        ctx,
        capability=TOOL_MEDIA_PLAY,
        requested_state="playing",
        execution=execution,
        terminal=terminal,
        server=outcome.as_dict(),
        speech=outcome.speech,
        error_class=outcome.error_class if outcome.status != PLAYBACK_STATUS_PLAYING else None,
    )


# ------------------------------------------------------------------- media.stop


def media_stop(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Durdur.", "Kapat şunu." — stops the session THIS service opened."""
    db = _require_db(ctx, TOOL_MEDIA_STOP)
    outcome = stop_playback(db, ctx.live.get("device_action"))
    if outcome.ok:
        _ledger(
            ctx,
            event_type=EVENT_TYPE_MEDIA_STOPPED,
            summary=f"Sahibin açtığı medya durduruldu: {outcome.title or '—'}.",
            detail={"playback_id": outcome.playback_id, "video_id": outcome.video_id},
        )
    return _receipt(
        ctx,
        capability=TOOL_MEDIA_STOP,
        requested_state="stopped",
        execution=EXECUTION_EXECUTED if outcome.ok else EXECUTION_REFUSED,
        terminal=TERMINAL_VERIFIED if outcome.ok else TERMINAL_FAILED,
        server=outcome.as_dict(),
        speech=outcome.speech,
        error_class=outcome.error_class,
    )


def register_media_tools(reg: ToolRegistry) -> ToolRegistry:
    """Register both tools (ONE line in ``default_registry``)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_MEDIA_PLAY,
            description=(
                "Sahibin ADIYLA istediği bir videoyu/şarkıyı internetten bulur ve "
                "bilgisayarında AÇAR: 'YouTube'dan şu şarkıyı aç', 'bana ... aç', "
                "'... çal'. Hangi videonun açılacağına SUNUCU karar verir. 'query' "
                "alanına sahibin söylediği ADI aynen yaz - kendin başka bir şeye "
                "çevirme. Haber videoları için bu araç DEĞİL, 'news.open' kullanılır. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "maxLength": MAX_QUERY_CHARS}},
                "additionalProperties": False,
            },
            handler=media_play,
            # NOT long_running: play_request returns only after browser.media_play has
            # reported back, so the answer IS the result. Declaring it long-running
            # made the tool answer "running" and the owner would have been told
            # nothing at all.
            preamble="Bakıyorum efendim.",
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MEDIA_STOP,
            description=(
                "Bu araçla açılan videoyu/şarkıyı durdurur ve tarayıcı oturumunu "
                "kapatır: 'durdur', 'kapat şunu'. Alarmın çaldığı müziği ya da haber "
                "videosunu DURDURMAZ (onlar 'alarm.stop' ve 'news.close'). Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=media_stop,
        )
    )
    return reg


__all__ = [
    "ERROR_PLAYBACK_UNVERIFIED",
    "TOOL_MEDIA_PLAY",
    "TOOL_MEDIA_STOP",
    "media_play",
    "media_stop",
    "register_media_tools",
]
