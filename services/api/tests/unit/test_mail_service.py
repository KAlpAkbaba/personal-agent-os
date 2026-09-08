"""``MailService`` against the fixture mailbox and its computed oracle
(docs/M21_MAIL_CALENDAR_SPEC.md §1, §3, §4, ADR-0084): every truth.json entry, the gate's
five refusals, idempotent confirmation, and secrets absent from every receipt/ledger row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
)
from app.ledger.models import ActivityEventRow
from app.mail.models import (
    DRAFT_STATE_DISCARDED,
    DRAFT_STATE_PREPARED,
    DRAFT_STATE_READ_BACK,
    DRAFT_STATE_SENT,
    MailDraftRow,
    MailIndexRow,
)
from app.mail.providers import ImapMailProvider, SmtpMailSender
from app.mail.service import MailService
from app.operator.models import ObjectFocusRow
from tests.mail_calendar_support import (
    FakeImapServer,
    FakeSmtpServer,
    build_fake_mail_provider,
    build_fake_mail_sender,
    load_truth,
)

TRUTH = load_truth()["mail"]
SECRET_PASSWORD = "correct-horse-battery-staple-never-logged"  # noqa: S105 - test fixture


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        MailIndexRow.__table__,
        MailDraftRow.__table__,
        ObjectFocusRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _service() -> MailService:
    return MailService(build_fake_mail_provider(), build_fake_mail_sender())


# ------------------------------------------------------------------- account_missing


def test_every_method_answers_account_missing_with_no_provider(db) -> None:
    service = MailService(None, None)
    assert service.inbox_summary(db)["error_class"] == "account_missing"
    assert service.search(db, "fatura")["error_class"] == "account_missing"
    assert service.read(db)["error_class"] == "account_missing"
    assert service.thread(db)["error_class"] == "account_missing"
    assert service.draft_reply(db, body="x")["error_class"] == "account_missing"
    assert (
        service.draft_new(db, to="a@b.com", subject="s", body="x")["error_class"]
        == "account_missing"
    )
    for r in (
        service.inbox_summary(db)["speech"],
        service.read(db)["speech"],
    ):
        assert r == "Tanımlı bir posta hesabı yok efendim."


# ------------------------------------------------------------------------- truth.json


def test_inbox_summary_matches_the_oracle(db) -> None:
    service = _service()
    result = service.inbox_summary(db, folder="INBOX")
    assert result["execution_status"] == "executed"
    assert result["count"] == TRUTH["counts"]["INBOX"]
    assert result["unread"] == TRUTH["unread_inbox"]
    got_unread_uids = sorted(
        int(m["message_id"].split("@")[0].removeprefix("<m"))
        for m in result["messages"]
        if m["unread"]
    )
    assert got_unread_uids == sorted(TRUTH["unread_inbox_uids"])


def test_inbox_summary_matches_the_oracle_for_every_folder(db) -> None:
    service = _service()
    for folder in ("INBOX", "Gönderilmiş", "Arşiv"):
        result = service.inbox_summary(db, folder=folder)
        assert result["count"] == TRUTH["counts"][folder], folder


def test_search_fatura_matches_the_oracle(db) -> None:
    service = _service()
    result = service.search(db, "fatura")
    got_uids = [int(m["message_id"].split("@")[0].removeprefix("<m")) for m in result["messages"]]
    assert got_uids == TRUTH["search"]["fatura"]


def test_search_butce_matches_the_oracle(db) -> None:
    service = _service()
    result = service.search(db, "bütçe")
    got_uids = [int(m["message_id"].split("@")[0].removeprefix("<m")) for m in result["messages"]]
    assert got_uids == TRUTH["search"]["bütçe"]


def test_read_by_name_finds_the_latest_from_ali(db) -> None:
    service = _service()
    result = service.read(db, target="Ali")
    assert result["execution_status"] == "executed"
    message = result["message"]
    assert message["message_id"] == f"<m{TRUTH['latest_from_ali']['uid']}@fixture.example>"
    assert message["subject"] == TRUTH["latest_from_ali"]["subject"]


def test_thread_matches_the_oracle_order(db) -> None:
    service = _service()
    service.read(db, target="Ali")  # focuses message 104
    result = service.thread(db, target="current")
    got_uids = [int(m["message_id"].split("@")[0].removeprefix("<m")) for m in result["messages"]]
    assert got_uids == TRUTH["thread_proje_plani"]


def test_html_only_body_reduced_to_text_matches_the_oracle(db) -> None:
    service = _service()
    # "1.284,50" names only the original invoice's amount (uid 105, HTML-only body) — its
    # own reply (uid 106, "Faturayı aldım, teşekkürler.") never repeats the figure, so this
    # resolves to the ONE message whose html_to_text reduction is under test.
    full = service.read(db, target="1.284,50")
    assert TRUTH["html_only_body_contains"] in full["message"]["body_text"]
    assert full["message"]["message_id"] == "<m105@fixture.example>"


def test_draft_reply_composes_the_exact_reply_headers(db) -> None:
    service = _service()
    service.read(db, target="Ali")  # focuses the latest-from-Ali message (uid 104)
    result = service.draft_reply(db, body="Yarın 10'da uygunum.", target="current")
    assert result["execution_status"] == "executed"
    draft = result["draft"]
    want = TRUTH["reply_to_latest_ali"]
    assert draft["to"] == [want["to"]]
    assert draft["subject"] == want["subject"]
    assert draft["in_reply_to"] == want["in_reply_to"]
    assert result["references"] == want["references"]
    assert draft["state"] == DRAFT_STATE_PREPARED
    # H1 (ADR-0084 addendum 2): PREPARE never stamps a read-back on its own any more -
    # only the explicit `mail.read_draft` act does (proven below).
    assert draft["read_back_at"] is None


def test_read_draft_is_the_explicit_read_back_act(db) -> None:
    service = _service()
    service.read(db, target="Ali")
    prepared = service.draft_reply(db, body="Yarın 10'da uygunum.", target="current")
    assert prepared["draft"]["read_back_at"] is None
    read_back = service.read_draft(db, session_id="sess-1", turn=3)
    assert read_back["execution_status"] == "executed"
    draft = read_back["draft"]
    assert draft["state"] == DRAFT_STATE_READ_BACK
    assert draft["read_back_at"] is not None


# ---------------------------------------------------------------------- the gate


def _prepared_draft(
    db,
    *,
    state: str = DRAFT_STATE_PREPARED,
    read_back_at=None,
    read_back_session_id: str | None = None,
    read_back_turn: int | None = None,
) -> MailDraftRow:
    now = datetime.now(UTC)
    row = MailDraftRow(
        id=uuid.uuid4(),
        kind="new",
        to_json=["ayse.kaya@example.com"],
        cc_json=[],
        subject="Toplantı",
        body="Yarın gelemiyorum.",
        in_reply_to=None,
        state=state,
        read_back_at=read_back_at,
        read_back_session_id=read_back_session_id,
        read_back_turn=read_back_turn,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    from app.operator import focus as focus_module
    from app.operator.models import FOCUS_KIND_DRAFT

    focus_module.set_focus(db, FOCUS_KIND_DRAFT, str(row.id), label=row.subject, source="test")
    return row


def _voice_confirmation(*, session_id="sess-1", turn=2, owner_intent_ok=True) -> Confirmation:
    return Confirmation(
        source=CONFIRM_SOURCE_VOICE, session_id=session_id, turn=turn, owner_intent_ok=owner_intent_ok
    )


def test_send_refuses_not_read_back(db) -> None:
    service = _service()
    _prepared_draft(db, state=DRAFT_STATE_PREPARED, read_back_at=None)
    result = service.send(db, host_flag_enabled=True, confirmation=_voice_confirmation())
    assert result["execution_status"] == "refused"
    assert result["error_class"] == GATE_NOT_READ_BACK
    assert service._sender.sent == []  # type: ignore[attr-defined]


def test_send_refuses_no_confirmation_when_none_is_given(db) -> None:
    service = _service()
    _prepared_draft(
        db,
        state=DRAFT_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=1,
    )
    result = service.send(db, host_flag_enabled=True, confirmation=None)
    assert result["error_class"] == GATE_NO_CONFIRMATION
    assert service._sender.sent == []  # type: ignore[attr-defined]


def test_send_refuses_no_confirmation_when_the_turn_is_not_after_the_read_back(db) -> None:
    """ADR-0084 addendum 2 condition (c): the confirmation's own turn must be STRICTLY
    LATER than the read-back's — the same turn (or an earlier one) is refused."""
    service = _service()
    _prepared_draft(
        db,
        state=DRAFT_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=2,
    )
    result = service.send(
        db,
        host_flag_enabled=True,
        confirmation=_voice_confirmation(session_id="sess-1", turn=2, owner_intent_ok=True),
    )
    assert result["error_class"] == GATE_NO_CONFIRMATION
    assert service._sender.sent == []  # type: ignore[attr-defined]


