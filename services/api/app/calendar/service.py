"""``CalendarService``: agenda / find_slot / propose / edit_proposal / read_proposal /
commit / discard (docs/M21_MAIL_CALENDAR_SPEC.md §1, §3, ADR-0084).

The same three tiers ``app.mail.service.MailService`` implements: READ (``agenda``/
``find_slot``) touches only ``calendar_index`` (Cloud Core's own bookkeeping); PREPARE
(``propose``/``edit_proposal``/``read_proposal``) writes a local, reversible
``calendar_proposals`` row, ALWAYS read back with its conflicts named; EXTERNAL MUTATION
(``commit``) is the one method that may ever call a real ``CalendarWriter``, gated by the
SAME ``app.actions.confirmation_gate.check_gate`` ``MailService.send`` uses.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.actions.confirmation_gate import (
    CONFIRM_SOURCE_REST,
    CONFIRM_SOURCE_VOICE,
    GATE_ACCOUNT_MISSING,
    GATE_ALREADY_SENT,
    GATE_CONFIRMATION_NOT_OWNER,
    GATE_NO_CONFIRMATION,
    GATE_NOT_READ_BACK,
    GATE_SEND_DISABLED,
    Confirmation,
    check_gate,
)
from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_FAILED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.calendar.ics import (
    DEFAULT_TIMEZONE,
    MAX_REMINDER_MINUTES,
    Occurrence,
    RRuleError,
    validate_rrule,
)
from app.calendar.models import (
    PROPOSAL_KIND_CANCEL,
    PROPOSAL_KIND_CREATE,
    PROPOSAL_KIND_RESCHEDULE,
    PROPOSAL_STATE_COMMITTED,
    PROPOSAL_STATE_COMMITTING,
    PROPOSAL_STATE_DISCARDED,
    PROPOSAL_STATE_PREPARED,
    PROPOSAL_STATE_READ_BACK,
    CalendarIndexRow,
    CalendarProposalRow,
)
from app.calendar.providers import (
    CalendarProvider,
    CalendarWriter,
    ProposalInput,
    find_conflicts,
)
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_CALENDAR_COMMITTED,
    EVENT_TYPE_CALENDAR_DISCARDED,
    EVENT_TYPE_CALENDAR_PROPOSED,
    EVENT_TYPE_CALENDAR_READ,
    SUBSYSTEM_CALENDAR,
)
from app.logging import get_logger
from app.notifications import events as notification_events
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_EVENT, FOCUS_KIND_PROPOSAL
from app.uistate import UiState
from app.uistate import publish as publish_ui_state

logger = get_logger("app.calendar.service")

SPEECH_ACCOUNT_MISSING = "Tanımlı bir takvim yok efendim."
SPEECH_NO_EVENT = "Hangi etkinlik?"
#: B27 req 731. Spec §1's own boundary - "no delete, no move, no mass action" - said out
#: loud instead of a sentence that reaches nothing. A refusal with a receipt is evidence;
#: the policy that may one day permit it is B46's (matrix row 354).
SPEECH_DELETE_NOT_PERMITTED = (
    "Takvimden etkinlik silme yetkim yok efendim; bunu takviminizden siz yapmalısınız."
)
ERROR_DELETE_NOT_PERMITTED = "deletion_not_permitted"
SPEECH_NO_PROPOSAL = "Önce bir öneri hazırlamam gerekiyor efendim."
SPEECH_NOT_FOUND = "Aradığınızı bulamadım efendim."
#: L1-equivalent for calendar (ADR-0084 addendum 2): a provider-side commit failure
#: reverts to ``read_back`` rather than leaving the row stuck ``committing``.
ERROR_COMMIT_FAILED = "commit_failed"
#: B46 (req 354): who may remove an event from the owner's calendar. ``refuse`` - the
#: default, M21 spec §1's boundary - keeps the honest no; ``confirm`` makes a cancel a
#: proposal read back and confirmed exactly like a create, never a direct delete.
CANCEL_POLICY_REFUSE = "refuse"
CANCEL_POLICY_CONFIRM = "confirm"
#: B46 (req 356): a recurrence the writer refuses to send.
ERROR_INVALID_RECURRENCE = "invalid_recurrence"
#: B46: the writer replaces whole events, so moving or removing one occurrence of a
#: recurring event would silently rewrite or delete the series.
ERROR_RECURRING_SERIES = "recurring_series"
SPEECH_RECURRING_SERIES = (
    "Bu tekrarlayan bir etkinlik efendim; tek bir tekrarını buradan değiştiremem, "
    "takviminizden yapmalısınız."
)
#: B46 (req 359, 361): how far ahead the clock mirrors the calendar into the index.
SYNC_HORIZON_DAYS = 14
#: B46 (req 358): how far ahead each pass looks for a due reminder.
REMINDER_LOOKAHEAD = timedelta(days=1)
INDEX_SOURCE_READ = "read"
INDEX_SOURCE_SYNC = "sync"
MAX_INDEX_ROWS_PER_PASS = 500

_GATE_SPEECH: dict[str, str] = {
    GATE_ACCOUNT_MISSING: SPEECH_ACCOUNT_MISSING,
    GATE_NOT_READ_BACK: "Önce öneriyi okumam gerekiyor efendim.",
    GATE_NO_CONFIRMATION: "Önce öneriyi okumam gerekiyor efendim.",
    GATE_CONFIRMATION_NOT_OWNER: "Önce öneriyi okumam gerekiyor efendim.",
    GATE_ALREADY_SENT: "Bu öneri zaten uygulanmış efendim.",
    GATE_SEND_DISABLED: "Takvim değişikliği şu anda kapalı efendim.",
}


def confirmed_by_label(confirmation: Confirmation) -> str:
    """``"rest:<session>"`` / ``"voice:<session>:<turn>"`` (ADR-0084 addendum 2) — the
    same literal format ``app.mail.service.confirmed_by_label`` stamps."""
    if confirmation.source == CONFIRM_SOURCE_REST:
        return f"{CONFIRM_SOURCE_REST}:{confirmation.session_id}"
    return f"{CONFIRM_SOURCE_VOICE}:{confirmation.session_id}:{confirmation.turn}"


#: The owner's own zone (spec §2's product default) — every ``start``/``end`` is stored
#: as a genuine UTC instant (see ``_utc``) and converted back to THIS zone at every
#: display/serialisation boundary, so a value is never shown in whatever zone happened to
#: survive a round trip.
_ISTANBUL = ZoneInfo(DEFAULT_TIMEZONE)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    """SQLite has no native timezone-aware column type — a ``DateTime(timezone=True))``
    value round-trips through it NAIVE even though Postgres (production) keeps the real
    offset. Every ``start``/``end``/``read_back_at``/``confirmed_at`` this service writes
    is always a genuine UTC instant (``_utc`` below converts before assignment), so a
    naive value read back is safely assumed UTC — the same rule
    ``app.actions.confirmation_gate._aware`` already applies to the gate's own two
    timestamps."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _utc(value: datetime) -> datetime:
    """The canonical STORED form for any timestamp column this service writes."""
    return value.astimezone(UTC)


