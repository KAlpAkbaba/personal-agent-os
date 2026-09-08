"""``MailService``: inbox_summary / search / read / thread / draft_reply / draft_new /
edit_draft / read_draft / send / discard (docs/M21_MAIL_CALENDAR_SPEC.md §1, §3, ADR-0084).

Three tiers, three vocabularies (spec §1): READ (``inbox_summary``/``search``/``read``/
``thread``) mutates nothing but ``mail_index`` (Cloud Core's own bookkeeping, never the
mailbox); PREPARE (``draft_reply``/``draft_new``/``edit_draft``/``read_draft``) writes a
local, reversible ``mail_drafts`` row, always read back to the owner in Turkish; EXTERNAL
MUTATION (``send``) is the one method that may ever call a real ``MailSender``, and only
after ``app.actions.confirmation_gate.check_gate`` says yes. No delete, no move, no mass
action exists here (ADR-0084 decision 2) — there is no method for any of them.

With no provider configured, every method answers ``account_missing`` honestly (spec §2)
— the production answer until the owner puts a real account on the host (owner item).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

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
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_MAIL_DISCARDED,
    EVENT_TYPE_MAIL_DRAFTED,
    EVENT_TYPE_MAIL_READ,
    EVENT_TYPE_MAIL_SENT,
    SUBSYSTEM_MAIL,
)
from app.logging import get_logger
from app.mail.models import (
    DRAFT_KIND_NEW,
    DRAFT_KIND_REPLY,
    DRAFT_STATE_DISCARDED,
    DRAFT_STATE_PREPARED,
    DRAFT_STATE_READ_BACK,
    DRAFT_STATE_SENDING,
    DRAFT_STATE_SENT,
    MailDraftRow,
    MailIndexRow,
)
from app.mail.providers import (
    DraftInput,
    MailMessage,
    MailProvider,
    MailSender,
    is_valid_email_address,
)
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_DRAFT, FOCUS_KIND_MESSAGE, FOCUS_KIND_THREAD
from app.uistate import UiState
from app.uistate import publish as publish_ui_state
from app.voice.intents import contains_secret_reference

logger = get_logger("app.mail.service")

SPEECH_ACCOUNT_MISSING = "Tanımlı bir posta hesabı yok efendim."
SPEECH_NO_MESSAGE = "Hangi maili?"
SPEECH_NO_DRAFT = "Önce bir taslak hazırlamam gerekiyor efendim."
SPEECH_NOT_FOUND = "Aradığınızı bulamadım efendim."
SPEECH_SECRET_REFUSED = "Şifreleri mailleyemem efendim."
ERROR_SECRET_REFUSED = "secret_refused"
#: L1, ADR-0084 addendum 2: a provider-side send failure (a bad recipient the stdlib
#: refused, a connection drop, ...) reverts the row to ``read_back`` rather than leaving
#: it stuck ``sending`` — this is the typed receipt that names it, never a stuck draft
#: with no receipt at all.
ERROR_SEND_FAILED = "send_failed"
#: L1: a recipient that is not a plausible address (a folded/injected header's own
#: remnant; a bare display name with no ``@``) is refused when the draft is CREATED —
#: never discovered only when the real sender raises.
ERROR_INVALID_RECIPIENT = "invalid_recipient"
SPEECH_INVALID_RECIPIENT = "Bu alıcı adresi geçerli görünmüyor efendim."

_GATE_SPEECH: dict[str, str] = {
    GATE_ACCOUNT_MISSING: SPEECH_ACCOUNT_MISSING,
    GATE_NOT_READ_BACK: "Önce taslağı okumam gerekiyor efendim.",
    GATE_NO_CONFIRMATION: "Önce taslağı okumam gerekiyor efendim.",
    GATE_CONFIRMATION_NOT_OWNER: "Önce taslağı okumam gerekiyor efendim.",
    GATE_ALREADY_SENT: "Bu taslak zaten gönderilmiş efendim.",
    GATE_SEND_DISABLED: "Mail gönderme şu anda kapalı efendim.",
}


def confirmed_by_label(confirmation: Confirmation) -> str:
    """``"rest:<session>"`` / ``"voice:<session>:<turn>"`` — the exact literal ADR-0084
    addendum 2 names, stamped on the row at the moment a confirmation actually succeeds."""
    if confirmation.source == CONFIRM_SOURCE_REST:
        return f"{CONFIRM_SOURCE_REST}:{confirmation.session_id}"
    return f"{CONFIRM_SOURCE_VOICE}:{confirmation.session_id}:{confirmation.turn}"


def _now() -> datetime:
    return datetime.now(UTC)


def _fmt_date(dt: datetime | None) -> str:
    if dt is None:
        return ""
    local = dt.astimezone(UTC)
    return local.strftime("%d %B %Y")


def _draft_dict(row: MailDraftRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "to": list(row.to_json or []),
        "cc": list(row.cc_json or []),
        "subject": row.subject,
        "body": row.body,
        "in_reply_to": row.in_reply_to,
        "state": row.state,
        "read_back_at": row.read_back_at.isoformat() if row.read_back_at else None,
        "read_back_session_id": row.read_back_session_id,
        "read_back_turn": row.read_back_turn,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "confirmed_by": row.confirmed_by,
        "last_error": row.last_error,
        "sent_message_id": row.sent_message_id,
    }


def _draft_speech(row: MailDraftRow) -> str:
    to = ", ".join(row.to_json or []) or "?"
    return f"Taslak: Kime: {to}. Konu: {row.subject}. Mesaj: {row.body} Göndermemi ister misiniz?"


class MailService:
    def __init__(self, provider: MailProvider | None, sender: MailSender | None) -> None:
        self._provider = provider
        self._sender = sender
        #: Whether an ACCOUNT is configured at all — independent of whether sending is
        #: turned on. ``SmtpMailSender`` refuses to even be constructed when the host flag
        #: is off (spec §2), so ``self._sender`` alone cannot tell "no account" from
        #: "account exists, sending is off": that distinction is exactly ``account_missing``
        #: vs ``send_disabled`` in ``app.actions.confirmation_gate``, and it is decided
        #: from the READ provider's presence, never from the sender object.
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
            record_receipt(db, receipt, SUBSYSTEM_MAIL)
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
                    subsystem=SUBSYSTEM_MAIL,
                    action=action,
                    factual_summary=summary,
                    occurred_at=_now(),
                    detail_json=detail,
                    source="live",
                    source_ref=f"{action}:{uuid.uuid4()}",
                ),
            )
        except Exception:  # noqa: BLE001 - evidence, never a dependency of the action
            logger.warning("mail_ledger_failed", action=action)

    def _publish(
        self,
        *,
        folder: str | None = None,
        subject: str | None = None,
        draft_state: str | None = None,
    ) -> None:
        metadata: dict[str, Any] = {}
        if folder:
            metadata["folder"] = folder[:64]
        if subject:
            metadata["subject"] = subject[:64]
        if draft_state:
            metadata["draft_state"] = draft_state[:32]
        publish_ui_state(
            UiState.MAIL_ACTIVITY,
            subsystem=SUBSYSTEM_MAIL,
            label=(subject or folder or "mail")[:64],
            metadata=metadata,
        )

    def _index_upsert(self, db: Session, message: MailMessage, *, now: datetime) -> MailIndexRow:
        row = (
            db.execute(
                select(MailIndexRow).where(MailIndexRow.provider_message_id == message.message_id)
            )
            .scalars()
            .first()
        )
        if row is None:
            row = MailIndexRow(id=uuid.uuid4(), provider_message_id=message.message_id)
            db.add(row)
        row.folder = message.folder
        row.from_name = message.from_name
        row.from_email = message.from_email
        row.to_json = list(message.to)
        row.subject = message.subject
        row.date = message.date
        row.snippet = message.snippet
        row.has_attachments = message.has_attachments
        row.thread_key = message.thread_key
        row.unread = message.unread
        row.last_used_at = now
        db.commit()
        db.refresh(row)
        return row

    # ------------------------------------------------------------------- READ

    def _unparseable_count(self) -> int:
        """M1 (security review): how many messages the provider's LAST read call
        skipped because they failed to parse — 0 for every provider that does not track
        this (the fixture-backed fakes, which never fail to parse their own fixture)."""
        return int(getattr(self._provider, "last_unparseable_count", 0) or 0)

    def _unparseable_suffix(self, count: int) -> str:
        return f" {count} ileti okunamadı." if count else ""

    def inbox_summary(
        self, db: Session, *, folder: str = "INBOX", session_id: str | None = None
    ) -> dict[str, Any]:
        if self._provider is None:
            return self._account_missing(capability="mail.inbox", session_id=session_id, db=db)
        messages = self._provider.list_messages(folder, limit=50)
        unparseable = self._unparseable_count()
        now = _now()
        for m in messages:
            self._index_upsert(db, m, now=now)
        unread = [m for m in messages if m.unread]
        self._ledger(
            db,
            event_type=EVENT_TYPE_MAIL_READ,
            action="mail.inbox",
            summary=f"mail.inbox -> {folder} ({len(unread)} okunmamış)",
            detail={"folder": folder, "count": len(messages), "unread": len(unread)},
        )
        self._publish(folder=folder)
        if not unread:
            speech = "Okunmamış mailiniz yok efendim."
        else:
            speech = f"{folder} klasöründe {len(unread)} okunmamış mailiniz var efendim."
        speech += self._unparseable_suffix(unparseable)
        return self._receipt(
            capability="mail.inbox",
            requested_state="read",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"folder": folder, "count": len(messages), "unread": len(unread)},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={
                "folder": folder,
                "count": len(messages),
                "unread": len(unread),
                "unparseable": unparseable,
                "messages": [m.as_summary() for m in messages],
            },
        )

    def search(self, db: Session, query: str, *, session_id: str | None = None) -> dict[str, Any]:
        if self._provider is None:
            return self._account_missing(capability="mail.search", session_id=session_id, db=db)
        results = self._provider.search(query, limit=50)
        unparseable = self._unparseable_count()
        now = _now()
        for m in results:
            self._index_upsert(db, m, now=now)
        self._ledger(
            db,
            event_type=EVENT_TYPE_MAIL_READ,
            action="mail.search",
            summary=f"mail.search -> {len(results)} sonuç",
            detail={"query": query[:200], "count": len(results)},
        )
        if not results:
            speech = SPEECH_NOT_FOUND
        elif len(results) == 1:
            m = results[0]
            speech = (
                f"{m.from_name or m.from_email}'dan '{m.subject}' konulu bir mail buldum efendim."
            )
        else:
            speech = (
                f"{len(results)} mail buldum efendim: "
                + ", ".join(m.subject for m in results[:5])
                + "."
            )
        speech += self._unparseable_suffix(unparseable)
        return self._receipt(
            capability="mail.search",
            requested_state="searched",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"count": len(results), "unparseable": unparseable},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"messages": [m.as_summary() for m in results]},
        )

    def _resolve_message(
        self, db: Session, target: str
    ) -> tuple[MailMessage | None, dict[str, Any] | None]:
        target = (target or "current").strip()
        if self._provider is None:
            return None, {"clarification": SPEECH_ACCOUNT_MISSING}
        if target in ("", "current"):
            entry = focus_module.current(db, FOCUS_KIND_MESSAGE)
            if entry is None:
                return None, {"clarification": SPEECH_NO_MESSAGE}
            return self._provider.get_message(entry.object_id), None
        if target == "previous":
            entry = focus_module.previous(db, FOCUS_KIND_MESSAGE)
            if entry is None:
                return None, {"clarification": SPEECH_NO_MESSAGE}
            return self._provider.get_message(entry.object_id), None
        # A spoken name/sender ("Ali'den gelen son maili"): search and pick the latest.
        found = self._provider.search(target, limit=50)
        if not found:
            return None, {"clarification": SPEECH_NOT_FOUND}
        found = sorted(
            found, key=lambda m: m.date or datetime.min.replace(tzinfo=UTC), reverse=True
        )
        return found[0], None

    def read(
        self, db: Session, *, target: str = "current", session_id: str | None = None
    ) -> dict[str, Any]:
        if self._provider is None:
            return self._account_missing(capability="mail.read", session_id=session_id, db=db)
        message, clar = self._resolve_message(db, target)
        if clar is not None:
            return {
                "status": "needs_clarification",
                "speech": clar["clarification"],
                "candidates": [],
            }
        assert message is not None
        now = _now()
        self._index_upsert(db, message, now=now)
        focus_module.set_focus(
            db,
            FOCUS_KIND_MESSAGE,
            message.message_id,
            label=message.subject,
            source="mail_read",
            now=now,
        )
        self._ledger(
            db,
            event_type=EVENT_TYPE_MAIL_READ,
            action="mail.read",
            summary=f"mail.read -> {message.subject}",
            detail={"message_id": message.message_id, "folder": message.folder},
        )
        self._publish(folder=message.folder, subject=message.subject)
        who = message.from_name or message.from_email
        speech = (
            f"{who}'dan, {_fmt_date(message.date)} tarihli, '{message.subject}' konulu "
            f"mail: {message.body_text}"
        )
        return self._receipt(
            capability="mail.read",
            requested_state="read",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"message_id": message.message_id},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"message": message.as_full()},
        )

    def thread(
        self, db: Session, *, target: str = "current", session_id: str | None = None
    ) -> dict[str, Any]:
        if self._provider is None:
            return self._account_missing(capability="mail.thread", session_id=session_id, db=db)
        message, clar = self._resolve_message(db, target)
        if clar is not None:
            return {
                "status": "needs_clarification",
                "speech": clar["clarification"],
                "candidates": [],
            }
        assert message is not None
        messages = self._provider.thread(message.message_id)
        unparseable = self._unparseable_count()
        now = _now()
        for m in messages:
            self._index_upsert(db, m, now=now)
        if messages:
            focus_module.set_focus(
                db,
                FOCUS_KIND_THREAD,
                messages[0].thread_key,
                label=messages[0].thread_key,
                source="mail_thread",
                now=now,
            )
        self._ledger(
            db,
            event_type=EVENT_TYPE_MAIL_READ,
            action="mail.thread",
            summary=f"mail.thread -> {len(messages)} mesaj",
            detail={"thread_key": message.thread_key, "count": len(messages)},
        )
        self._publish(folder=message.folder, subject=message.thread_key)
        parts = [f"{m.from_name or m.from_email}: {m.body_text}" for m in messages]
        speech = f"'{message.thread_key}' konuşması, {len(messages)} mesaj. " + " ".join(parts)
        speech += self._unparseable_suffix(unparseable)
        return self._receipt(
            capability="mail.thread",
            requested_state="read",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"count": len(messages), "unparseable": unparseable},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"messages": [m.as_full() for m in messages]},
        )

    # ------------------------------------------------------------------ PREPARE

    def _upsert_draft_focus(self, db: Session, row: MailDraftRow, *, now: datetime) -> None:
        focus_module.set_focus(
            db, FOCUS_KIND_DRAFT, str(row.id), label=row.subject, source="mail_draft", now=now
        )

    def _secret_refused(
        self, db: Session, *, capability: str, session_id: str | None
    ) -> dict[str, Any]:
        return self._receipt(
            capability=capability,
            requested_state="prepared",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_SECRET_REFUSED},
            speech=SPEECH_SECRET_REFUSED,
            db=db,
            error_class=ERROR_SECRET_REFUSED,
            session_id=session_id,
        )

    def _invalid_recipient(
        self, db: Session, *, capability: str, session_id: str | None
    ) -> dict[str, Any]:
        """L1: refused at DRAFT-creation time, before anything is ever assembled toward
        a real sender — never discovered only when ``smtplib`` raises."""
        return self._receipt(
            capability=capability,
            requested_state="prepared",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_INVALID_RECIPIENT},
            speech=SPEECH_INVALID_RECIPIENT,
            db=db,
            error_class=ERROR_INVALID_RECIPIENT,
            session_id=session_id,
        )

    def draft_reply(
        self, db: Session, *, body: str, target: str = "current", session_id: str | None = None
    ) -> dict[str, Any]:
        if contains_secret_reference(body):
            return self._secret_refused(db, capability="mail.draft", session_id=session_id)
        if self._provider is None:
            return self._account_missing(capability="mail.draft", session_id=session_id, db=db)
        message, clar = self._resolve_message(db, target)
        if clar is not None:
            return {
                "status": "needs_clarification",
                "speech": clar["clarification"],
                "candidates": [],
            }
        assert message is not None
        if not is_valid_email_address(message.from_email):
            return self._invalid_recipient(db, capability="mail.draft", session_id=session_id)
        now = _now()
        subject = message.subject
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        references = list(message.references) + [message.message_id]
        row = MailDraftRow(
            id=uuid.uuid4(),
            kind=DRAFT_KIND_REPLY,
            to_json=[message.from_email],
            cc_json=[],
            subject=subject,
            body=body,
            in_reply_to=message.message_id,
            state=DRAFT_STATE_PREPARED,
            read_back_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        # References is not its own column (spec §3's exact column list) — recomputed at
        # send time from the original message's own chain, never stored twice.
        self._upsert_draft_focus(db, row, now=now)
        self._ledger(
            db,
            event_type=EVENT_TYPE_MAIL_DRAFTED,
            action="mail.draft",
            summary=f"mail.draft -> {subject}",
            detail={"draft_id": str(row.id), "kind": "reply"},
        )
        self._publish(subject=subject, draft_state=row.state)
        return self._receipt(
            capability="mail.draft",
            requested_state="prepared",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"draft_id": str(row.id)},
            speech=_draft_speech(row),
            db=db,
            session_id=session_id,
            extra={"draft": _draft_dict(row), "references": references},
        )

    def draft_new(
        self, db: Session, *, to: str, subject: str, body: str, session_id: str | None = None
    ) -> dict[str, Any]:
        if contains_secret_reference(body) or contains_secret_reference(subject):
            return self._secret_refused(db, capability="mail.draft", session_id=session_id)
        if self._provider is None:
            return self._account_missing(capability="mail.draft", session_id=session_id, db=db)
        if not is_valid_email_address(to):
            return self._invalid_recipient(db, capability="mail.draft", session_id=session_id)
        now = _now()
        row = MailDraftRow(
            id=uuid.uuid4(),
            kind=DRAFT_KIND_NEW,
            to_json=[to],
            cc_json=[],
            subject=subject,
            body=body,
            in_reply_to=None,
            state=DRAFT_STATE_PREPARED,
            read_back_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        self._upsert_draft_focus(db, row, now=now)
        self._ledger(
            db,
            event_type=EVENT_TYPE_MAIL_DRAFTED,
            action="mail.draft",
            summary=f"mail.draft -> {subject}",
            detail={"draft_id": str(row.id), "kind": "new"},
        )
        self._publish(subject=subject, draft_state=row.state)
        return self._receipt(
            capability="mail.draft",
            requested_state="prepared",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"draft_id": str(row.id)},
            speech=_draft_speech(row),
            db=db,
            session_id=session_id,
            extra={"draft": _draft_dict(row)},
        )

    def _current_draft(self, db: Session) -> MailDraftRow | None:
        entry = focus_module.current(db, FOCUS_KIND_DRAFT)
        if entry is None:
            return None
        return db.get(MailDraftRow, uuid.UUID(entry.object_id))

    def edit_draft(
        self,
        db: Session,
        *,
        subject: str | None = None,
        body: str | None = None,
        to: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if contains_secret_reference(body or "") or contains_secret_reference(subject or ""):
            return self._secret_refused(db, capability="mail.edit_draft", session_id=session_id)
        if to is not None and not is_valid_email_address(to):
            return self._invalid_recipient(db, capability="mail.edit_draft", session_id=session_id)
        row = self._current_draft(db)
        if row is None or row.state not in (DRAFT_STATE_PREPARED, DRAFT_STATE_READ_BACK):
            return {"status": "needs_clarification", "speech": SPEECH_NO_DRAFT, "candidates": []}
        if subject is not None:
            row.subject = subject
        if body is not None:
            row.body = body
        if to is not None:
            row.to_json = [to]
        now = _now()
        # An edit changes the content the owner heard, so any EARLIER read-back is
        # invalidated HERE (module docstring; H1, ADR-0084 addendum 2): the row drops
        # back to ``prepared`` and a fresh, explicit read-back act is required before any
        # confirmation can bind to it again — never silently re-stamped as already read
        # back, which is exactly the vacuous-gate bug the security review found.
        row.state = DRAFT_STATE_PREPARED
        row.read_back_at = None
        row.read_back_session_id = None
        row.read_back_turn = None
        row.updated_at = now
        db.commit()
        db.refresh(row)
        self._upsert_draft_focus(db, row, now=now)
        self._ledger(
            db,
            event_type=EVENT_TYPE_MAIL_DRAFTED,
            action="mail.edit_draft",
            summary=f"mail.edit_draft -> {row.subject}",
            detail={"draft_id": str(row.id)},
        )
        self._publish(subject=row.subject, draft_state=row.state)
        return self._receipt(
            capability="mail.edit_draft",
            requested_state="prepared",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"draft_id": str(row.id)},
            speech=_draft_speech(row),
            db=db,
            session_id=session_id,
            extra={"draft": _draft_dict(row)},
        )

    def read_draft(
        self,
        db: Session,
        *,
        session_id: str | None = None,
        turn: int | None = None,
    ) -> dict[str, Any]:
        """The EXPLICIT read-back act (module docstring, H1): this is the ONE place
        ``read_back_at``/``read_back_session_id``/``read_back_turn`` are set on a draft
        that is not already gone (sent/discarded) — never at prepare time. ``session_id``/
        ``turn`` bind the read-back to the exact voice turn it happened on, so a LATER
        confirmation can be checked against the SAME session and a STRICTLY LATER turn
        (``app.actions.confirmation_gate``) rather than trusting a bare clock reading."""
        row = self._current_draft(db)
        if row is None or row.state not in (DRAFT_STATE_PREPARED, DRAFT_STATE_READ_BACK):
            return {"status": "needs_clarification", "speech": SPEECH_NO_DRAFT, "candidates": []}
        now = _now()
        row.state = DRAFT_STATE_READ_BACK
        row.read_back_at = now
        row.read_back_session_id = session_id
        row.read_back_turn = turn
        db.commit()
        db.refresh(row)
        self._ledger(
            db,
            event_type=EVENT_TYPE_MAIL_DRAFTED,
            action="mail.read_draft",
            summary=f"mail.read_draft -> {row.subject}",
            detail={"draft_id": str(row.id)},
        )
        self._publish(subject=row.subject, draft_state=row.state)
        return self._receipt(
            capability="mail.read_draft",
            requested_state="read",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"draft_id": str(row.id)},
            speech=_draft_speech(row),
            db=db,
            session_id=session_id,
            extra={"draft": _draft_dict(row)},
        )

    # ---------------------------------------------------------- EXTERNAL MUTATION

    def send(
        self,
        db: Session,
        *,
        draft_id: str | None = None,
        host_flag_enabled: bool,
        session_id: str | None = None,
        confirmation: Confirmation | None = None,
    ) -> dict[str, Any]:
        """EXTERNAL MUTATION (spec §1). ``confirmation`` is how THIS call claims to be
        the owner's word (module docstring; ``app.mail.routes.confirm_draft`` builds a
        REST one, ``app.voice.realtime_sessions.tools_mail.mail_send`` a voice one) — the
        gate (H1) judges the claim, never trusts it. Once the gate says yes, the state
        transition ``read_back`` -> ``sending`` is an atomic compare-and-swap (H2): two
        concurrent confirmations both reaching this point race on ONE UPDATE, and only the
        winner's ``rowcount`` is 1 — the loser gets the same ``already_sent`` receipt a
        genuinely-already-sent draft gets, and the real sender is called at most once no
        matter how many callers got past the gate check above at the same instant.
        """
        row = db.get(MailDraftRow, uuid.UUID(draft_id)) if draft_id else self._current_draft(db)
        if row is None:
            return {"status": "needs_clarification", "speech": "Neyi göndereyim?", "candidates": []}
        result = check_gate(
            state=row.state,
            prepared_state=DRAFT_STATE_PREPARED,
            read_back_state=DRAFT_STATE_READ_BACK,
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
                event_type=EVENT_TYPE_MAIL_SENT,
                action="mail.send",
                summary=f"mail.send refused ({result.reason})",
                detail={"draft_id": str(row.id), "reason": result.reason},
            )
            return self._receipt(
                capability="mail.send",
                requested_state="sent",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"draft_id": str(row.id), "reason": result.reason},
                speech=_GATE_SPEECH[result.reason],
                db=db,
                session_id=session_id,
                error_class=result.reason,
            )
        if self._sender is None:
            # The gate said yes (an account exists AND the host flag is on) but the
            # wiring never built a live sender — a configuration inconsistency, never a
            # guess at having sent anything.
            return self._receipt(
                capability="mail.send",
                requested_state="sent",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"draft_id": str(row.id), "reason": GATE_SEND_DISABLED},
                speech=_GATE_SPEECH[GATE_SEND_DISABLED],
                db=db,
                session_id=session_id,
                error_class=GATE_SEND_DISABLED,
            )
        assert confirmation is not None  # the gate above already required one to pass
        now = _now()
        confirmed_by = confirmed_by_label(confirmation)
        cas = db.execute(
            sa_update(MailDraftRow)
            .where(MailDraftRow.id == row.id, MailDraftRow.state == DRAFT_STATE_READ_BACK)
            .values(
                state=DRAFT_STATE_SENDING,
                confirmed_at=now,
                confirmed_by=confirmed_by,
                updated_at=now,
            )
        )
        db.commit()
        if cas.rowcount != 1:
            # H2: lost the race - a concurrent confirmation (or a replay of this very one)
            # already moved the row past read_back between the check above and this
            # UPDATE. The real sender is never touched; the caller gets the SAME receipt
            # a genuinely-already-sent draft gets, never a second send.
            self._ledger(
                db,
                event_type=EVENT_TYPE_MAIL_SENT,
                action="mail.send",
                summary="mail.send refused (already_sent, lost the race)",
                detail={"draft_id": str(row.id), "reason": GATE_ALREADY_SENT},
            )
            return self._receipt(
                capability="mail.send",
                requested_state="sent",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"draft_id": str(row.id), "reason": GATE_ALREADY_SENT},
                speech=_GATE_SPEECH[GATE_ALREADY_SENT],
                db=db,
                session_id=session_id,
                error_class=GATE_ALREADY_SENT,
            )
        db.refresh(row)
        draft_input = DraftInput(
            kind=row.kind,
            to=tuple(row.to_json or []),
            cc=tuple(row.cc_json or []),
            subject=row.subject,
            body=row.body,
            in_reply_to=row.in_reply_to,
            references=tuple(),
        )
        try:
            sent_message_id = self._sender.send(draft_input)
        except Exception as exc:  # noqa: BLE001 - L1: a provider failure reverts, never crashes
            failed_at = _now()
            db.execute(
                sa_update(MailDraftRow)
                .where(MailDraftRow.id == row.id)
                .values(
                    state=DRAFT_STATE_READ_BACK,
                    last_error=type(exc).__name__,
                    updated_at=failed_at,
                )
            )
            db.commit()
            db.refresh(row)
            logger.warning("mail_send_failed", draft_id=str(row.id), error_class=type(exc).__name__)
            self._ledger(
                db,
                event_type=EVENT_TYPE_MAIL_SENT,
                action="mail.send",
                summary=f"mail.send failed ({type(exc).__name__})",
                detail={"draft_id": str(row.id), "error_class": type(exc).__name__},
            )
            return self._receipt(
                capability="mail.send",
                requested_state="sent",
                execution=EXECUTION_FAILED,
                terminal=TERMINAL_FAILED,
                server={"draft_id": str(row.id), "reason": ERROR_SEND_FAILED},
                speech="Maili gönderemedim efendim; taslak duruyor.",
                db=db,
                session_id=session_id,
                error_class=ERROR_SEND_FAILED,
                extra={"draft": _draft_dict(row)},
            )
        row.state = DRAFT_STATE_SENT
        row.sent_message_id = sent_message_id
        row.updated_at = now
        db.commit()
        db.refresh(row)
        self._ledger(
            db,
            event_type=EVENT_TYPE_MAIL_SENT,
            action="mail.send",
            summary=f"mail.send -> {row.subject}",
            detail={"draft_id": str(row.id), "sent_message_id": sent_message_id},
        )
        self._publish(subject=row.subject, draft_state=row.state)
        return self._receipt(
            capability="mail.send",
            requested_state="sent",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"draft_id": str(row.id), "sent_message_id": sent_message_id},
            speech="Maili gönderdim efendim.",
            db=db,
            session_id=session_id,
            extra={"draft": _draft_dict(row)},
        )

    def discard(
        self, db: Session, *, draft_id: str | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        row = db.get(MailDraftRow, uuid.UUID(draft_id)) if draft_id else self._current_draft(db)
        if row is None:
            return {"status": "needs_clarification", "speech": SPEECH_NO_DRAFT, "candidates": []}
        now = _now()
        row.state = DRAFT_STATE_DISCARDED
        row.updated_at = now
        db.commit()
        db.refresh(row)
        self._ledger(
            db,
            event_type=EVENT_TYPE_MAIL_DISCARDED,
            action="mail.discard",
            summary=f"mail.discard -> {row.subject}",
            detail={"draft_id": str(row.id)},
        )
        self._publish(subject=row.subject, draft_state=row.state)
        return self._receipt(
            capability="mail.discard",
            requested_state="discarded",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"draft_id": str(row.id)},
            speech="Taslağı sildim efendim.",
            db=db,
            session_id=session_id,
            extra={"draft": _draft_dict(row)},
        )


__all__ = [
    "ERROR_INVALID_RECIPIENT",
    "ERROR_SEND_FAILED",
    "MailService",
    "SPEECH_ACCOUNT_MISSING",
    "SPEECH_INVALID_RECIPIENT",
    "SPEECH_NO_DRAFT",
    "SPEECH_NO_MESSAGE",
    "confirmed_by_label",
]
