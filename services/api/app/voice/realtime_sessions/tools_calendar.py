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

from app.actions.confirmation_gate import (
    CONFIRM_SOURCE_OWNER_POLICY,
    CONFIRM_SOURCE_VOICE,
    GATE_ACCOUNT_MISSING,
    GATE_SEND_DISABLED,
    Confirmation,
)
from app.actions.receipt import EXECUTION_EXECUTED
from app.calendar import tr_time
from app.calendar.service import CalendarService
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.intents import Intent

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_CALENDAR_AGENDA: Final = "calendar.agenda"
TOOL_CALENDAR_FIND_SLOT: Final = "calendar.find_slot"
TOOL_CALENDAR_PROPOSE: Final = "calendar.propose"
TOOL_CALENDAR_READ_PROPOSAL: Final = "calendar.read_proposal"
TOOL_CALENDAR_COMMIT: Final = "calendar.commit"
TOOL_CALENDAR_DISCARD: Final = "calendar.discard"
#: B27 req 731: "Toplantıyı iptal et." reaches the calendar and is answered with a receipt.
TOOL_CALENDAR_CANCEL: Final = "calendar.cancel"

CALENDAR_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_CALENDAR_AGENDA,
    TOOL_CALENDAR_FIND_SLOT,
    TOOL_CALENDAR_PROPOSE,
    TOOL_CALENDAR_READ_PROPOSAL,
    TOOL_CALENDAR_COMMIT,
    TOOL_CALENDAR_DISCARD,
    TOOL_CALENDAR_CANCEL,
)

#: B27 req 730. "Bu hafta ne var?" / "Haftalık programımı söyle." - a WEEK, which the
#: day parser has no word for. "haftaya" / "gelecek hafta" is the next Monday-to-Sunday;
#: "bu hafta" is from today through this Sunday (what is still ahead, never what passed).
_WEEK_STEMS: Final[tuple[str, ...]] = ("hafta",)
_NEXT_WEEK_FORMS: Final[tuple[str, ...]] = ("haftaya", "gelecek", "önümüzdeki", "onumuzdeki")
RANGE_THIS_WEEK: Final = "this_week"
RANGE_NEXT_WEEK: Final = "next_week"

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


def _range_bounds(ctx: ToolContext, when_spoken: str) -> tuple[datetime, datetime, str]:
    """(start, end, label): a week when the owner said one, else the day ``_day_bounds``
    resolves - the same tokens, read once."""
    now = _local_now(ctx)
    _, tokens, _ = _tokenize(when_spoken)
    if any(token.startswith(_WEEK_STEMS) for token in tokens):
        today = datetime.combine(now.date(), datetime.min.time(), tzinfo=_ZONE)
        next_monday = today + timedelta(days=7 - today.weekday())
        if any(token.startswith(_NEXT_WEEK_FORMS) for token in tokens):
            return next_monday, next_monday + timedelta(days=7), RANGE_NEXT_WEEK
        return today, next_monday, RANGE_THIS_WEEK
    start, end = _day_bounds(ctx, when_spoken)
    label = "today" if start.date() == now.date() else start.date().isoformat()
    return start, end, label


def calendar_agenda(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bugün takvimimde ne var?" (spec §3); B27 req 730: "Bu hafta ne var?"."""
    db = _require_db(ctx, TOOL_CALENDAR_AGENDA)
    service = _service(ctx, TOOL_CALENDAR_AGENDA)
    when_spoken = str(arguments.get("when_spoken") or "bugün")
    start, end, label = _range_bounds(ctx, when_spoken)
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
        proposed = service.propose_reschedule(
            db, minutes_delta=minutes, session_id=str(ctx.session_id)
        )
        return _commit_on_first_word(ctx, service, proposed)
    if not when_spoken:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "calendar.propose needs when_spoken")
    summary = str(arguments.get("summary") or "")
    if not summary:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "calendar.propose needs a summary")
    now = _local_now(ctx)
    _, tokens, _ = _tokenize(when_spoken)
    hint = tr_time.extract_day(tokens)
    day = tr_time.resolve_date(now, hint)
    # B46: "15 dakika önce hatırlat" is neither the event's clock nor its duration.
    timing = tr_time.without_reminder(when_spoken)
    clock = tr_time.extract_clock(timing)
    hour, minute = clock or (now.hour, 0)
    start = datetime.combine(day, datetime.min.time(), tzinfo=_ZONE).replace(
        hour=hour, minute=minute
    )
    duration = arguments.get("duration_minutes")
    duration_minutes = (
        int(duration)
        if isinstance(duration, int | float) and duration > 0
        else (tr_time.extract_duration_minutes(timing) or tr_time.DEFAULT_EVENT_MINUTES)
    )
    end = start + timedelta(minutes=duration_minutes)
    # B46 (req 356, 357): the router's reading of the owner's own sentence wins; the model's
    # when_spoken is read only when the router found nothing, and its reminder_minutes
    # argument only when neither did.
    rrule = turn.get("calendar_rrule") or tr_time.extract_recurrence(when_spoken)
    reminder = turn.get("calendar_reminder_minutes")
    if not isinstance(reminder, int):
        reminder = tr_time.extract_reminder_minutes(when_spoken)
    model_reminder = arguments.get("reminder_minutes")
    if reminder is None and isinstance(model_reminder, int) and 0 <= model_reminder <= 10080:
        reminder = model_reminder
    proposed = service.propose(
        db,
        summary=summary,
        start=start,
        end=end,
        rrule=rrule if isinstance(rrule, str) else None,
        reminder_minutes=reminder,
        session_id=str(ctx.session_id),
    )
    return _commit_on_first_word(ctx, service, proposed)