def _local(value: datetime) -> datetime:
    """The canonical DISPLAYED form: the owner's own zone, from a genuine (or
    round-trip-recovered) UTC instant."""
    return _aware(value).astimezone(_ISTANBUL)


def _fmt_time(dt: datetime) -> str:
    return _local(dt).strftime("%d %B %Y %H:%M")


def _proposal_dict(row: CalendarProposalRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "event_uid": row.event_uid,
        "summary": row.summary,
        "start": _local(row.start).isoformat(),
        "end": _local(row.end).isoformat(),
        "location": row.location,
        "rrule": row.rrule,
        "recurrence": recurrence_phrase(row.rrule) or None,
        "reminder_minutes": row.reminder_minutes,
        "conflicts": list(row.conflicts_json or []),
        "state": row.state,
        "read_back_at": _local(row.read_back_at).isoformat() if row.read_back_at else None,
        "read_back_session_id": row.read_back_session_id,
        "read_back_turn": row.read_back_turn,
        "confirmed_at": _local(row.confirmed_at).isoformat() if row.confirmed_at else None,
        "confirmed_by": row.confirmed_by,
        "last_error": row.last_error,
        "committed_event_uid": row.committed_event_uid,
    }


_BYDAY_TR: dict[str, str] = {
    "MO": "pazartesi",
    "TU": "salı",
    "WE": "çarşamba",
    "TH": "perşembe",
    "FR": "cuma",
    "SA": "cumartesi",
    "SU": "pazar",
}


def recurrence_phrase(rrule: str | None) -> str:
    """B46 (req 356): the owner's own words for the rule the writer will send."""
    if not rrule:
        return ""
    parts = dict(piece.split("=", 1) for piece in rrule.split(";") if "=" in piece)
    freq = parts.get("FREQ", "")
    interval = int(parts.get("INTERVAL", "1") or 1)
    days = [_BYDAY_TR.get(code, code) for code in parts.get("BYDAY", "").split(",") if code]
    if freq == "WEEKLY" and days == ["pazartesi", "salı", "çarşamba", "perşembe", "cuma"]:
        text = "hafta içi her gün"
    elif freq == "DAILY":
        text = "her gün" if interval == 1 else f"{interval} günde bir"
    elif freq == "WEEKLY":
        text = "her hafta" if interval == 1 else f"{interval} haftada bir"
        if days:
            text += " " + ", ".join(days)
    elif freq == "MONTHLY":
        text = "her ay" if interval == 1 else f"{interval} ayda bir"
    elif freq == "YEARLY":
        text = "her yıl"
    else:
        text = "tekrarlayan"
    if parts.get("COUNT"):
        text += f", {parts['COUNT']} kez"
    return text


def reminder_phrase(minutes: int | None) -> str:
    """B46 (req 357): the reminder as the owner hears it in the read-back."""
    if minutes is None:
        return ""
    if minutes == 0:
        return "başladığında hatırlatarak"
    if minutes % 1440 == 0:
        return f"{minutes // 1440} gün önce hatırlatarak"
    if minutes % 60 == 0:
        return f"{minutes // 60} saat önce hatırlatarak"
    return f"{minutes} dakika önce hatırlatarak"


def _proposal_speech(row: CalendarProposalRow) -> str:
    if row.kind == PROPOSAL_KIND_CANCEL:
        return (
            f"{row.summary}, {_fmt_time(row.start)} etkinliğini takviminizden silmeyi "
            "öneriyorum. Onaylıyor musunuz?"
        )
    verb = "taşımayı" if row.kind == PROPOSAL_KIND_RESCHEDULE else "eklemeyi"
    extras = [
        phrase
        for phrase in (recurrence_phrase(row.rrule), reminder_phrase(row.reminder_minutes))
        if phrase
    ]
    how = f" ({'; '.join(extras)})" if extras else ""
    base = (
        f"{row.summary}, {_fmt_time(row.start)} - {_local(row.end).strftime('%H:%M')}{how} "
        f"olarak {verb} öneriyorum."
    )
    conflicts = list(row.conflicts_json or [])
    if conflicts:
        names = ", ".join(c.get("summary", c.get("uid", "")) for c in conflicts)
        base += f" Ancak {names} ile çakışıyor."
    base += " Onaylıyor musunuz?"
    return base


