"""Calendar's voice tools (docs/M21_MAIL_CALENDAR_SPEC.md §3, §4, ADR-0084). Six tools,
registered from ``tools.default_registry()`` by ONE added line
(:func:`register_calendar_tools`), the same discipline every other family establishes.

Freeform day/time/duration text is a MODEL argument (``when_spoken``), parsed HERE against
``ctx.now`` through ``app.calendar.tr_time`` — the exact same split
``app.alarms.sequence``/``alarm.create`` already uses for its own ``when_spoken`` (a
router-extracted IDENTITY field would have nothing to resolve against without the clock a
pure resolver deliberately does not carry). ``calendar.commit`` reaches the real
``CalendarWriter`` ONLY after ``app.actions.confirmation_gate.check_gate`` says yes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

from app.calendar import tr_time
from app.calendar.service import CalendarService
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_CALENDAR_AGENDA: Final = "calendar.agenda"
TOOL_CALENDAR_FIND_SLOT: Final = "calendar.find_slot"
TOOL_CALENDAR_PROPOSE: Final = "calendar.propose"
TOOL_CALENDAR_READ_PROPOSAL: Final = "calendar.read_proposal"
TOOL_CALENDAR_COMMIT: Final = "calendar.commit"
TOOL_CALENDAR_DISCARD: Final = "calendar.discard"

CALENDAR_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_CALENDAR_AGENDA,
    TOOL_CALENDAR_FIND_SLOT,
    TOOL_CALENDAR_PROPOSE,
    TOOL_CALENDAR_READ_PROPOSAL,
    TOOL_CALENDAR_COMMIT,
    TOOL_CALENDAR_DISCARD,
)

#: Europe/Istanbul (CLAUDE.md, spec §2's product default) — the same zone
#: ``app.calendar.ics.DEFAULT_TIMEZONE`` names, kept as a literal here rather than
#: imported so this module never has to import the parser just for a zone name.
_ZONE = ZoneInfo("Europe/Istanbul")


def _service(ctx: ToolContext, tool: str) -> CalendarService:
    service = ctx.live.get("calendar_service")
    if service is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the calendar service"
        )
    return service


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _turn_record(ctx: ToolContext) -> dict[str, Any]:
    return dict(ctx.context.get("last_utterance") or {})


def _local_now(ctx: ToolContext) -> datetime:
    now = ctx.now
    if now.tzinfo is None:
        from datetime import UTC

        now = now.replace(tzinfo=UTC)
    return now.astimezone(_ZONE)


def _day_bounds(ctx: ToolContext, when_spoken: str) -> tuple[datetime, datetime]:
    now = _local_now(ctx)
    _, tokens, _ = _tokenize(when_spoken)
    hint = tr_time.extract_day(tokens)
    day = tr_time.resolve_date(now, hint)
    start = datetime.combine(day, datetime.min.time(), tzinfo=_ZONE)
    return start, start + timedelta(days=1)


def _tokenize(text: str) -> tuple[str, tuple[str, ...], int]:
    from app.voice.intents import normalize_transcript

    return normalize_transcript(text)


# --------------------------------------------------------------------- READ tools


def calendar_agenda(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bugün takvimimde ne var?" (spec §3)."""
    db = _require_db(ctx, TOOL_CALENDAR_AGENDA)
    service = _service(ctx, TOOL_CALENDAR_AGENDA)
    when_spoken = str(arguments.get("when_spoken") or "bugün")
    start, end = _day_bounds(ctx, when_spoken)
    label = "today" if start.date() == _local_now(ctx).date() else start.date().isoformat()
    return service.agenda(
        db, start=start, end=end, range_label=label, session_id=str(ctx.session_id)
    )


