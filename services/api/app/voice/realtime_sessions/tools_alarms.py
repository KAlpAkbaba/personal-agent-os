"""The wake alarm's song by voice (the owner, 2026-10-05: "alarmda istediğim müzikle beni
uyandıracak ya da istediğim müziği açacak").

Two things a voice turn needs that ``tools_ambient`` does not have:

* :func:`song_for_create` - "Yarın 7'de beni Tarkan'ın Şımarık'ıyla uyandır.": the song the
  SENTENCE named (the router's ``media_query``, the owner's words; the model's
  ``media.title`` only when the router has nothing), found the way media.play finds one -
  the device's own search, the first real watch URL (``app.media.resolve``) - and handed
  to ``alarm.create`` as ``{"url", "title"}``;
* :func:`alarm_set_song` (``alarm.set_song``) - "Alarmımın şarkısını Şımarık yap." /
  "Uyandırma şarkımı Sezen Aksu Gülümse yap.": the next alarm's own song, or the owner's
  global wake song, with the same search.

The search runs in a short-lived session of the ALARM browser profile and closes it: a set
alarm leaves no tab behind, and the owner's own Chrome is never driven to set an alarm.
Nothing is played now - the song plays when the alarm rings (``app.alarms.sequence``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.alarms import service as alarms_service
from app.alarms.sequence import ALARM_BROWSER_PROFILE
from app.alarms.speech import at_phrase, cardinal
from app.alarms.tr_time import DEFAULT_TIMEZONE
from app.ledger.vocabulary import SUBSYSTEM_ROUTINE
from app.logging import get_logger
from app.media.resolve import pick_video, search_query
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.intents import (
    SONG_SCOPE_ALARM,
    SONG_SCOPE_WAKE_SONG,
    alarm_song_set_match,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.realtime_sessions.tools_alarms")

TOOL_ALARM_SET_SONG: Final = "alarm.set_song"

ERROR_NO_SONG_NAMED: Final = "no_song_named"
ERROR_NO_ALARM: Final = "no_alarm"
ERROR_NO_DEVICE: Final = "no_capable_device"
ERROR_SEARCH_FAILED: Final = "search_failed"
ERROR_NO_VIDEO_FOUND: Final = "no_video_found"

#: The intents whose turn carries the song an alarm sentence named.
_CREATE_INTENTS: Final[frozenset[str]] = frozenset({"alarm_create", "alarm_test_create"})
#: The router's intent -> the song's scope (the turn record carries no sentence).
_SCOPE_BY_INTENT: Final[dict[str, str]] = {
    "alarm_song_set": SONG_SCOPE_ALARM,
    "wake_song_set": SONG_SCOPE_WAKE_SONG,
}

SEARCH_MAX_RESULTS: Final = 10
SEARCH_LOCALE: Final = "tr-TR"
TIMEOUT_SESSION_S: Final = 30.0
TIMEOUT_SEARCH_S: Final = 45.0
TIMEOUT_CLOSE_S: Final = 15.0
MAX_QUERY_CHARS: Final = 200

SONG_WHICH_TR: Final = "Hangi şarkıyla uyandırayım efendim?"
SONG_NO_ALARM_TR: Final = "Kurulu bir alarm yok efendim; önce alarmı kuralım."
SONG_NOT_FOUND_TR: Final = "O şarkıyı bulamadım efendim; başka bir isimle deneyelim mi?"
SONG_NO_DEVICE_TR: Final = "Şarkıyı aramak için bilgisayarınıza ulaşamıyorum efendim."
SONG_SET_ALARM_TR: Final = "Tamam efendim, {when} {title} ile uyandıracağım."
SONG_SET_WAKE_TR: Final = "Tamam efendim, bundan sonra sizi {title} ile uyandıracağım."

_DAY_NAMES_TR: Final[tuple[str, ...]] = (
    "pazartesi",
    "salı",
    "çarşamba",
    "perşembe",
    "cuma",
    "cumartesi",
    "pazar",
)


@dataclass(frozen=True, slots=True)
class FoundSong:
    ok: bool
    song: dict[str, str] | None = None
    error_class: str | None = None


def find_song(device_action: Any, spoken: str) -> FoundSong:
    """Search for what the owner named and keep the first real video - never play it.

    Same choice as media.play (``app.media.resolve``: the engine's order, a watch URL or
    nothing), in a session of the alarm's own profile that is closed again either way.
    """
    words = " ".join(str(spoken or "").split())[:MAX_QUERY_CHARS]
    if not words:
        return FoundSong(False, error_class=ERROR_NO_SONG_NAMED)
    if device_action is None:
        return FoundSong(False, error_class=ERROR_NO_DEVICE)
    session_id = f"alarm-song-{uuid.uuid4()}"
    prefix = f"alarm-song:{session_id}"
    opened = device_action.run(
        capability="browser.session_open",
        payload={
            "session_id": session_id,
            "profile": ALARM_BROWSER_PROFILE,
            "session_kind": "media",
            "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": False},
            "channel": "chrome",
        },
        idempotency_key=f"{prefix}:open",
        timeout_s=TIMEOUT_SESSION_S,
    )
    if not opened.ok:
        return FoundSong(False, error_class=ERROR_NO_DEVICE)
    try:
        searched = device_action.run(
            capability="browser.search",
            payload={
                "session_id": session_id,
                "query": search_query(words),
                "engine": "auto",
                "max_results": SEARCH_MAX_RESULTS,
                "locale": SEARCH_LOCALE,
            },
            idempotency_key=f"{prefix}:search",
            timeout_s=TIMEOUT_SEARCH_S,
        )
        if not searched.ok:
            return FoundSong(False, error_class=ERROR_SEARCH_FAILED)
        candidate = pick_video((searched.result or {}).get("results"))
        if candidate is None:
            return FoundSong(False, error_class=ERROR_NO_VIDEO_FOUND)
        return FoundSong(True, song={"url": candidate.url, "title": candidate.title or words})
    finally:
        try:
            device_action.run(
                capability="browser.session_close",
                payload={"session_id": session_id},
                idempotency_key=f"{prefix}:close",
                timeout_s=TIMEOUT_CLOSE_S,
            )
        except Exception:  # noqa: BLE001 - a stray tab is not a lost answer
            logger.warning("alarm_song_session_close_failed", session_id=session_id)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _turn(ctx: ToolContext) -> dict[str, Any]:
    return dict(ctx.context.get("last_utterance") or {})


def song_for_create(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, str] | None:
    """The song ``alarm.create`` should carry, found now - or None (nothing named, an
    explicit url given, or the search found nothing: the alarm is still set, and rings the
    global wake song or the tone)."""
    media = arguments.get("media") if isinstance(arguments.get("media"), dict) else {}
    if isinstance(media.get("url"), str) and media["url"].strip():
        return None
    turn = _turn(ctx)
    spoken = turn.get("media_query") if turn.get("intent") in _CREATE_INTENTS else None
    if not (isinstance(spoken, str) and spoken.strip()):
        spoken = media.get("title") if isinstance(media.get("title"), str) else None
    if not spoken or not spoken.strip():
        return None
    found = find_song(ctx.live.get("device_action"), spoken)
    if not found.ok:
        logger.info("alarm_song_not_found", error_class=found.error_class)
        return None
    return found.song


def song_scope(utterance: str) -> str | None:
    """ "alarm" (the next alarm's own song) or "wake_song" (the global one), or None."""
    matched = alarm_song_set_match(utterance)
    return matched[0] if matched else None


def _when_words(
    *,
    local_time: str,
    tomorrow: bool,
    weekdays: tuple[int, ...] | list[int],
    relative_seconds: int | None = None,
) -> str:
    if relative_seconds is not None:
        return f"{cardinal(relative_seconds)} saniye sonra"
    clock = at_phrase(local_time)
    days = tuple(sorted(set(weekdays)))
    if days == (0, 1, 2, 3, 4):
        return f"her hafta içi {clock}"
    if days == tuple(range(7)):
        return f"her gün {clock}"
    if days:
        return "her " + ", ".join(_DAY_NAMES_TR[d] for d in days) + f" {clock}"
    return f"yarın {clock}" if tomorrow else clock


def song_created_speech(
    *,
    local_time: str,
    tomorrow: bool,
    weekdays: tuple[int, ...] | list[int],
    title: str,
    relative_seconds: int | None = None,
) -> str:
    """ONE short sentence: "Yarın yedide Şımarık ile uyandıracağım efendim." """
    when = _when_words(
        local_time=local_time,
        tomorrow=tomorrow,
        weekdays=weekdays,
        relative_seconds=relative_seconds,
    )
    clean = " ".join(title.replace(".", " ").split()) or "seçtiğiniz şarkı"
    return f"{when[:1].upper()}{when[1:]} {clean} ile uyandıracağım efendim."


def _receipt(
    ctx: ToolContext,
    *,
    execution: str,
    terminal: str,
    server: dict[str, Any],
    speech: str,
    error_class: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    receipt = ActionReceipt(
        action_id=ctx.call_id or str(uuid.uuid4()),
        capability=TOOL_ALARM_SET_SONG,
        requested_state="song_set",
        execution_status=execution,
        terminal_status=terminal,
        observed_after={"server": server, "local": {}},
        evidence_refs=[{"kind": "realtime_session", "ref": str(ctx.session_id)}],
        error_class=error_class,
        speech=speech,
        started_at=ctx.now,
        completed_at=now,
        session_id=str(ctx.session_id),
        observed_at=now,
    )
    if ctx.db is not None:
        record_receipt(ctx.db, receipt, SUBSYSTEM_ROUTINE)
    return receipt.as_dict()


def _refused(ctx: ToolContext, error_class: str, speech: str, **server: Any) -> dict[str, Any]:
    return _receipt(
        ctx,
        execution=EXECUTION_REFUSED,
        terminal=TERMINAL_FAILED,
        server={"error": error_class, **server},
        speech=speech,
        error_class=error_class,
    )


def alarm_set_song(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Alarmımın şarkısını Şımarık yap." / "Uyandırma şarkımı Sezen Aksu Gülümse yap."

    The title is the owner's words (the router's ``media_query``; the model's ``title`` only
    when the router has none). No title -> ask which song and change nothing. The scope -
    the next alarm, or the global wake song - is the router's intent (``alarm_song_set`` /
    ``wake_song_set``); the model's ``scope`` only when the router did not decide.
    """
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{TOOL_ALARM_SET_SONG} needs the durable state; no database on this session",
        )
    db = ctx.db
    turn = _turn(ctx)
    spoken = turn.get("media_query")
    if not (isinstance(spoken, str) and spoken.strip()):
        argued = arguments.get("title")
        spoken = argued if isinstance(argued, str) else None
    if not spoken or not spoken.strip():
        return _refused(ctx, ERROR_NO_SONG_NAMED, SONG_WHICH_TR)
    scope = _SCOPE_BY_INTENT.get(str(turn.get("intent") or ""))
    if scope is None:
        argued_scope = arguments.get("scope")
        scope = argued_scope if argued_scope in (SONG_SCOPE_ALARM, SONG_SCOPE_WAKE_SONG) else None
    scope = scope or SONG_SCOPE_ALARM

    alarm = alarms_service.next_alarm(db, now=ctx.now) if scope == SONG_SCOPE_ALARM else None
    if scope == SONG_SCOPE_ALARM and alarm is None:
        return _refused(ctx, ERROR_NO_ALARM, SONG_NO_ALARM_TR)

    found = find_song(ctx.live.get("device_action"), spoken)
    if not found.ok or found.song is None:
        speech = SONG_NO_DEVICE_TR if found.error_class == ERROR_NO_DEVICE else SONG_NOT_FOUND_TR
        return _refused(ctx, found.error_class or ERROR_NO_VIDEO_FOUND, speech, query=spoken)
    song = found.song

    if alarm is not None:
        alarms_service.set_alarm_song(db, alarm.id, url=song["url"], title=song["title"])
        weekdays = tuple((alarm.recurrence or {}).get("weekdays") or ())
        zone = ZoneInfo(alarm.timezone or DEFAULT_TIMEZONE)
        ring_day = _aware(alarm.scheduled_for).astimezone(zone).date()
        tomorrow = not weekdays and (ring_day - _aware(ctx.now).astimezone(zone).date()).days == 1
        speech = SONG_SET_ALARM_TR.format(
            when=_when_words(local_time=alarm.local_time, tomorrow=tomorrow, weekdays=weekdays),
            title=song["title"],
        )
        server = {"scope": scope, "song": song, "alarm": alarms_service.alarm_dict(alarm)}
    else:
        alarms_service.set_wake_song(db, url=song["url"], title=song["title"])
        speech = SONG_SET_WAKE_TR.format(title=song["title"])
        server = {"scope": scope, "song": song, "wake_song": alarms_service.get_wake_song(db)}
    return _receipt(
        ctx,
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        server=server,
        speech=speech,
    )


def register_alarm_song_tools(reg: ToolRegistry) -> ToolRegistry:
    """``alarm.set_song``. Called from ``tools_ambient.register_ambient_tools`` (the alarm
    family's one registration line) once that wiring lands."""
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_ALARM_SET_SONG,
            description=(
                "Alarmın ŞARKISINI değiştirir: 'alarmımın şarkısını X yap' (sıradaki alarm), "
                "'uyandırma şarkımı X yap' (genel uyandırma şarkısı). Şarkı adını 'title' "
                "alanına sahibin söylediği gibi ver. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": MAX_QUERY_CHARS},
                    "scope": {"type": "string", "enum": [SONG_SCOPE_ALARM, SONG_SCOPE_WAKE_SONG]},
                },
                "additionalProperties": False,
            },
            handler=alarm_set_song,
        )
    )
    return reg


__all__ = [
    "ERROR_NO_SONG_NAMED",
    "TOOL_ALARM_SET_SONG",
    "FoundSong",
    "alarm_set_song",
    "find_song",
    "register_alarm_song_tools",
    "song_created_speech",
    "song_for_create",
    "song_scope",
]
