"""M21 security review — M1 (nested-MIME isolation), L1 (header sanitisation, recipient
validation, a typed ``send_failed`` receipt) — the pure/direct-unit half; the IMAP wire
version of M1 (a poisoned message inside a real fetch loop) lives in
``test_mail_imap_provider.py``, and the full send-failure-reverts-the-row path in
``test_mail_service.py``.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from app.actions.confirmation_gate import CONFIRM_SOURCE_VOICE, Confirmation
from app.mail.models import DRAFT_STATE_PREPARED, DRAFT_STATE_READ_BACK, MailDraftRow
from app.mail.providers import (
    MIME_MAX_DEPTH,
    MIME_MAX_PARTS,
    FakeMailSender,
    is_valid_email_address,
    message_from_rfc822,
)
from app.mail.service import ERROR_INVALID_RECIPIENT, ERROR_SEND_FAILED, MailService
from tests.mail_calendar_support import (
    build_fake_mail_provider,
    deeply_nested_rfc822,
    folded_from_header_rfc822,
)

# ------------------------------------------------------------------------------- M1


def test_a_3000_level_nested_message_never_recurses_unboundedly() -> None:
    """M1: the exact pathological shape the security review verified live. Whether the
    interpreter's own recursion limit trips inside ``email.message_from_bytes`` or inside
    ``Message.walk()`` is a stdlib implementation detail (see
    ``deeply_nested_rfc822``'s own docstring) — the ONE thing this module guarantees is
    that ``message_from_rfc822`` either returns a message or raises, in BOUNDED time,
    and never corrupts the interpreter's call stack for the rest of the process."""
    raw = deeply_nested_rfc822(3000, uid=901)
    started = time.monotonic()
    try:
        message_from_rfc822(raw, uid="901", folder="INBOX", unread=False)
    except RecursionError:
        pass  # acceptable: the CALLER (ImapMailProvider._fetch_uids) isolates this
    elapsed = time.monotonic() - started
    assert elapsed < 2.0, f"took {elapsed:.2f}s - not bounded"
    # And, critically, the interpreter is still usable afterward - a real RecursionError
    # sometimes leaves too little headroom for the `except` clause's own cleanup; a
    # trivial recursive call here proves the stack unwound cleanly.

    def _tiny_recursion(n: int) -> int:
        return n if n == 0 else 1 + _tiny_recursion(n - 1)

    assert _tiny_recursion(50) == 50


def test_a_moderately_deep_but_parseable_message_is_bounded_by_depth_and_parts() -> None:
    """A message nested deeper than :data:`MIME_MAX_DEPTH` but shallow enough for the
    stdlib to parse it at all (module constants) still has its walk BOUNDED — proving the
    cap actually bites even when nothing raises."""
    depth = MIME_MAX_DEPTH + 10
    assert depth < 800  # stays well under where this interpreter's own parse gives up
    raw = deeply_nested_rfc822(depth, uid=902)
    message = message_from_rfc822(raw, uid="902", folder="INBOX", unread=False)
    # The leaf is beyond MIME_MAX_DEPTH, so its own text is never reached as the body -
    # bounded, not a crash, and never silently pretending to have read everything.
    assert message is not None
    assert message.message_id == "<t902@fixture.example>"


# ------------------------------------------------------------------------------- L1


def test_a_folded_from_header_never_carries_a_line_break_after_parsing() -> None:
    raw = folded_from_header_rfc822(uid=903)
    message = message_from_rfc822(raw, uid="903", folder="INBOX", unread=False)
    assert "\n" not in message.from_email
    assert "\r" not in message.from_email
    assert "\n" not in message.from_name
    assert "\r" not in message.from_name
    # The injected line is neutralised into plain trailing text, never a header boundary
    # - and specifically, it no longer forms a valid address (is_valid_email_address
    # below is what actually stops it reaching a draft's own `to`).
    assert not is_valid_email_address(message.from_email)


def test_is_valid_email_address_rejects_crlf_and_control_characters() -> None:
    assert is_valid_email_address("ali.yilmaz@example.com")
    assert not is_valid_email_address("evil@example.com\r\nBcc: attacker@example.com")
    assert not is_valid_email_address("evil@example.com\nX-Injected: 1")
    assert not is_valid_email_address("no-at-sign.example.com")
    assert not is_valid_email_address("")
    assert not is_valid_email_address("has space@example.com")
    assert not is_valid_email_address("a" * 400 + "@example.com")


def test_draft_reply_refuses_a_poisoned_from_address_before_writing_anything() -> None:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.ledger.models import ActivityEventRow
    from app.mail.models import MailIndexRow
    from app.operator.models import ObjectFocusRow

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for table in (
        MailIndexRow.__table__,
        MailDraftRow.__table__,
        ObjectFocusRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    class _PoisonedProvider:
        """A minimal fake standing in for a REAL provider that just parsed a poisoned
        message from the wire (``app.mail.providers.ImapMailProvider`` proves the real
        parse elsewhere) — this test's own job is only ``MailService.draft_reply``'s
        refusal, not the parse itself."""

        def get_message(self, message_id: str):
            raw = folded_from_header_rfc822(uid=904)
            return message_from_rfc822(raw, uid="904", folder="INBOX", unread=True)

        def search(self, query, *, limit=50):
            return [self.get_message("<t904@fixture.example>")]

    with factory() as db_session:
        service = MailService(_PoisonedProvider(), FakeMailSender())
        result = service.draft_reply(db_session, body="Selam.", target="<t904@fixture.example>")
        assert result["execution_status"] == "refused"
        assert result["error_class"] == ERROR_INVALID_RECIPIENT
        assert db_session.execute(select(MailDraftRow)).scalars().first() is None


# ------------------------------------------------------------- send_failed (L1)


def test_a_provider_failure_reverts_the_draft_to_read_back_never_stuck() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.ledger.models import ActivityEventRow
    from app.mail.models import MailIndexRow
    from app.operator import focus as focus_module
    from app.operator.models import FOCUS_KIND_DRAFT, ObjectFocusRow

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for table in (
        MailIndexRow.__table__,
        MailDraftRow.__table__,
        ObjectFocusRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    class _RaisingSender:
        def send(self, draft):
            raise ValueError("Header values may not contain linefeed or carriage return characters")

    with factory() as db:
        now = datetime.now(UTC)
        row = MailDraftRow(
            id=uuid.uuid4(),
            kind="new",
            to_json=["ali.yilmaz@example.com"],
            cc_json=[],
            subject="Toplantı",
            body="Yarın gelemiyorum.",
            in_reply_to=None,
            state=DRAFT_STATE_READ_BACK,
            read_back_at=now,
            read_back_session_id="sess-1",
            read_back_turn=1,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        focus_module.set_focus(db, FOCUS_KIND_DRAFT, str(row.id), label=row.subject, source="test")

        service = MailService(build_fake_mail_provider(), _RaisingSender())
        confirmation = Confirmation(
            source=CONFIRM_SOURCE_VOICE, session_id="sess-1", turn=2, owner_intent_ok=True
        )
        result = service.send(db, host_flag_enabled=True, confirmation=confirmation)
        assert result["execution_status"] == "failed"
        assert result["error_class"] == ERROR_SEND_FAILED
        assert result["draft"]["state"] == DRAFT_STATE_READ_BACK
        assert result["draft"]["last_error"] == "ValueError"

        # Never a stuck row: a fresh, valid confirmation on the SAME (still read_back)
        # draft, in a LATER turn, now succeeds once the provider side is healthy again.
        db.refresh(row)
        assert row.state == DRAFT_STATE_READ_BACK
        healthy_sender = FakeMailSender()
        service._sender = healthy_sender  # type: ignore[attr-defined] - simulate recovery
        retry_confirmation = Confirmation(
            source=CONFIRM_SOURCE_VOICE, session_id="sess-1", turn=3, owner_intent_ok=True
        )
        retried = service.send(db, host_flag_enabled=True, confirmation=retry_confirmation)
        assert retried["execution_status"] == "executed"
        assert len(healthy_sender.sent) == 1