def test_send_refuses_confirmation_not_owner_when_the_router_never_resolved_mail_send(db) -> None:
    """H1: the tool being CALLED is never proof of the owner's word - only the router
    having resolved THIS turn to MAIL_SEND is (a model-issued send with no owner turn)."""
    service = _service()
    _prepared_draft(
        db,
        state=DRAFT_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=1,
    )
    result = service.send(
        db,
        host_flag_enabled=True,
        confirmation=_voice_confirmation(session_id="sess-1", turn=2, owner_intent_ok=False),
    )
    assert result["error_class"] == GATE_CONFIRMATION_NOT_OWNER
    assert service._sender.sent == []  # type: ignore[attr-defined]


def test_send_refuses_not_read_back_when_the_confirmation_is_a_different_session(db) -> None:
    """A voice confirmation from a session that never heard THIS draft's read-back is
    refused the same way an unread draft is - never a guess that the owner remembers a
    different conversation."""
    service = _service()
    _prepared_draft(
        db,
        state=DRAFT_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-old",
        read_back_turn=1,
    )
    result = service.send(
        db,
        host_flag_enabled=True,
        confirmation=_voice_confirmation(session_id="sess-new", turn=1, owner_intent_ok=True),
    )
    assert result["error_class"] == GATE_NOT_READ_BACK
    assert service._sender.sent == []  # type: ignore[attr-defined]


