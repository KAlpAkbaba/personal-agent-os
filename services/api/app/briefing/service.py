"""``BriefingService.build``: greeting, local date/time, weather, system status,
overnight autonomous-work summary, CI/deploy state and (where authorised and available)
calendar — assembled from REAL sources only (task brief §3). A source that cannot answer
is said in one honest clause, never invented; there is no per-source "reminders"
integration in this repository yet, so that item is silently omitted rather than
fabricated (the same "the honest failure is always better than the invented answer" rule
applied to an ABSENT source, not just a failing one).

Default answer is CONCISE (task brief §3): one paragraph, plain sentences, no internal
telemetry unless the owner explicitly asked "Sistem durumunu teknik anlat." — this module
never emits the technical variant itself; a caller that wants it passes ``technical=True``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.briefing.models import BRIEFING_PREFERENCES_ID, BriefingPreferencesRow
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_MORNING_BRIEFING_DELIVERED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    SUBSYSTEM_BRIEFING,
    SUBSYSTEM_DEPLOYMENT,
    SUBSYSTEM_EVOLUTION,
    SUBSYSTEM_GENESIS,
)
from app.logging import get_logger
from app.release.version import release_model

logger = get_logger("app.briefing.service")

CAPABILITY_MORNING_BRIEFING = "briefing.morning"
#: "Sistem durumu nasıl?" / "Gece neler yaptın?" (task brief §4): narrower than the full
#: briefing, distinct tools so a single-topic question never speaks the whole briefing
#: (task brief §5's own "deterministic and distinct" requirement) — reuse the SAME
#: section builders ``build`` uses, so a narrow answer and the briefing's own section can
#: never disagree about what happened.
CAPABILITY_SYSTEM_STATUS = "briefing.system_status"
CAPABILITY_OVERNIGHT_WORK = "briefing.overnight_work"

_ZONE = ZoneInfo("Europe/Istanbul")

_WEEKDAYS_TR: tuple[str, ...] = (
    "Pazartesi",
    "Salı",
    "Çarşamba",
    "Perşembe",
    "Cuma",
    "Cumartesi",
    "Pazar",
)
_MONTHS_TR: tuple[str, ...] = (
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
)

SPEECH_DISABLED = "Sabah özeti şu anda kapalı efendim."
_UPDATABLE_FIELDS = (
    "morning_briefing_enabled",
    "include_weather",
    "include_system_status",
    "include_calendar",
    "include_overnight_work",
    "include_news_summary",
    "auto_open_news_video",
)

#: A digest-worthy overnight subsystem set (the same "ordinary autonomous development"
#: class ``app.ledger.briefing._DIGEST_PREFIXES`` already names for the proactive
#: pending-briefing queue — reused here rather than re-decided, so "what did you do
#: overnight" and the proactive digest never disagree about what counts).
_OVERNIGHT_SUBSYSTEMS: tuple[str, ...] = (
    SUBSYSTEM_EVOLUTION,
    SUBSYSTEM_GENESIS,
    SUBSYSTEM_DEPLOYMENT,
)
#: How far back "overnight" reaches: there is no durable "last briefing delivered at"
#: timestamp to anchor on (a future refinement, not needed for a truthful answer today
#: — a fixed window is still real evidence, just not adaptive to when the owner last
#: asked). 12 hours covers a full night's sleep with margin, without pulling in
#: "yesterday afternoon" work that is no longer "overnight" by any reasonable reading.
_OVERNIGHT_WINDOW = timedelta(hours=12)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _local(now: datetime) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(_ZONE)


def greeting_for(now_local: datetime) -> str:
    """Time-of-day Turkish greeting — "Günaydın." only in the morning window (task brief
    §4's own example utterance), a plain "İyi günler"/"İyi akşamlar"/"İyi geceler"
    otherwise, so a briefing asked for at 9 PM never opens with a false "Günaydın"."""
    hour = now_local.hour
    if 5 <= hour < 12:
        return "Günaydın efendim."
    if 12 <= hour < 18:
        return "İyi günler efendim."
    if 18 <= hour < 22:
        return "İyi akşamlar efendim."
    return "İyi geceler efendim."


def date_time_sentence(now_local: datetime) -> str:
    weekday = _WEEKDAYS_TR[now_local.weekday()]
    month = _MONTHS_TR[now_local.month - 1]
    return f"Bugün {now_local.day} {month} {now_local.year}, {weekday}, saat {now_local:%H:%M}."


# ------------------------------------------------------------------------ preferences


def get_preferences_row(session: Session) -> BriefingPreferencesRow:
    """The single preferences row, created with the spec's own defaults on first read —
    same lazy-singleton discipline as ``app.ambient.service.get_policy_row``."""
    row = session.get(BriefingPreferencesRow, BRIEFING_PREFERENCES_ID)
    if row is None:
        row = BriefingPreferencesRow(preferences_id=BRIEFING_PREFERENCES_ID)
        session.add(row)
        session.commit()
    return row


def update_preferences(session: Session, updates: dict[str, Any]) -> BriefingPreferencesRow:
    row = get_preferences_row(session)
    for key, value in updates.items():
        if key not in _UPDATABLE_FIELDS:
            raise ValueError(f"unknown briefing preference: {key!r}")
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be a bool")
        setattr(row, key, value)
    session.commit()
    return row


# ----------------------------------------------------------------------- components


def _system_status_sentence(*, settings: Any, live: dict[str, Any]) -> str:
    model = release_model(settings)
    uptime_h = model["uptime_s"] / 3600.0
    parts = [f"Sistem {model['version']} sürümünde, {uptime_h:.1f} saattir çalışıyor."]
    registry = live.get("device_statuses")
    if registry is not None:
        known = registry.all()
        parts.append(
            f"{len(known)} cihazdan durum aldım." if known else "Şu an rapor eden bir cihaz yok."
        )
    evolution_on = bool(getattr(settings, "evolution_supervisor_enabled", False))
    parts.append("Otonom geliştirme açık." if evolution_on else "Otonom geliştirme kapalı.")
    return " ".join(parts)


def _overnight_summary_sentence(session: Session, *, now: datetime) -> str:
    since = now - _OVERNIGHT_WINDOW
    events = ledger_service.query(
        session, since=since, until=now, subsystems=_OVERNIGHT_SUBSYSTEMS, limit=200
    )
    if not events:
        return "Gece boyunca otonom bir geliştirme etkinliği olmadı."
    completed = sum(1 for e in events if e.status == STATUS_COMPLETED)
    failed = sum(1 for e in events if e.status == STATUS_FAILED)
    sentence = f"Gece boyunca {len(events)} otonom geliştirme etkinliği oldu"
    if completed or failed:
        sentence += f": {completed} tamamlandı, {failed} başarısız oldu."
    else:
        sentence += "."
    return sentence


def _weather_sentence(
    session: Session,
    *,
    live: dict[str, Any],
    device_id: uuid.UUID | None,
    session_id: str | None,
    now: datetime,
) -> str | None:
    weather_service = live.get("weather_service")
    if weather_service is None:
        return None
    result = weather_service.current(session, device_id=device_id, session_id=session_id, now=now)
    speech = result.get("speech")
    return str(speech) if speech else None


def _calendar_sentence(
    session: Session, *, live: dict[str, Any], now_local: datetime, session_id: str | None
) -> str | None:
    calendar_service = live.get("calendar_service")
    if calendar_service is None:
        return None
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    result = calendar_service.agenda(
        session, start=start, end=end, range_label="today", session_id=session_id
    )
    speech = result.get("speech")
    return str(speech) if speech else None


class BriefingService:
    def build(
        self,
        session: Session,
        *,
        settings: Any,
        live: dict[str, Any],
        device_id: uuid.UUID | None = None,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or _now_utc()
        now_local = _local(now)
        prefs = get_preferences_row(session)

        if not prefs.morning_briefing_enabled:
            return self._answer(
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                speech=SPEECH_DISABLED,
                db=session,
                session_id=session_id,
                server={"reason": "briefing_disabled"},
                error_class="briefing_disabled",
                now=now,
            )

        sections: dict[str, str] = {}
        sentences = [greeting_for(now_local), date_time_sentence(now_local)]

        if prefs.include_weather:
            weather = _weather_sentence(
                session, live=live, device_id=device_id, session_id=session_id, now=now
            )
            if weather:
                sections["weather"] = weather
                sentences.append(weather)

        if prefs.include_system_status:
            status = _system_status_sentence(settings=settings, live=live)
            sections["system_status"] = status
            sentences.append(status)

        if prefs.include_overnight_work:
            overnight = _overnight_summary_sentence(session, now=now)
            sections["overnight_work"] = overnight
            sentences.append(overnight)

        if prefs.include_calendar:
            calendar = _calendar_sentence(
                session, live=live, now_local=now_local, session_id=session_id
            )
            if calendar:
                sections["calendar"] = calendar
                sentences.append(calendar)

        if prefs.include_news_summary:
            # No news resolver exists in this repository yet (another track's scope,
            # task brief) — the honest one-clause absence, never a fabricated summary,
            # and auto_open_news_video is not acted on here for the same reason: there
            # is no news-video capability to open yet. Wiring it in is a follow-up once
            # that track lands, with no change to this preference's shape.
            gap = "Haber özeti şu an bağlı değil efendim."
            sections["news_summary"] = gap
            sentences.append(gap)

        speech = " ".join(sentences)

        try:
            ledger_service.record(
                session,
                ledger_service.ActivityEvent(
                    event_type=EVENT_TYPE_MORNING_BRIEFING_DELIVERED,
                    subsystem=SUBSYSTEM_BRIEFING,
                    action=CAPABILITY_MORNING_BRIEFING,
                    factual_summary=f"briefing.morning -> {', '.join(sections) or 'greeting only'}",
                    occurred_at=now,
                    detail_json={"sections": list(sections)},
                    source="live",
                    source_ref=f"briefing:{uuid.uuid4()}",
                ),
            )
        except Exception:  # noqa: BLE001 - evidence, never a dependency of the answer
            logger.warning("briefing_ledger_failed")

        return self._answer(
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            speech=speech,
            db=session,
            session_id=session_id,
            server={"sections": sections},
            now=now,
        )

    def system_status(
        self,
        session: Session,
        *,
        settings: Any,
        live: dict[str, Any],
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """ "Sistem durumu nasıl?" — the same sentence ``build`` includes under
        ``include_system_status``, on its own."""
        now = now or _now_utc()
        speech = _system_status_sentence(settings=settings, live=live)
        return self._answer(
            capability=CAPABILITY_SYSTEM_STATUS,
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            speech=speech,
            db=session,
            session_id=session_id,
            server={},
            now=now,
        )

    def overnight_work(
        self, session: Session, *, session_id: str | None = None, now: datetime | None = None
    ) -> dict[str, Any]:
        """ "Gece neler yaptın?" — the same sentence ``build`` includes under
        ``include_overnight_work``, on its own, from the same durable ledger evidence."""
        now = now or _now_utc()
        speech = _overnight_summary_sentence(session, now=now)
        return self._answer(
            capability=CAPABILITY_OVERNIGHT_WORK,
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            speech=speech,
            db=session,
            session_id=session_id,
            server={},
            now=now,
        )

    def _answer(
        self,
        *,
        capability: str = CAPABILITY_MORNING_BRIEFING,
        execution: str,
        terminal: str,
        speech: str,
        db: Session,
        session_id: str | None,
        server: dict[str, Any],
        now: datetime,
        error_class: str | None = None,
    ) -> dict[str, Any]:
        receipt = ActionReceipt(
            action_id=str(uuid.uuid4()),
            capability=capability,
            requested_state="read",
            execution_status=execution,
            terminal_status=terminal,
            observed_after={"server": server, "local": {}},
            error_class=error_class,
            speech=speech,
            started_at=now,
            completed_at=now,
            session_id=session_id,
            observed_at=now,
        )
        record_receipt(db, receipt, SUBSYSTEM_BRIEFING)
        return receipt.as_dict()


__all__ = [
    "CAPABILITY_MORNING_BRIEFING",
    "CAPABILITY_OVERNIGHT_WORK",
    "CAPABILITY_SYSTEM_STATUS",
    "BriefingService",
    "date_time_sentence",
    "get_preferences_row",
    "greeting_for",
    "update_preferences",
]