def calendar_find_slot(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Cuma 60 dakikalık boşluk bul." / "Yarın öğleden sonra boş muyum?" (spec §3)."""
    db = _require_db(ctx, TOOL_CALENDAR_FIND_SLOT)
    service = _service(ctx, TOOL_CALENDAR_FIND_SLOT)
    when_spoken = str(arguments.get("when_spoken") or "bugün")
    _, tokens, _ = _tokenize(when_spoken)
    day_start, _ = _day_bounds(ctx, when_spoken)
    window = tr_time.extract_daypart_window(tokens)
    start_hour, end_hour = window or (
        tr_time.DEFAULT_WINDOW_START_HOUR,
        tr_time.DEFAULT_WINDOW_END_HOUR,
    )
    start = day_start.replace(hour=start_hour, minute=0)
    end = day_start.replace(hour=end_hour, minute=0)
    duration = arguments.get("duration_minutes")
    duration_minutes = (
        int(duration)
        if isinstance(duration, int | float) and duration > 0
        else (tr_time.extract_duration_minutes(when_spoken) or tr_time.DEFAULT_EVENT_MINUTES)
    )
    return service.find_slot(
        db, start=start, end=end, duration_minutes=duration_minutes, session_id=str(ctx.session_id)
    )


# ------------------------------------------------------------------ PREPARE tools


def calendar_propose(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Perşembe 15'e diş hekimi ekle." (a NEW event) / "Bunu bir saat ertele." (the
    FOCUSED event -> a reschedule; ``calendar_ref`` resolved by the router) (spec §3)."""
    db = _require_db(ctx, TOOL_CALENDAR_PROPOSE)
    service = _service(ctx, TOOL_CALENDAR_PROPOSE)
    turn = _turn_record(ctx)
    when_spoken = str(arguments.get("when_spoken") or "")
    if turn.get("calendar_ref") == "current":
        minutes = tr_time.extract_duration_minutes(when_spoken) or tr_time.DEFAULT_EVENT_MINUTES
        return service.propose_reschedule(db, minutes_delta=minutes, session_id=str(ctx.session_id))
    if not when_spoken:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "calendar.propose needs when_spoken")
    summary = str(arguments.get("summary") or "")
    if not summary:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "calendar.propose needs a summary")
    now = _local_now(ctx)
    _, tokens, _ = _tokenize(when_spoken)
    hint = tr_time.extract_day(tokens)
    day = tr_time.resolve_date(now, hint)
    clock = tr_time.extract_clock(when_spoken)
    hour, minute = clock or (now.hour, 0)
    start = datetime.combine(day, datetime.min.time(), tzinfo=_ZONE).replace(
        hour=hour, minute=minute
    )
    duration = arguments.get("duration_minutes")
    duration_minutes = (
        int(duration)
        if isinstance(duration, int | float) and duration > 0
        else (tr_time.extract_duration_minutes(when_spoken) or tr_time.DEFAULT_EVENT_MINUTES)
    )
    end = start + timedelta(minutes=duration_minutes)
    return service.propose(
        db, summary=summary, start=start, end=end, session_id=str(ctx.session_id)
    )


def calendar_read_proposal(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Öneriyi oku." (spec §3)."""
    del arguments
    db = _require_db(ctx, TOOL_CALENDAR_READ_PROPOSAL)
    service = _service(ctx, TOOL_CALENDAR_READ_PROPOSAL)
    return service.read_proposal(db, session_id=str(ctx.session_id))


# ---------------------------------------------------------- EXTERNAL MUTATION tools


def calendar_commit(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Onayla." / "Tamam, ekle." (spec §3) — reaches the real ``CalendarWriter`` ONLY
    after the confirmation gate says yes."""
    del arguments
    db = _require_db(ctx, TOOL_CALENDAR_COMMIT)
    service = _service(ctx, TOOL_CALENDAR_COMMIT)
    settings = ctx.live.get("settings")
    host_flag = bool(getattr(settings, "calendar_write_enabled", False))
    return service.commit(db, host_flag_enabled=host_flag, session_id=str(ctx.session_id))


def calendar_discard(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """The mail-family's "Vazgeç." resolved to the calendar side (the current prepared
    proposal) by the router's own ``draft_pending``/``proposal_pending`` disambiguation."""
    del arguments
    db = _require_db(ctx, TOOL_CALENDAR_DISCARD)
    service = _service(ctx, TOOL_CALENDAR_DISCARD)
    return service.discard(db, session_id=str(ctx.session_id))


# ------------------------------------------------------------------ registration


def register_calendar_tools(reg: ToolRegistry) -> ToolRegistry:
    """Register all six tools (module docstring: ONE line in ``default_registry``)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_CALENDAR_AGENDA,
            description=(
                "Takvimdeki etkinlikleri listeler: 'Bugün takvimimde ne var?'. "
                "'when_spoken' alanına sahibin söylediği gün ifadesini aynen ver "
                "('bugün', 'yarın', 'Cuma' ...). Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"when_spoken": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=calendar_agenda,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CALENDAR_FIND_SLOT,
            description=(
                "Boş bir zaman aralığı bulur: 'Cuma 60 dakikalık boşluk bul', 'Yarın "
                "öğleden sonra boş muyum?'. 'when_spoken' alanına sahibin söylediği gün/"
                "zaman ifadesini aynen ver; süre söylenmişse 'duration_minutes' alanına "
                "dakika olarak ver. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "when_spoken": {"type": "string", "maxLength": 200},
                    "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 480},
                },
                "additionalProperties": False,
            },
            handler=calendar_find_slot,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CALENDAR_PROPOSE,
            description=(
                "Bir takvim ÖNERİSİ hazırlar (EKLEMEZ): YENİ bir etkinlik için "
                "'Perşembe 15'e diş hekimi ekle' -> 'when_spoken' ve 'summary' ver; "
                "ODAKTAKİ etkinliği ertelemek için 'Bunu bir saat ertele' -> sadece "
                "'when_spoken' (süre ifadesi) ver, 'summary' verme. Çakışmalar "
                "sahibe okunur; ONAY olmadan hiçbir şey değişmez. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "when_spoken": {"type": "string", "maxLength": 200},
                    "summary": {"type": "string", "maxLength": 200},
                    "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 480},
                },
                "additionalProperties": False,
            },
            handler=calendar_propose,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CALENDAR_READ_PROPOSAL,
            description=(
                "ODAKTAKİ ÖNERİYİ aynen okur: 'Öneriyi oku'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=calendar_read_proposal,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CALENDAR_COMMIT,
            description=(
                "ODAKTAKİ ÖNERİYİ takvime İŞLER - GERÇEK bir dış etkidir, SADECE öneri "
                "sahibe okunduktan SONRA sahip 'Onayla.' dediğinde çağrılır. Sahip "
                "öneriyi duymadan bunu ASLA çağırma. Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=calendar_commit,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CALENDAR_DISCARD,
            description=(
                "ODAKTAKİ ÖNERİDEN vazgeçer, takvimi değiştirmez: 'Vazgeç.'. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=calendar_discard,
        )
    )
    return reg


__all__ = [
    "CALENDAR_TOOL_NAMES",
    "TOOL_CALENDAR_AGENDA",
    "TOOL_CALENDAR_COMMIT",
    "TOOL_CALENDAR_DISCARD",
    "TOOL_CALENDAR_FIND_SLOT",
    "TOOL_CALENDAR_PROPOSE",
    "TOOL_CALENDAR_READ_PROPOSAL",
    "calendar_agenda",
    "calendar_commit",
    "calendar_discard",
    "calendar_find_slot",
    "calendar_propose",
    "calendar_read_proposal",
    "register_calendar_tools",
]