def test_send_refuses_send_disabled(db) -> None:
    service = _service()
    _prepared_draft(
        db,
        state=DRAFT_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=1,
    )
    result = service.send(db, host_flag_enabled=False, confirmation=_voice_confirmation())
    assert result["error_class"] == GATE_SEND_DISABLED
    assert service._sender.sent == []  # type: ignore[attr-defined]


def test_send_refuses_account_missing(db) -> None:
    service = MailService(None, None)
    _prepared_draft(
        db,
        state=DRAFT_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=1,
    )
    result = service.send(db, host_flag_enabled=True, confirmation=_voice_confirmation())
    assert result["error_class"] == GATE_ACCOUNT_MISSING


def test_send_succeeds_exactly_once_and_a_second_confirmation_refuses(db) -> None:
    service = _service()
    _prepared_draft(
        db,
        state=DRAFT_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=1,
    )
    first = service.send(db, host_flag_enabled=True, confirmation=_voice_confirmation())
    assert first["execution_status"] == "executed"
    assert first["draft"]["state"] == DRAFT_STATE_SENT
    assert len(service._sender.sent) == 1  # type: ignore[attr-defined]

    second = service.send(
        db, host_flag_enabled=True, confirmation=_voice_confirmation(turn=3)
    )
    assert second["execution_status"] == "refused"
    assert second["error_class"] == GATE_ALREADY_SENT
    assert len(service._sender.sent) == 1  # type: ignore[attr-defined] # never sent twice


def test_rest_confirmation_needs_no_turn_and_succeeds_once(db) -> None:
    """The Cockpit's own authenticated act IS the confirmation on the REST surface -
    no turn to check (module docstring, ADR-0084 addendum 2)."""
    service = _service()
    _prepared_draft(
        db,
        state=DRAFT_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="rest:owner-session-1",
        read_back_turn=None,
    )
    confirmation = Confirmation(source=CONFIRM_SOURCE_REST, session_id="owner-session-1")
    result = service.send(db, host_flag_enabled=True, confirmation=confirmation)
    assert result["execution_status"] == "executed"
    assert result["draft"]["confirmed_by"] == "rest:owner-session-1"


def test_discard_prevents_a_later_send(db) -> None:
    service = _service()
    row = _prepared_draft(
        db,
        state=DRAFT_STATE_READ_BACK,
        read_back_at=datetime.now(UTC),
        read_back_session_id="sess-1",
        read_back_turn=1,
    )
    discarded = service.discard(db, draft_id=str(row.id))
    assert discarded["draft"]["state"] == DRAFT_STATE_DISCARDED
    later = service.send(
        db, draft_id=str(row.id), host_flag_enabled=True, confirmation=_voice_confirmation()
    )
    assert later["error_class"] == GATE_ALREADY_SENT
    assert service._sender.sent == []  # type: ignore[attr-defined]


def test_secret_reference_is_refused_before_any_draft_is_written(db) -> None:
    service = _service()
    result = service.draft_new(db, to="ali.yilmaz@example.com", subject="Şifre", body="Şifrem 1234")
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "secret_refused"
    assert db.execute(select(MailDraftRow)).scalars().first() is None


# --------------------------------------------------------- secrets never logged


def test_no_password_ever_reaches_a_receipt_or_ledger_row(db) -> None:
    """A REAL provider/sender (against the loopback fakes), exercised end to end, never
    leaves its own password anywhere a receipt or ledger row can echo it."""
    with FakeImapServer() as imap_server, FakeSmtpServer() as smtp_server:
        provider = ImapMailProvider(
            host="127.0.0.1",
            port=imap_server.port,
            username="owner",
            password=SECRET_PASSWORD,
            use_ssl=False,
        )
        sender = SmtpMailSender(
            host="127.0.0.1",
            port=smtp_server.port,
            username="owner",
            password=SECRET_PASSWORD,
            mail_from="alp@example.com",
            use_tls=False,
            enabled=True,
        )
        service = MailService(provider, sender)
        service.inbox_summary(db, folder="INBOX")
        service.read(db, target="current")
        draft = service.draft_new(db, to="ali.yilmaz@example.com", subject="Selam", body="Merhaba")
        draft_id = draft["draft"]["id"]
        service.read_draft(db, session_id="sess-1", turn=1)
        confirmation = Confirmation(
            source=CONFIRM_SOURCE_VOICE, session_id="sess-1", turn=2, owner_intent_ok=True
        )
        service.send(db, draft_id=draft_id, host_flag_enabled=True, confirmation=confirmation)

        rows = db.execute(select(ActivityEventRow)).scalars().all()
        assert len(rows) > 0
        for row in rows:
            blob = f"{row.factual_summary} {row.detail_json}"
            assert SECRET_PASSWORD not in blob
