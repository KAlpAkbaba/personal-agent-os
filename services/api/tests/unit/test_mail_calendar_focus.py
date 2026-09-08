"""M21's own kinds on the M19 durable object-focus stack
(docs/M21_MAIL_CALENDAR_SPEC.md §3, ADR-0084 decision 5): ``message``/``thread`` (a mail
item the owner read), ``draft``/``proposal`` (a PREPARE-tier row) and ``event`` (a
calendar occurrence) are additive kinds on the SAME ``app.operator.focus``/
``ObjectFocusRow`` table ``test_documents_focus.py`` already covers generically — this
file narrows to what is specific to the five new kinds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.calendar.models import CalendarProposalRow
from app.calendar.service import CalendarService
from app.ledger.models import ActivityEventRow
from app.mail.models import MailDraftRow, MailIndexRow
from app.mail.service import MailService
from app.operator import focus as focus_module
from app.operator.models import (
    FOCUS_KIND_DRAFT,
    FOCUS_KIND_EVENT,
    FOCUS_KIND_MESSAGE,
    FOCUS_KIND_PROPOSAL,
    FOCUS_KIND_THREAD,
    FOCUS_KINDS,
    ObjectFocusRow,
)
from tests.mail_calendar_support import build_fake_calendar_provider, build_fake_mail_provider


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        ObjectFocusRow.__table__,
        MailIndexRow.__table__,
        MailDraftRow.__table__,
        CalendarProposalRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def test_the_five_m21_kinds_are_known_focus_kinds() -> None:
    assert FOCUS_KIND_MESSAGE in FOCUS_KINDS
    assert FOCUS_KIND_THREAD in FOCUS_KINDS
    assert FOCUS_KIND_DRAFT in FOCUS_KINDS
    assert FOCUS_KIND_EVENT in FOCUS_KINDS
    assert FOCUS_KIND_PROPOSAL in FOCUS_KINDS


def test_message_and_draft_focus_are_independent_stacks(db) -> None:
    focus_module.set_focus(
        db, FOCUS_KIND_MESSAGE, "<m104@fixture.example>", label="Re: Proje planı", source="test"
    )
    focus_module.set_focus(db, FOCUS_KIND_DRAFT, "d-1", label="Toplantı", source="test")
    message_current = focus_module.current(db, FOCUS_KIND_MESSAGE)
    draft_current = focus_module.current(db, FOCUS_KIND_DRAFT)
    assert message_current is not None and message_current.object_id == "<m104@fixture.example>"
    assert draft_current is not None and draft_current.object_id == "d-1"
    assert focus_module.current(db, FOCUS_KIND_EVENT) is None


def test_mail_read_sets_the_message_focus() -> None:
    service = MailService(build_fake_mail_provider(), None)
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (ObjectFocusRow.__table__, MailIndexRow.__table__, ActivityEventRow.__table__):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        result = service.read(db, target="Ali")
        entry = focus_module.current(db, FOCUS_KIND_MESSAGE)
    assert entry is not None
    assert entry.object_id == result["message"]["message_id"]


def test_mail_thread_sets_the_thread_focus() -> None:
    service = MailService(build_fake_mail_provider(), None)
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (ObjectFocusRow.__table__, MailIndexRow.__table__, ActivityEventRow.__table__):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        service.read(db, target="Ali")
        service.thread(db, target="current")
        entry = focus_module.current(db, FOCUS_KIND_THREAD)
    assert entry is not None
    assert entry.object_id == "Proje planı"


def test_mail_draft_reply_sets_the_draft_focus() -> None:
    service = MailService(build_fake_mail_provider(), None)
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        ObjectFocusRow.__table__,
        MailIndexRow.__table__,
        MailDraftRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        service.read(db, target="Ali")
        result = service.draft_reply(db, body="Yarın 10'da uygunum.", target="current")
        entry = focus_module.current(db, FOCUS_KIND_DRAFT)
    assert entry is not None
    assert entry.object_id == result["draft"]["id"]


def test_calendar_propose_sets_the_proposal_focus() -> None:
    service = CalendarService(build_fake_calendar_provider(), None)
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        ObjectFocusRow.__table__,
        CalendarProposalRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)
    with factory() as db:
        result = service.propose(
            db, summary="Kontrol", start=now + timedelta(hours=2), end=now + timedelta(hours=3)
        )
        entry = focus_module.current(db, FOCUS_KIND_PROPOSAL)
    assert entry is not None
    assert entry.object_id == result["proposal"]["id"]


def test_event_focus_is_set_independently_for_a_reschedule(db) -> None:
    """"Bunu bir saat ertele." (spec §3): the owner's WORDS point at the currently
    focused EVENT, never a mail message or a draft — the same independent-stack
    discipline every other kind above already proves."""
    focus_module.set_focus(
        db, FOCUS_KIND_EVENT, "ev-dis@fixture.example", label="Diş hekimi", source="test"
    )
    entry = focus_module.current(db, FOCUS_KIND_EVENT)
    assert entry is not None
    assert entry.object_id == "ev-dis@fixture.example"
    assert focus_module.current(db, FOCUS_KIND_PROPOSAL) is None