class CalendarService:
    def __init__(
        self,
        provider: CalendarProvider | None,
        writer: CalendarWriter | None,
        *,
        cancel_policy: str = CANCEL_POLICY_REFUSE,
    ) -> None:
        self._provider = provider
        self._writer = writer
        #: B46 (req 354): anything but an explicit "confirm" keeps the refusal.
        self._cancel_policy = (
            CANCEL_POLICY_CONFIRM
            if cancel_policy == CANCEL_POLICY_CONFIRM
            else CANCEL_POLICY_REFUSE
        )
        #: See ``app.mail.service.MailService``'s identical field: "account exists" is
        #: decided from the READ provider, independent of whether the writer was built
        #: (which depends on the host flag) — the same account_missing/send_disabled split.
        self._account_configured = provider is not None

    # ------------------------------------------------------------------ plumbing

    def _receipt(
        self,
        *,
        capability: str,
        requested_state: str,
        execution: str,
        terminal: str,
        server: dict[str, Any],
        speech: str,
        db: Session | None = None,
        error_class: str | None = None,
        session_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        receipt = ActionReceipt(
            action_id=str(uuid.uuid4()),
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
            session_id=session_id,
            observed_at=now,
        )
        if db is not None:
            record_receipt(db, receipt, SUBSYSTEM_CALENDAR)
        out = receipt.as_dict()
        if extra:
            out.update(extra)
        return out

    def _account_missing(
        self, *, capability: str, session_id: str | None, db: Session
    ) -> dict[str, Any]:
        return self._receipt(
            capability=capability,
            requested_state="read",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": "account_missing"},
            speech=SPEECH_ACCOUNT_MISSING,
            db=db,
            error_class="account_missing",
            session_id=session_id,
        )

    def _ledger(
        self,
        db: Session | None,
        *,
        event_type: str,
        action: str,
        summary: str,
        detail: dict[str, Any],
    ) -> None:
        if db is None:
            return
        try:
            ledger_service.record(
                db,
                ledger_service.ActivityEvent(
                    event_type=event_type,
                    subsystem=SUBSYSTEM_CALENDAR,
                    action=action,
                    factual_summary=summary,
                    occurred_at=_now(),
                    detail_json=detail,
                    source="live",
                    source_ref=f"{action}:{uuid.uuid4()}",
                ),
            )
        except Exception:  # noqa: BLE001 - evidence, never a dependency of the action
            logger.warning("calendar_ledger_failed", action=action)

    def _publish(
        self, *, rng: str | None = None, event: str | None = None, proposal_state: str | None = None
    ) -> None:
        metadata: dict[str, Any] = {}
        if rng:
            metadata["range"] = rng[:32]
        if event:
            metadata["event"] = event[:64]
        if proposal_state:
            metadata["proposal_state"] = proposal_state[:32]
        publish_ui_state(
            UiState.CALENDAR_ACTIVITY,
            subsystem=SUBSYSTEM_CALENDAR,
            label=(event or rng or "calendar")[:64],
            metadata=metadata,
        )

    # ------------------------------------------------------------------- READ

    def _window_notes(self) -> tuple[bool, bool]:
        """M2 (security review): whether the LAST ``events``/``free_slots`` call had its
        window narrowed to ``MAX_WINDOW_DAYS`` and/or an RRULE expansion cap actually bit
        — ``False``/``False`` for a provider that does not track this (every fake)."""
        clamped = bool(getattr(self._provider, "last_window_clamped", False))
        truncated = bool(getattr(self._provider, "last_truncated", False))
        return clamped, truncated

    def _window_suffix(self, *, clamped: bool, truncated: bool) -> str:
        if clamped and truncated:
            return " Aralığı sınırladım ve bazı tekrarlar kesildi efendim."
        if clamped:
            return " Aralığı sınırladım efendim."
        if truncated:
            return " Bazı tekrarlar kesildi efendim."
        return ""

    def agenda(
        self,
        db: Session,
        *,
        start: datetime,
        end: datetime,
        range_label: str = "today",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if self._provider is None:
            return self._account_missing(capability="calendar.agenda", session_id=session_id, db=db)
        occs = self._provider.events(start, end)
        clamped, truncated = self._window_notes()
        # B46 (req 359): what the owner heard is indexed - the table M21 declared and
        # nothing ever wrote.
        self._index_occurrences(db, occs, now=_now(), source=INDEX_SOURCE_READ)
        self._ledger(
            db,
            event_type=EVENT_TYPE_CALENDAR_READ,
            action="calendar.agenda",
            summary=f"calendar.agenda -> {len(occs)} etkinlik",
            detail={"range": range_label, "count": len(occs)},
        )
        self._publish(rng=range_label)
        if not occs:
            speech = "Bu aralıkta bir etkinliğiniz yok efendim."
        else:
            names = ", ".join(f"{o.summary} ({o.start.strftime('%H:%M')})" for o in occs)
            speech = f"{len(occs)} etkinliğiniz var efendim: {names}."
        speech += self._window_suffix(clamped=clamped, truncated=truncated)
        return self._receipt(
            capability="calendar.agenda",
            requested_state="read",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"count": len(occs), "window_clamped": clamped, "truncated": truncated},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"events": [o.as_dict() for o in occs]},
        )

    def find_slot(
        self,
        db: Session,
        *,
        start: datetime,
        end: datetime,
        duration_minutes: int,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if self._provider is None:
            return self._account_missing(
                capability="calendar.find_slot", session_id=session_id, db=db
            )
        slots = self._provider.free_slots(start, end, duration_minutes)
        clamped, truncated = self._window_notes()
        self._ledger(
            db,
            event_type=EVENT_TYPE_CALENDAR_READ,
            action="calendar.find_slot",
            summary=f"calendar.find_slot -> {len(slots)} boşluk",
            detail={"duration_minutes": duration_minutes, "count": len(slots)},
        )
        self._publish(rng=f"{start.date()}")
        if not slots:
            speech = "Bu aralıkta uygun bir boşluk bulamadım efendim."
        else:
            s0, e0 = slots[0]
            speech = f"{s0.strftime('%H:%M')} - {e0.strftime('%H:%M')} arası uygunsunuz efendim."
        speech += self._window_suffix(clamped=clamped, truncated=truncated)
        return self._receipt(
            capability="calendar.find_slot",
            requested_state="read",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"count": len(slots), "window_clamped": clamped, "truncated": truncated},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"slots": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in slots]},
        )

    # ------------------------------------------------------ INDEX / SYNC / REMINDERS (B46)

    def _index_occurrences(
        self, db: Session, occs: list[Occurrence], *, now: datetime, source: str
    ) -> tuple[int, int]:
        """One index row per (uid, start). An owner read stamps ``last_used_at``; the
        clock's mirror stamps ``synced_at`` and leaves ``source='sync'``, so the index never
        claims the owner heard what only the clock read."""
        occs = occs[:MAX_INDEX_ROWS_PER_PASS]
        if not occs:
            return 0, 0
        uids = sorted({occ.uid for occ in occs})
        existing: dict[tuple[str, datetime], CalendarIndexRow] = {
            (row.uid, _aware(row.start)): row
            for row in db.execute(
                select(CalendarIndexRow).where(CalendarIndexRow.uid.in_(uids))
            ).scalars()
        }
        added = updated = 0
        for occ in occs:
            key = (occ.uid, _utc(occ.start))
            row = existing.get(key)
            if row is None:
                row = CalendarIndexRow(
                    id=uuid.uuid4(),
                    uid=occ.uid,
                    summary=occ.summary[:998],
                    start=_utc(occ.start),
                    end=_utc(occ.end),
                    all_day=occ.all_day,
                    last_used_at=now,
                    source=source,
                    synced_at=now if source == INDEX_SOURCE_SYNC else None,
                )
                db.add(row)
                existing[key] = row
                added += 1
                continue
            row.summary = occ.summary[:998]
            row.end = _utc(occ.end)
            row.all_day = occ.all_day
            if source == INDEX_SOURCE_READ:
                row.last_used_at = now
                row.source = INDEX_SOURCE_READ
            else:
                row.synced_at = now
            updated += 1
        db.commit()
        return added, updated

    def sync(
        self, db: Session, *, now: datetime | None = None, horizon_days: int = SYNC_HORIZON_DAYS
    ) -> dict[str, Any]:
        """B46 (req 361): the next ``horizon_days`` of the owner's calendar mirrored into
        the index - added, refreshed, and removed when the event is gone upstream (the index
        must not remember a deleted event). Reads only; never writes to the calendar. When
        the provider capped the expansion, nothing is removed: a truncated answer is not
        evidence that an event no longer exists."""
        if self._provider is None:
            return {"status": "no_account", "added": 0, "updated": 0, "removed": 0}
        now = now or _now()
        end = now + timedelta(days=horizon_days)
        occs = self._provider.events(now, end)
        _clamped, truncated = self._window_notes()
        added, updated = self._index_occurrences(db, occs, now=now, source=INDEX_SOURCE_SYNC)
        removed = 0
        if not truncated and len(occs) <= MAX_INDEX_ROWS_PER_PASS:
            present = {(occ.uid, _utc(occ.start)) for occ in occs}
            window_rows = db.execute(
                select(CalendarIndexRow).where(
                    CalendarIndexRow.start >= _utc(now), CalendarIndexRow.start < _utc(end)
                )
            ).scalars()
            for row in list(window_rows):
                start = _aware(row.start)
                if _utc(now) <= start < _utc(end) and (row.uid, start) not in present:
                    db.delete(row)
                    removed += 1
            db.commit()
        if added or removed:
            self._ledger(
                db,
                event_type=EVENT_TYPE_CALENDAR_READ,
                action="calendar.sync",
                summary=f"calendar.sync -> +{added} / -{removed}",
                detail={
                    "added": added,
                    "updated": updated,
                    "removed": removed,
                    "horizon_days": horizon_days,
                },
            )
        return {
            "status": "synced",
            "added": added,
            "updated": updated,
            "removed": removed,
            "seen": len(occs),
            "truncated": truncated,
        }

    def remind_due(self, db: Session, *, now: datetime | None = None) -> dict[str, Any]:
        """B46 (req 358): the reminder an event carries (its VALARM) raised once, as an owner
        notification, when its moment has come and the event has not begun. A moment missed
        while nothing ran is still raised if the event is ahead; once the event has started
        it would be news, not a reminder. With several alarms the earliest is the one
        raised, once."""
        if self._provider is None:
            return {"status": "no_account", "sent": 0}
        now = now or _now()
        due: list[Occurrence] = []
        for occ in self._provider.events(now, now + REMINDER_LOOKAHEAD):
            if not occ.reminders or occ.start <= now:
                continue
            if occ.start - timedelta(minutes=max(occ.reminders)) <= now:
                due.append(occ)
        if not due:
            return {"status": "checked", "sent": 0}
        uids = sorted({occ.uid for occ in due})
        index: dict[tuple[str, datetime], CalendarIndexRow] = {
            (row.uid, _aware(row.start)): row
            for row in db.execute(
                select(CalendarIndexRow).where(CalendarIndexRow.uid.in_(uids))
            ).scalars()
        }
        sent = 0
        for occ in due:
            key = (occ.uid, _utc(occ.start))
            row = index.get(key)
            if row is not None and row.reminded_at is not None:
                continue
            # The notification first, then the stamp: a crash between them repeats one
            # reminder, where the other order could lose it.
            notification_events.calendar_reminder(
                db, event_uid=occ.uid, summary=occ.summary, starts_at=_local(occ.start), now=now
            )
            if row is None:
                row = CalendarIndexRow(
                    id=uuid.uuid4(),
                    uid=occ.uid,
                    summary=occ.summary[:998],
                    start=_utc(occ.start),
                    end=_utc(occ.end),
                    all_day=occ.all_day,
                    last_used_at=now,
                    source=INDEX_SOURCE_SYNC,
                    synced_at=now,
                )
                db.add(row)
                index[key] = row
            row.reminded_at = now
            db.commit()
            sent += 1
            self._ledger(
                db,
                event_type=EVENT_TYPE_CALENDAR_READ,
                action="calendar.reminder",
                summary=f"calendar.reminder -> {occ.summary}",
                detail={"event_uid": occ.uid, "start": _local(occ.start).isoformat()},
            )
        return {"status": "checked", "sent": sent}

    # ------------------------------------------------------------------ PREPARE

    def _upsert_proposal_focus(
        self, db: Session, row: CalendarProposalRow, *, now: datetime
    ) -> None:
        focus_module.set_focus(
            db,
            FOCUS_KIND_PROPOSAL,
            str(row.id),
            label=row.summary,
            source="calendar_propose",
            now=now,
        )

    def _current_event(self, db: Session) -> str | None:
        entry = focus_module.current(db, FOCUS_KIND_EVENT)
        return entry.object_id if entry is not None else None

    def propose(
        self,
        db: Session,
        *,
        summary: str,
        start: datetime,
        end: datetime,
        location: str | None = None,
        rrule: str | None = None,
        reminder_minutes: int | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if self._provider is None:
            return self._account_missing(
                capability="calendar.propose", session_id=session_id, db=db
            )
        # B46 (req 356, 357): the rule and reminder the owner will HEAR in the read-back are
        # validated here, so the writer never meets one it would refuse.
        try:
            rrule = validate_rrule(rrule) if rrule else None
            if reminder_minutes is not None and not (
                0 <= int(reminder_minutes) <= MAX_REMINDER_MINUTES
            ):
                raise RRuleError(f"reminder out of range: {reminder_minutes}")
        except RRuleError as exc:
            return self._receipt(
                capability="calendar.propose",
                requested_state="prepared",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": ERROR_INVALID_RECURRENCE, "detail": str(exc)[:200]},
                speech=(
                    "Bu tekrarı ya da hatırlatmayı anlayamadım efendim; "
                    "daha basit söyler misiniz?"
                ),
                db=db,
                session_id=session_id,
                error_class=ERROR_INVALID_RECURRENCE,
            )
        window_start = start - timedelta(hours=6)
        window_end = end + timedelta(hours=6)
        busy = self._provider.events(window_start, window_end)
        conflicts = find_conflicts(busy, start=start, end=end)
        now = _now()
        row = CalendarProposalRow(
            id=uuid.uuid4(),
            kind=PROPOSAL_KIND_CREATE,
            event_uid=None,
            summary=summary,
            start=_utc(start),
            end=_utc(end),
            location=location,
            rrule=rrule,
            reminder_minutes=reminder_minutes,
            conflicts_json=conflicts,
            state=PROPOSAL_STATE_PREPARED,
            read_back_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        self._upsert_proposal_focus(db, row, now=now)
        self._ledger(
            db,
            event_type=EVENT_TYPE_CALENDAR_PROPOSED,
            action="calendar.propose",
            summary=f"calendar.propose -> {summary}",
            detail={"proposal_id": str(row.id), "conflicts": len(conflicts)},
        )
        self._publish(event=summary, proposal_state=row.state)
        return self._receipt(
            capability="calendar.propose",
            requested_state="prepared",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"proposal_id": str(row.id), "conflicts": len(conflicts)},
            speech=_proposal_speech(row),
            db=db,
            session_id=session_id,
            extra={"proposal": _proposal_dict(row)},
        )

    def propose_reschedule(
        self,
        db: Session,
        *,
        minutes_delta: int,
        event_uid: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """ "Bunu bir saat ertele" (spec §3): the current event -> a reschedule proposal."""
        if self._provider is None:
            return self._account_missing(
                capability="calendar.propose", session_id=session_id, db=db
            )
        uid = event_uid or self._current_event(db)
        if uid is None:
            return {"status": "needs_clarification", "speech": SPEECH_NO_EVENT, "candidates": []}
        occ = self._provider.get_event(uid)
        if occ is None:
            return {"status": "needs_clarification", "speech": SPEECH_NOT_FOUND, "candidates": []}
        if occ.recurring:
            # B46: the writer PUTs a whole event; "move this one" would rewrite the series.
            return self._receipt(
                capability="calendar.propose",
                requested_state="prepared",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"event_uid": uid, "reason": ERROR_RECURRING_SERIES},
                speech=SPEECH_RECURRING_SERIES,
                db=db,
                session_id=session_id,
                error_class=ERROR_RECURRING_SERIES,
            )
        delta = timedelta(minutes=minutes_delta)
        new_start = occ.start + delta
        new_end = occ.end + delta
        window_start = new_start - timedelta(hours=6)
        window_end = new_end + timedelta(hours=6)
        busy = self._provider.events(window_start, window_end)
        conflicts = find_conflicts(busy, start=new_start, end=new_end, exclude_uid=uid)
        now = _now()
        row = CalendarProposalRow(
            id=uuid.uuid4(),
            kind=PROPOSAL_KIND_RESCHEDULE,
            event_uid=uid,
            summary=occ.summary,
            start=_utc(new_start),
            end=_utc(new_end),
            location=None,
            # B46: a reschedule keeps the reminder the event already had.
            reminder_minutes=occ.reminders[0] if occ.reminders else None,
            conflicts_json=conflicts,
            state=PROPOSAL_STATE_PREPARED,
            read_back_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        self._upsert_proposal_focus(db, row, now=now)
        self._ledger(
            db,
            event_type=EVENT_TYPE_CALENDAR_PROPOSED,
            action="calendar.propose",
            summary=f"calendar.propose -> {occ.summary} (ertele)",
            detail={"proposal_id": str(row.id), "conflicts": len(conflicts)},
        )
        self._publish(event=occ.summary, proposal_state=row.state)
        return self._receipt(
            capability="calendar.propose",
            requested_state="prepared",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"proposal_id": str(row.id), "conflicts": len(conflicts)},
            speech=_proposal_speech(row),
            db=db,
            session_id=session_id,
            extra={"proposal": _proposal_dict(row)},
        )

    def _current_proposal(self, db: Session) -> CalendarProposalRow | None:
        entry = focus_module.current(db, FOCUS_KIND_PROPOSAL)
        if entry is None:
            return None
        return db.get(CalendarProposalRow, uuid.UUID(entry.object_id))

    def edit_proposal(
        self,
        db: Session,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        summary: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        row = self._current_proposal(db)
        if row is None or row.state not in (PROPOSAL_STATE_PREPARED, PROPOSAL_STATE_READ_BACK):
            return {"status": "needs_clarification", "speech": SPEECH_NO_PROPOSAL, "candidates": []}
        if start is not None:
            row.start = _utc(start)
        if end is not None:
            row.end = _utc(end)
        if summary is not None:
            row.summary = summary
        row_start, row_end = _aware(row.start), _aware(row.end)
        conflicts: list[dict[str, str]] = []
        if self._provider is not None:
            window_start = row_start - timedelta(hours=6)
            window_end = row_end + timedelta(hours=6)
            busy = self._provider.events(window_start, window_end)
            conflicts = find_conflicts(
                busy, start=row_start, end=row_end, exclude_uid=row.event_uid
            )
        row.conflicts_json = conflicts
        now = _now()
        # H1, ADR-0084 addendum 2: an edit invalidates any earlier read-back (the owner
        # heard the OLD content) - back to prepared, a fresh explicit read-back required.
        row.state = PROPOSAL_STATE_PREPARED
        row.read_back_at = None
        row.read_back_session_id = None
        row.read_back_turn = None
        row.updated_at = now
        db.commit()
        db.refresh(row)
        self._upsert_proposal_focus(db, row, now=now)
        self._ledger(
            db,
            event_type=EVENT_TYPE_CALENDAR_PROPOSED,
            action="calendar.edit_proposal",
            summary=f"calendar.edit_proposal -> {row.summary}",
            detail={"proposal_id": str(row.id)},
        )
        self._publish(event=row.summary, proposal_state=row.state)
        return self._receipt(
            capability="calendar.edit_proposal",
            requested_state="prepared",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"proposal_id": str(row.id)},
            speech=_proposal_speech(row),
            db=db,
            session_id=session_id,
            extra={"proposal": _proposal_dict(row)},
        )

    def read_proposal(
        self,
        db: Session,
        *,
        session_id: str | None = None,
        turn: int | None = None,
    ) -> dict[str, Any]:
        """The EXPLICIT read-back act (H1) — see ``app.mail.service.MailService.
        read_draft``'s docstring; the same session/turn binding, mirrored here."""
        row = self._current_proposal(db)
        if row is None or row.state not in (PROPOSAL_STATE_PREPARED, PROPOSAL_STATE_READ_BACK):
            return {"status": "needs_clarification", "speech": SPEECH_NO_PROPOSAL, "candidates": []}
        now = _now()
        row.state = PROPOSAL_STATE_READ_BACK
        row.read_back_at = now
        row.read_back_session_id = session_id
        row.read_back_turn = turn
        db.commit()
        db.refresh(row)
        self._ledger(
            db,
            event_type=EVENT_TYPE_CALENDAR_PROPOSED,
            action="calendar.read_proposal",
            summary=f"calendar.read_proposal -> {row.summary}",
            detail={"proposal_id": str(row.id)},
        )
        self._publish(event=row.summary, proposal_state=row.state)
        return self._receipt(
            capability="calendar.read_proposal",
            requested_state="read",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"proposal_id": str(row.id)},
            speech=_proposal_speech(row),
            db=db,
            session_id=session_id,
            extra={"proposal": _proposal_dict(row)},
        )

    # ---------------------------------------------------------- EXTERNAL MUTATION

    def commit(
        self,
        db: Session,
        *,
        proposal_id: str | None = None,
        host_flag_enabled: bool,
        session_id: str | None = None,
        confirmation: Confirmation | None = None,
    ) -> dict[str, Any]:
        """EXTERNAL MUTATION (spec §1) — see ``app.mail.service.MailService.send``'s
        docstring for the gate/CAS/failure-handling discipline mirrored here exactly."""
        row = (
            db.get(CalendarProposalRow, uuid.UUID(proposal_id))
            if proposal_id
            else self._current_proposal(db)
        )
        if row is None:
            return {
                "status": "needs_clarification",
                "speech": "Neyi onaylayayım?",
                "candidates": [],
            }
        result = check_gate(
            state=row.state,
            prepared_state=PROPOSAL_STATE_PREPARED,
            read_back_state=PROPOSAL_STATE_READ_BACK,
            read_back_at=row.read_back_at,
            read_back_session_id=row.read_back_session_id,
            read_back_turn=row.read_back_turn,
            confirmation=confirmation,
            host_flag_enabled=host_flag_enabled,
            provider_available=self._account_configured,
        )
        if not result.ok:
            assert result.reason is not None
            self._ledger(
                db,
                event_type=EVENT_TYPE_CALENDAR_COMMITTED,
                action="calendar.commit",
                summary=f"calendar.commit refused ({result.reason})",
                detail={"proposal_id": str(row.id), "reason": result.reason},
            )
            return self._receipt(
                capability="calendar.commit",
                requested_state="committed",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"proposal_id": str(row.id), "reason": result.reason},
                speech=_GATE_SPEECH[result.reason],
                db=db,
                session_id=session_id,
                error_class=result.reason,
            )
        if self._writer is None:
            return self._receipt(
                capability="calendar.commit",
                requested_state="committed",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"proposal_id": str(row.id), "reason": GATE_SEND_DISABLED},
                speech=_GATE_SPEECH[GATE_SEND_DISABLED],
                db=db,
                session_id=session_id,
                error_class=GATE_SEND_DISABLED,
            )
        assert confirmation is not None  # the gate above already required one to pass
        now = _now()
        confirmed_by = confirmed_by_label(confirmation)
        cas = db.execute(
            sa_update(CalendarProposalRow)
            .where(
                CalendarProposalRow.id == row.id,
                CalendarProposalRow.state == PROPOSAL_STATE_READ_BACK,
            )
            .values(
                state=PROPOSAL_STATE_COMMITTING,
                confirmed_at=now,
                confirmed_by=confirmed_by,
                updated_at=now,
            )
        )
        db.commit()
        if cas.rowcount != 1:
            # H2: lost the race - same receipt a genuinely-already-committed proposal gets.
            self._ledger(
                db,
                event_type=EVENT_TYPE_CALENDAR_COMMITTED,
                action="calendar.commit",
                summary="calendar.commit refused (already_sent, lost the race)",
                detail={"proposal_id": str(row.id), "reason": GATE_ALREADY_SENT},
            )
            return self._receipt(
                capability="calendar.commit",
                requested_state="committed",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"proposal_id": str(row.id), "reason": GATE_ALREADY_SENT},
                speech=_GATE_SPEECH[GATE_ALREADY_SENT],
                db=db,
                session_id=session_id,
                error_class=GATE_ALREADY_SENT,
            )
        db.refresh(row)
        proposal_input = ProposalInput(
            kind=row.kind,
            event_uid=row.event_uid,
            summary=row.summary,
            start=_aware(row.start),
            end=_aware(row.end),
            location=row.location,
            rrule=row.rrule,
            reminder_minutes=row.reminder_minutes,
        )
        try:
            if row.kind == PROPOSAL_KIND_CANCEL and row.event_uid:
                # B46 (req 354): only ever reached under the confirm policy, after the gate.
                self._writer.delete(row.event_uid)
                event_uid = row.event_uid
            elif row.kind == PROPOSAL_KIND_RESCHEDULE and row.event_uid:
                event_uid = self._writer.update(row.event_uid, proposal_input)
            else:
                event_uid = self._writer.create(proposal_input)
        except Exception as exc:  # noqa: BLE001 - L1-equivalent: revert, never crash
            failed_at = _now()
            db.execute(
                sa_update(CalendarProposalRow)
                .where(CalendarProposalRow.id == row.id)
                .values(
                    state=PROPOSAL_STATE_READ_BACK,
                    last_error=type(exc).__name__,
                    updated_at=failed_at,
                )
            )
            db.commit()
            db.refresh(row)
            logger.warning(
                "calendar_commit_failed", proposal_id=str(row.id), error_class=type(exc).__name__
            )
            self._ledger(
                db,
                event_type=EVENT_TYPE_CALENDAR_COMMITTED,
                action="calendar.commit",
                summary=f"calendar.commit failed ({type(exc).__name__})",
                detail={"proposal_id": str(row.id), "error_class": type(exc).__name__},
            )
            return self._receipt(
                capability="calendar.commit",
                requested_state="committed",
                execution=EXECUTION_FAILED,
                terminal=TERMINAL_FAILED,
                server={"proposal_id": str(row.id), "reason": ERROR_COMMIT_FAILED},
                speech="Takvime işleyemedim efendim; öneri duruyor.",
                db=db,
                session_id=session_id,
                error_class=ERROR_COMMIT_FAILED,
                extra={"proposal": _proposal_dict(row)},
            )
        row.state = PROPOSAL_STATE_COMMITTED
        row.committed_event_uid = event_uid
        row.updated_at = now
        if row.kind == PROPOSAL_KIND_CANCEL and row.event_uid:
            # The index must not remember an event the owner just removed.
            db.execute(sa_delete(CalendarIndexRow).where(CalendarIndexRow.uid == row.event_uid))
        db.commit()
        db.refresh(row)
        self._ledger(
            db,
            event_type=EVENT_TYPE_CALENDAR_COMMITTED,
            action="calendar.commit",
            summary=f"calendar.commit -> {row.summary}",
            detail={"proposal_id": str(row.id), "event_uid": event_uid},
        )
        self._publish(event=row.summary, proposal_state=row.state)
        return self._receipt(
            capability="calendar.commit",
            requested_state="committed",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"proposal_id": str(row.id), "event_uid": event_uid},
            speech=(
                "Takvimden sildim efendim."
                if row.kind == PROPOSAL_KIND_CANCEL
                else "Onayladım efendim."
            ),
            db=db,
            session_id=session_id,
            extra={"proposal": _proposal_dict(row)},
        )

    def discard(
        self, db: Session, *, proposal_id: str | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        row = (
            db.get(CalendarProposalRow, uuid.UUID(proposal_id))
            if proposal_id
            else self._current_proposal(db)
        )
        if row is None:
            return {"status": "needs_clarification", "speech": SPEECH_NO_PROPOSAL, "candidates": []}
        now = _now()
        row.state = PROPOSAL_STATE_DISCARDED
        row.updated_at = now
        db.commit()
        db.refresh(row)
        self._ledger(
            db,
            event_type=EVENT_TYPE_CALENDAR_DISCARDED,
            action="calendar.discard",
            summary=f"calendar.discard -> {row.summary}",
            detail={"proposal_id": str(row.id)},
        )
        self._publish(event=row.summary, proposal_state=row.state)
        return self._receipt(
            capability="calendar.discard",
            requested_state="discarded",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"proposal_id": str(row.id)},
            speech="Vazgeçtim efendim.",
            db=db,
            session_id=session_id,
            extra={"proposal": _proposal_dict(row)},
        )

    def _propose_cancel(
        self, db: Session, occ: Occurrence, *, session_id: str | None
    ) -> dict[str, Any]:
        """B46 (req 354) under ``calendar_cancel_policy=confirm``: a cancel is a PROPOSAL -
        read back, confirmed on the owner's next turn, then the writer's delete, through the
        same gate a create passes. A recurring event is refused: the writer removes whole
        events, and one "iptal et" must never delete a series."""
        if occ.recurring:
            return self._receipt(
                capability="calendar.cancel",
                requested_state="prepared",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"event_uid": occ.uid, "reason": ERROR_RECURRING_SERIES},
                speech=SPEECH_RECURRING_SERIES,
                db=db,
                session_id=session_id,
                error_class=ERROR_RECURRING_SERIES,
            )
        now = _now()
        row = CalendarProposalRow(
            id=uuid.uuid4(),
            kind=PROPOSAL_KIND_CANCEL,
            event_uid=occ.uid,
            summary=occ.summary,
            start=_utc(occ.start),
            end=_utc(occ.end),
            location=None,
            conflicts_json=[],
            state=PROPOSAL_STATE_PREPARED,
            read_back_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        self._upsert_proposal_focus(db, row, now=now)
        self._ledger(
            db,
            event_type=EVENT_TYPE_CALENDAR_PROPOSED,
            action="calendar.cancel",
            summary=f"calendar.cancel -> {occ.summary} (öneri)",
            detail={"proposal_id": str(row.id), "event_uid": occ.uid},
        )
        self._publish(event=occ.summary, proposal_state=row.state)
        return self._receipt(
            capability="calendar.cancel",
            requested_state="prepared",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"proposal_id": str(row.id), "event_uid": occ.uid},
            speech=_proposal_speech(row),
            db=db,
            session_id=session_id,
            extra={"proposal": _proposal_dict(row)},
        )

    def cancel_event(
        self, db: Session, *, event_uid: str | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        """ "Toplantıyı iptal et." (B27 req 731) — the focused event, and an honest no.

        The writer has ``create`` and ``update`` and, by spec §1, no ``delete``; this
        method exists so the sentence reaches the calendar and comes back with a RECEIPT
        that says which event and why not, instead of reaching nothing. The event is
        resolved the way every other tool here resolves it (the durable focus), so
        "which one did it refuse" is on the record too.
        """
        if self._provider is None:
            return self._account_missing(capability="calendar.cancel", session_id=session_id, db=db)
        entry = None if event_uid else focus_module.current(db, FOCUS_KIND_EVENT)
        uid = event_uid or (entry.object_id if entry is not None else None)
        if uid is None:
            return {"status": "needs_clarification", "speech": SPEECH_NO_EVENT, "candidates": []}
        occurrence = self._provider.get_event(uid)
        summary = (
            occurrence.summary
            if occurrence is not None
            else (entry.label if entry is not None and entry.label else uid)
        )
        if self._cancel_policy == CANCEL_POLICY_CONFIRM and occurrence is not None:
            return self._propose_cancel(db, occurrence, session_id=session_id)
        return self._receipt(
            capability="calendar.cancel",
            requested_state="cancelled",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"event_uid": uid, "reason": ERROR_DELETE_NOT_PERMITTED},
            speech=SPEECH_DELETE_NOT_PERMITTED,
            db=db,
            error_class=ERROR_DELETE_NOT_PERMITTED,
            session_id=session_id,
            extra={"event": {"uid": uid, "summary": summary}},
        )


__all__ = [
    "CANCEL_POLICY_CONFIRM",
    "CANCEL_POLICY_REFUSE",
    "ERROR_COMMIT_FAILED",
    "ERROR_INVALID_RECURRENCE",
    "ERROR_RECURRING_SERIES",
    "ERROR_DELETE_NOT_PERMITTED",
    "SPEECH_DELETE_NOT_PERMITTED",
    "CalendarService",
    "SPEECH_ACCOUNT_MISSING",
    "SPEECH_NO_EVENT",
    "SPEECH_NO_PROPOSAL",
    "confirmed_by_label",
]
