"""Unit tests: /v1/mail/drafts/* and /v1/calendar/proposals/* — the Cockpit's approval
pair (docs/M21_MAIL_CALENDAR_SPEC.md §3, ADR-0084). Owner-gated, and ``confirm`` runs
the SAME confirmation gate the voice tool uses.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.confirmation_gate import CONFIRM_SOURCE_REST, GATE_NOT_READ_BACK
from app.calendar.models import (
    PROPOSAL_STATE_PREPARED,
    PROPOSAL_STATE_READ_BACK,
    CalendarProposalRow,
)
from app.calendar.service import CalendarService
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.mail.models import DRAFT_STATE_PREPARED, DRAFT_STATE_READ_BACK, MailDraftRow
from app.mail.service import MailService
from app.main import create_app
from app.operator.models import ObjectFocusRow
from tests.identity_support import authenticate, install_identity
from tests.mail_calendar_support import build_fake_calendar_provider, build_fake_mail_provider

ALL_TABLES = [
    MailDraftRow.__table__,
    CalendarProposalRow.__table__,
    ObjectFocusRow.__table__,
    ActivityEventRow.__table__,
]


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ALL_TABLES:
        table.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def app_and_client(engine, factory):
    from app.artifacts.runtime import ArtifactRuntime

    settings = Settings(_env_file=None, mail_send_enabled=True, calendar_write_enabled=True)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = factory
    app.state.artifacts = artifacts
    app.state.mail_service = MailService(build_fake_mail_provider(), _FakeSender())
    app.state.calendar_service = CalendarService(build_fake_calendar_provider(), _FakeWriter())
    return app, TestClient(app)


class _FakeSender:
    def __init__(self) -> None:
        self.sent: list[object] = []

    def send(self, draft) -> str:
        self.sent.append(draft)
        return "<fake-sent@fixture.example>"


class _FakeWriter:
    def __init__(self) -> None:
        self.created: list[object] = []

    def create(self, proposal) -> str:
        self.created.append(proposal)
        return "fake-event@fixture.example"

    def update(self, event_uid, changes) -> str:
        self.created.append(changes)
        return event_uid


@pytest.fixture()
def client(app_and_client) -> TestClient:
    app, test_client = app_and_client
    authenticate(app, test_client, settings=Settings(_env_file=None))
    return test_client


def _prepared_draft(factory, *, read_back_at, state: str = DRAFT_STATE_PREPARED) -> str:
    now = datetime.now(UTC)
    with factory() as db:
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
            read_back_session_id=f"{CONFIRM_SOURCE_REST}:owner" if read_back_at else None,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        return str(row.id)


def _prepared_proposal(factory, *, read_back_at, state: str = PROPOSAL_STATE_PREPARED) -> str:
    now = datetime.now(UTC)
    with factory() as db:
        row = CalendarProposalRow(
            id=uuid.uuid4(),
            kind="create",
            event_uid=None,
            summary="Kontrol",
            start=now + timedelta(hours=2),
            end=now + timedelta(hours=3),
            location=None,
            conflicts_json=[],
            state=state,
            read_back_at=read_back_at,
            read_back_session_id=f"{CONFIRM_SOURCE_REST}:owner" if read_back_at else None,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        return str(row.id)


# ------------------------------------------------------------------------- gating


def test_mail_and_calendar_routes_require_an_owner_session(app_and_client) -> None:
    _, test_client = app_and_client
    draft_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    assert test_client.get("/v1/mail/drafts/pending").status_code == 401
    assert test_client.post(f"/v1/mail/drafts/{draft_id}/confirm").status_code == 401
    assert test_client.post(f"/v1/mail/drafts/{draft_id}/discard").status_code == 401
    assert test_client.get("/v1/calendar/proposals/pending").status_code == 401
    assert test_client.post(f"/v1/calendar/proposals/{proposal_id}/confirm").status_code == 401
    assert test_client.post(f"/v1/calendar/proposals/{proposal_id}/discard").status_code == 401


# ------------------------------------------------------------------------------- mail


def test_pending_drafts_lists_only_prepared_ones(client, factory) -> None:
    read_back = datetime.now(UTC) - timedelta(seconds=1)
    draft_id = _prepared_draft(factory, read_back_at=read_back)
    response = client.get("/v1/mail/drafts/pending")
    assert response.status_code == 200
    ids = {d["id"] for d in response.json()["drafts"]}
    assert draft_id in ids


def test_confirm_runs_the_same_gate_and_refuses_without_a_read_back(client, factory) -> None:
    draft_id = _prepared_draft(factory, read_back_at=None)
    response = client.post(f"/v1/mail/drafts/{draft_id}/confirm")
    assert response.status_code == 200
    body = response.json()
    assert body["execution_status"] == "refused"
    assert body["error_class"] == GATE_NOT_READ_BACK


def test_confirm_sends_exactly_once_through_the_fake_sender(
    client, factory, app_and_client
) -> None:
    app, _ = app_and_client
    read_back = datetime.now(UTC) - timedelta(seconds=1)
    draft_id = _prepared_draft(factory, read_back_at=read_back, state=DRAFT_STATE_READ_BACK)
    response = client.post(f"/v1/mail/drafts/{draft_id}/confirm")
    assert response.status_code == 200
    assert response.json()["execution_status"] == "executed"
    assert len(app.state.mail_service._sender.sent) == 1  # type: ignore[attr-defined]

    second = client.post(f"/v1/mail/drafts/{draft_id}/confirm")
    assert second.json()["execution_status"] == "refused"
    assert len(app.state.mail_service._sender.sent) == 1  # type: ignore[attr-defined]


def test_discard_marks_the_draft_discarded(client, factory) -> None:
    draft_id = _prepared_draft(factory, read_back_at=datetime.now(UTC))
    response = client.post(f"/v1/mail/drafts/{draft_id}/discard")
    assert response.status_code == 200
    assert response.json()["draft"]["state"] == "discarded"


def test_confirm_unknown_draft_is_404(client) -> None:
    response = client.post(f"/v1/mail/drafts/{uuid.uuid4()}/confirm")
    assert response.status_code == 404


def test_pending_listing_is_the_read_back_and_a_confirm_then_succeeds(
    client, factory, app_and_client
) -> None:
    """ADR-0084 addendum 2: a draft that was never voice-read-back can still be
    confirmed through the Cockpit, because THIS surface's own listing IS the read-back
    (module docstring) — never a stuck ``not_read_back`` for a draft the owner is
    literally looking at on screen."""
    app, _ = app_and_client
    draft_id = _prepared_draft(factory, read_back_at=None, state=DRAFT_STATE_PREPARED)
    pending = client.get("/v1/mail/drafts/pending").json()["drafts"]
    row = next(d for d in pending if d["id"] == draft_id)
    assert row["state"] == "read_back"
    assert row["read_back_at"] is not None

    response = client.post(f"/v1/mail/drafts/{draft_id}/confirm")
    assert response.json()["execution_status"] == "executed"
    assert len(app.state.mail_service._sender.sent) == 1  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- calendar


def test_pending_proposals_lists_only_prepared_ones(client, factory) -> None:
    read_back = datetime.now(UTC) - timedelta(seconds=1)
    proposal_id = _prepared_proposal(factory, read_back_at=read_back)
    response = client.get("/v1/calendar/proposals/pending")
    assert response.status_code == 200
    ids = {p["id"] for p in response.json()["proposals"]}
    assert proposal_id in ids


def test_calendar_confirm_runs_the_same_gate(client, factory) -> None:
    proposal_id = _prepared_proposal(factory, read_back_at=None)
    response = client.post(f"/v1/calendar/proposals/{proposal_id}/confirm")
    assert response.status_code == 200
    assert response.json()["error_class"] == GATE_NOT_READ_BACK


def test_calendar_confirm_commits_exactly_once(client, factory, app_and_client) -> None:
    app, _ = app_and_client
    read_back = datetime.now(UTC) - timedelta(seconds=1)
    proposal_id = _prepared_proposal(
        factory, read_back_at=read_back, state=PROPOSAL_STATE_READ_BACK
    )
    response = client.post(f"/v1/calendar/proposals/{proposal_id}/confirm")
    assert response.status_code == 200
    assert response.json()["execution_status"] == "executed"
    assert len(app.state.calendar_service._writer.created) == 1  # type: ignore[attr-defined]

    second = client.post(f"/v1/calendar/proposals/{proposal_id}/confirm")
    assert second.json()["execution_status"] == "refused"
    assert len(app.state.calendar_service._writer.created) == 1  # type: ignore[attr-defined]


def test_calendar_discard_marks_the_proposal_discarded(client, factory) -> None:
    proposal_id = _prepared_proposal(factory, read_back_at=datetime.now(UTC))
    response = client.post(f"/v1/calendar/proposals/{proposal_id}/discard")
    assert response.status_code == 200
    assert response.json()["proposal"]["state"] == "discarded"


def test_calendar_confirm_unknown_proposal_is_404(client) -> None:
    response = client.post(f"/v1/calendar/proposals/{uuid.uuid4()}/confirm")
    assert response.status_code == 404


def test_pending_proposal_listing_is_the_read_back_and_a_confirm_then_succeeds(
    client, factory, app_and_client
) -> None:
    """The calendar side of ``test_pending_listing_is_the_read_back_and_a_confirm_then_
    succeeds`` above."""
    app, _ = app_and_client
    proposal_id = _prepared_proposal(factory, read_back_at=None, state=PROPOSAL_STATE_PREPARED)
    pending = client.get("/v1/calendar/proposals/pending").json()["proposals"]
    row = next(p for p in pending if p["id"] == proposal_id)
    assert row["state"] == "read_back"
    assert row["read_back_at"] is not None

    response = client.post(f"/v1/calendar/proposals/{proposal_id}/confirm")
    assert response.json()["execution_status"] == "executed"
    assert len(app.state.calendar_service._writer.created) == 1  # type: ignore[attr-defined]
