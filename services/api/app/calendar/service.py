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
from app.calendar.ics import DEFAULT_TIMEZONE
from app.calendar.models import (
    PROPOSAL_KIND_CREATE,
    PROPOSAL_KIND_RESCHEDULE,
    PROPOSAL_STATE_COMMITTED,
    PROPOSAL_STATE_COMMITTING,
    PROPOSAL_STATE_DISCARDED,
    PROPOSAL_STATE_PREPARED,
    PROPOSAL_STATE_READ_BACK,
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
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_EVENT, FOCUS_KIND_PROPOSAL
from app.uistate import UiState
from app.uistate import publish as publish_ui_state

logger = get_logger("app.calendar.service")

SPEECH_ACCOUNT_MISSING = "Tanımlı bir takvim yok efendim."
SPEECH_NO_EVENT = "Hangi etkinlik?"
SPEECH_NO_PROPOSAL = "Önce bir öneri hazırlamam gerekiyor efendim."
SPEECH_NOT_FOUND = "Aradığınızı bulamadım efendim."
#: L1-equivalent for calendar (ADR-0084 addendum 2): a provider-side commit failure
#: reverts to ``read_back`` rather than leaving the row stuck ``committing``.
ERROR_COMMIT_FAILED = "commit_failed"

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


def _proposal_speech(row: CalendarProposalRow) -> str:
    verb = "taşımayı" if row.kind == PROPOSAL_KIND_RESCHEDULE else "eklemeyi"
    base = (
        f"{row.summary}, {_fmt_time(row.start)} - {_local(row.end).strftime('%H:%M')} olarak "
        f"{verb} öneriyorum."
    )
    conflicts = list(row.conflicts_json or [])
    if conflicts:
        names = ", ".join(c.get("summary", c.get("uid", "")) for c in conflicts)
        base += f" Ancak {names} ile çakışıyor."
    base += " Onaylıyor musunuz?"
    return base


class CalendarService:
    def __init__(self, provider: CalendarProvider | None, writer: CalendarWriter | None) -> None:
        self._provider = provider
        self._writer = writer
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
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if self._provider is None:
            return self._account_missing(
                capability="calendar.propose", session_id=session_id, db=db
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
        )
        try:
            if row.kind == PROPOSAL_KIND_RESCHEDULE and row.event_uid:
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
            speech="Onayladım efendim.",
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


__all__ = [
    "ERROR_COMMIT_FAILED",
    "CalendarService",
    "SPEECH_ACCOUNT_MISSING",
    "SPEECH_NO_EVENT",
    "SPEECH_NO_PROPOSAL",
    "confirmed_by_label",
]