def _commit_on_first_word(
    ctx: ToolContext, service: Any, proposed: dict[str, Any]
) -> dict[str, Any]:
    """Owner decision 2026-09-19 ("Tüm 2. ses onaylarını kaldır, mail hariç"): the proposal
    the owner just made is committed in the same turn, on the owner's own sentence, with no
    "Onayla" in between.

    The gate is not bypassed - it is walked, in order: the proposal's speech is its read-back
    (H1's explicit act, recorded on THIS session and turn by ``read_proposal``), then the
    commit under the owner's standing decision. The account and the host flag are still
    required; when the gate refuses for a reason the owner cannot answer right now (no
    account, writing disabled - B46 is deferred) the proposal STANDS and is spoken as before,
    so "Onayla" still works the day the account is there and nothing is lost."""
    if ctx.db is None or not isinstance(proposed.get("proposal"), dict):
        return proposed
    turn = _turn_record(ctx)
    turn_no = turn.get("turn") if isinstance(turn.get("turn"), int) else None
    read = service.read_proposal(ctx.db, session_id=str(ctx.session_id), turn=turn_no)
    if read.get("status") == "needs_clarification":
        return proposed
    settings = ctx.live.get("settings")
    host_flag = bool(getattr(settings, "calendar_write_enabled", False))
    confirmation = Confirmation(
        source=CONFIRM_SOURCE_OWNER_POLICY,
        session_id=str(ctx.session_id),
        turn=turn_no,
        owner_intent_ok=turn.get("intent") == Intent.CALENDAR_PROPOSE,
    )
    committed = service.commit(
        ctx.db,
        host_flag_enabled=host_flag,
        session_id=str(ctx.session_id),
        confirmation=confirmation,
    )
    if committed.get("execution_status") == EXECUTION_EXECUTED:
        return committed
    if committed.get("error_class") in (GATE_ACCOUNT_MISSING, GATE_SEND_DISABLED):
        return proposed
    return committed


def calendar_read_proposal(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Öneriyi oku." (spec §3) — the EXPLICIT read-back act (H1); see
    ``app.voice.realtime_sessions.tools_mail.mail_read_draft``'s docstring."""
    del arguments
    db = _require_db(ctx, TOOL_CALENDAR_READ_PROPOSAL)
    service = _service(ctx, TOOL_CALENDAR_READ_PROPOSAL)
    turn = _turn_record(ctx).get("turn")
    return service.read_proposal(
        db,
        session_id=str(ctx.session_id),
        turn=turn if isinstance(turn, int) else None,
    )


# ---------------------------------------------------------- EXTERNAL MUTATION tools


def calendar_commit(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Onayla." / "Tamam, ekle." (spec §3) — reaches the real ``CalendarWriter`` ONLY
    after the confirmation gate says yes. H1 (ADR-0084 addendum 2): see
    ``app.voice.realtime_sessions.tools_mail.mail_send``'s docstring — the same
    router-resolved, never handler-assumed, owner-intent check."""
    del arguments
    db = _require_db(ctx, TOOL_CALENDAR_COMMIT)
    service = _service(ctx, TOOL_CALENDAR_COMMIT)
    settings = ctx.live.get("settings")
    host_flag = bool(getattr(settings, "calendar_write_enabled", False))
    turn = _turn_record(ctx)
    confirmation = Confirmation(
        source=CONFIRM_SOURCE_VOICE,
        session_id=str(ctx.session_id),
        turn=turn.get("turn") if isinstance(turn.get("turn"), int) else None,
        owner_intent_ok=turn.get("intent") == Intent.CALENDAR_COMMIT,
    )
    return service.commit(
        db,
        host_flag_enabled=host_flag,
        session_id=str(ctx.session_id),
        confirmation=confirmation,
    )


def calendar_cancel(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Toplantıyı iptal et." / "Perşembeki toplantıyı iptal et." (B27 req 731) — the
    focused event, answered by the calendar service with a receipt: today an honest
    refusal (spec §1: no delete), tomorrow whatever B46's policy allows, from the SAME
    method."""
    db = _require_db(ctx, TOOL_CALENDAR_CANCEL)
    service = _service(ctx, TOOL_CALENDAR_CANCEL)
    uid = arguments.get("event_uid")
    return service.cancel_event(
        db,
        event_uid=str(uid) if isinstance(uid, str) and uid.strip() else None,
        session_id=str(ctx.session_id),
    )


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
                "sahibe okunur; ONAY olmadan hiçbir şey değişmez. Tekrar ('her hafta "
                "pazartesi') ve hatırlatma ('15 dakika önce hatırlat') sözcüklerini "
                "'when_spoken' içinde aynen bırak. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "when_spoken": {"type": "string", "maxLength": 200},
                    "summary": {"type": "string", "maxLength": 200},
                    "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 480},
                    "reminder_minutes": {"type": "integer", "minimum": 0, "maximum": 10080},
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
    reg.register(
        ToolSpec(
            name=TOOL_CALENDAR_CANCEL,
            description=(
                "Odaktaki takvim ETKİNLİĞİNİ İPTAL ETMEYİ ister: 'toplantıyı iptal et', "
                "'perşembeki toplantıyı iptal et', 'randevuyu sil'. Takvimden silme "
                "yetkisi yoksa bunu olduğu gibi söyler; bir öneriden vazgeçmek için "
                "calendar.discard kullanılır. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"event_uid": {"type": "string", "maxLength": 500}},
                "additionalProperties": False,
            },
            handler=calendar_cancel,
        )
    )
    return reg


__all__ = [
    "CALENDAR_TOOL_NAMES",
    "TOOL_CALENDAR_AGENDA",
    "TOOL_CALENDAR_CANCEL",
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
