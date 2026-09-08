"""``app.executive.activities`` (spec §3): the ONE activity's idempotency, verification
and run-progress mechanics — the DB semantics that must hold regardless of which real
service a step kind happens to call. Individual family services (DocumentService,
MailService, SceneService, ...) already have their own extensive test suites elsewhere
in this codebase; what THIS file proves is specific to the executive layer: never
verified over an empty comparison, a mutating kind never double-creates on retry, and a
run's own progress/state is a pure function of its steps.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.executive import activities
from app.executive.models import (
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_PARTIAL,
    STATE_RUNNING,
    STEP_STATE_COMPENSATED,
    STEP_STATE_FAILED,
    STEP_STATE_PENDING,
    STEP_STATE_VERIFIED,
    ExecutiveRunRow,
    ExecutiveStepRow,
)
from app.executive.spec import (
    COMPENSATION_DISCARD_DRAFT,
    COMPENSATION_NONE,
    EVIDENCE_ARTIFACT_ID,
    EVIDENCE_DOCUMENT_REFS,
    EVIDENCE_DRAFT_ID,
    EVIDENCE_TEXT,
    RISK_MUTATE_EXTERNAL,
    RISK_MUTATE_LOCAL,
    RISK_READ,
    STEP_KIND_ARTIFACTS_CREATE,
    STEP_KIND_DOCUMENTS_FIND,
    STEP_KIND_MAIL_DRAFT,
    STEP_KIND_SYNTHESIS,
)
from app.mail.models import DRAFT_STATE_DISCARDED, DRAFT_STATE_PREPARED, MailDraftRow
from app.object_store import InMemoryObjectStore
from app.uistate.contract import UI_STATES
from app.uistate.publisher import UiStatePublisher, set_publisher
from tests.alarms_support import FakeDeviceAction
from tests.documents_support import document_capability_results

TABLES = [ExecutiveRunRow.__table__, ExecutiveStepRow.__table__, MailDraftRow.__table__]


@pytest.fixture()
def db_factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(db_factory):
    session = db_factory()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _patch_seams(monkeypatch, db_factory):
    """The activity module's own seams (module docstring of activities.py) —
    redirected at a single in-memory SQLite DB per test and a fake device, the same
    substitution ``app.research.activities.get_provider`` is designed for."""
    store = InMemoryObjectStore()
    monkeypatch.setattr(activities, "build_artifact_context", lambda _settings: (db_factory, store))
    device = FakeDeviceAction(results=document_capability_results())
    monkeypatch.setattr(activities, "get_device_action", lambda: device)
    publisher = UiStatePublisher()
    set_publisher(publisher)
    yield device
    set_publisher(UiStatePublisher())


def _make_run_and_step(
    db,
    *,
    kind: str,
    postcondition_evidence: str,
    risk_class: str,
    compensation: str = COMPENSATION_NONE,
    evidence_min: int | None = None,
) -> tuple[uuid.UUID, str]:
    now = datetime.now(UTC)
    run = ExecutiveRunRow(
        id=uuid.uuid4(),
        goal="test",
        graph_json={},
        state=STATE_RUNNING,
        steps_total=1,
        steps_done=0,
        source="voice",
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    db.flush()
    step = ExecutiveStepRow(
        id=uuid.uuid4(),
        run_id=run.id,
        step_id="s1",
        kind=kind,
        inputs_json={},
        precondition_json={},
        postcondition_json={"evidence": postcondition_evidence, "min": evidence_min},
        retry_json={"max_attempts": 1, "backoff_s": 1.0, "only_on": []},
        timeout_s=60,
        risk_class=risk_class,
        compensation=compensation,
        state=STEP_STATE_PENDING,
        attempt=0,
        created_at=now,
        updated_at=now,
    )
    db.add(step)
    db.commit()
    return run.id, step.step_id


# --------------------------------------------------------------- pure functions


def test_evidence_meets_minimum_document_refs_empty_is_not_verified() -> None:
    """Never verified over an empty comparison (module docstring's central rule)."""
    assert not activities._evidence_meets_minimum(
        EVIDENCE_DOCUMENT_REFS, {"document_refs": []}, None
    )


def test_evidence_meets_minimum_document_refs_with_explicit_zero_min_is_verified() -> None:
    """A step whose postcondition explicitly allows zero (spec's mail-thread shape:
    documents.find may legitimately find nothing) IS verified over an empty list."""
    assert activities._evidence_meets_minimum(EVIDENCE_DOCUMENT_REFS, {"document_refs": []}, 0)


def test_evidence_meets_minimum_text_empty_string_is_not_verified() -> None:
    assert not activities._evidence_meets_minimum(EVIDENCE_TEXT, {"text": "   "}, None)


def test_evidence_meets_minimum_text_nonempty_is_verified() -> None:
    assert activities._evidence_meets_minimum(EVIDENCE_TEXT, {"text": "hello"}, None)


def test_evidence_meets_minimum_artifact_id_absent_is_not_verified() -> None:
    assert not activities._evidence_meets_minimum(EVIDENCE_ARTIFACT_ID, {}, None)


def test_evidence_meets_minimum_artifact_id_present_is_verified() -> None:
    assert activities._evidence_meets_minimum(EVIDENCE_ARTIFACT_ID, {"artifact_id": "x"}, None)


def test_derive_run_outcome_all_verified_is_completed() -> None:
    steps = [
        ExecutiveStepRow(
            step_id="s1",
            kind=STEP_KIND_SYNTHESIS,
            state=STEP_STATE_VERIFIED,
            evidence_json={"text": "done"},
        )
    ]
    state, reasons, text = activities._derive_run_outcome(steps)
    assert state == STATE_COMPLETED
    assert reasons == {}
    assert text == "done"


def test_derive_run_outcome_mixed_is_partial() -> None:
    steps = [
        ExecutiveStepRow(
            step_id="s1",
            kind=STEP_KIND_DOCUMENTS_FIND,
            state=STEP_STATE_FAILED,
            error_class="not_found",
        ),
        ExecutiveStepRow(
            step_id="s2",
            kind=STEP_KIND_SYNTHESIS,
            state=STEP_STATE_VERIFIED,
            evidence_json={"text": "partial"},
        ),
    ]
    state, reasons, _text = activities._derive_run_outcome(steps)
    assert state == STATE_PARTIAL
    assert reasons == {"s1": "not_found"}


def test_derive_run_outcome_all_failed_is_failed() -> None:
    steps = [
        ExecutiveStepRow(
            step_id="s1",
            kind=STEP_KIND_DOCUMENTS_FIND,
            state=STEP_STATE_FAILED,
            error_class="not_found",
        )
    ]
    state, _reasons, _text = activities._derive_run_outcome(steps)
    assert state == STATE_FAILED


def test_derive_run_outcome_still_running_while_a_step_is_pending() -> None:
    steps = [
        ExecutiveStepRow(step_id="s1", kind=STEP_KIND_DOCUMENTS_FIND, state=STEP_STATE_PENDING)
    ]
    state, _reasons, _text = activities._derive_run_outcome(steps)
    assert state == STATE_RUNNING


# --------------------------------------------------------- the ONE activity, run_step


async def _run(run_id: uuid.UUID, step_id: str) -> dict:
    return await activities.run_step_activity(str(run_id), step_id)


@pytest.mark.asyncio
async def test_documents_find_verifies_and_publishes(db, _patch_seams) -> None:
    run_id, step_id = _make_run_and_step(
        db,
        kind=STEP_KIND_DOCUMENTS_FIND,
        postcondition_evidence=EVIDENCE_DOCUMENT_REFS,
        risk_class=RISK_READ,
    )
    result = await _run(run_id, step_id)
    assert result["state"] == STEP_STATE_VERIFIED
    assert result["evidence"]["document_refs"]

    fresh_run = db.get(ExecutiveRunRow, run_id)
    db.refresh(fresh_run)
    assert fresh_run.state == STATE_COMPLETED  # the only step verified -> the run completes
    assert fresh_run.steps_done == 1


@pytest.mark.asyncio
async def test_a_step_publishes_executive_run_on_the_ui_state_bus(db) -> None:
    from app.uistate.publisher import get_publisher

    run_id, step_id = _make_run_and_step(
        db,
        kind=STEP_KIND_DOCUMENTS_FIND,
        postcondition_evidence=EVIDENCE_DOCUMENT_REFS,
        risk_class=RISK_READ,
    )
    await _run(run_id, step_id)
    tail = get_publisher().tail()
    assert tail, "no executive.run event was published"
    event = tail[-1]
    assert event.state.value == "executive.run"
    assert event.state.value in UI_STATES
    assert event.metadata["state"] == STATE_COMPLETED
    assert event.metadata["done"] == 1
    assert event.metadata["total"] == 1
    assert "run" in event.metadata  # the short token, never the full uuid
    assert str(run_id) not in str(event.metadata["run"])


@pytest.mark.asyncio
async def test_dependency_unavailable_from_missing_upstream_is_failed_recoverable_when_retryable(
    db,
) -> None:
    """artifacts.create with no upstream content (module docstring's own case) —
    classified dependency_unavailable; failed_recoverable exactly when the step's own
    retry.only_on allows it, per spec §1/§3."""
    now = datetime.now(UTC)
    run = ExecutiveRunRow(
        id=uuid.uuid4(),
        goal="t",
        graph_json={},
        state=STATE_RUNNING,
        steps_total=1,
        steps_done=0,
        source="voice",
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    db.flush()
    step = ExecutiveStepRow(
        id=uuid.uuid4(),
        run_id=run.id,
        step_id="s1",
        kind=STEP_KIND_ARTIFACTS_CREATE,
        inputs_json={"kind": "document", "source": {}},
        precondition_json={},
        postcondition_json={"evidence": EVIDENCE_ARTIFACT_ID},
        retry_json={"max_attempts": 2, "backoff_s": 1.0, "only_on": ["dependency_unavailable"]},
        timeout_s=60,
        risk_class=RISK_MUTATE_LOCAL,
        compensation="delete_render",
        state=STEP_STATE_PENDING,
        attempt=0,
        created_at=now,
        updated_at=now,
    )
    db.add(step)
    db.commit()

    result = await _run(run.id, "s1")
    assert result["error_class"] == "dependency_unavailable"
    assert result["state"] == "failed_recoverable"  # retryable per this step's own only_on


@pytest.mark.asyncio
async def test_replaying_a_verified_step_returns_cached_evidence_without_recalling_the_service(
    db, _patch_seams
) -> None:
    device = _patch_seams
    run_id, step_id = _make_run_and_step(
        db,
        kind=STEP_KIND_DOCUMENTS_FIND,
        postcondition_evidence=EVIDENCE_DOCUMENT_REFS,
        risk_class=RISK_READ,
    )
    first = await _run(run_id, step_id)
    calls_after_first = len(device.calls)
    assert calls_after_first > 0

    second = await _run(run_id, step_id)
    assert second["evidence"] == first["evidence"]
    assert len(device.calls) == calls_after_first, "a verified step must not call the device again"


# ------------------------------------------------------------------- idempotent create


@pytest.mark.asyncio
async def test_mail_draft_with_prior_evidence_reuses_the_row_and_only_reverifies(db) -> None:
    """A crash between 'created' and 'verified' (module docstring): the SAME draft row
    is reused on the next attempt, never a second draft."""
    now = datetime.now(UTC)
    draft = MailDraftRow(
        id=uuid.uuid4(),
        kind="reply",
        to_json=["a@example.com"],
        cc_json=[],
        subject="Re: x",
        body="body",
        state=DRAFT_STATE_PREPARED,
        created_at=now,
        updated_at=now,
    )
    db.add(draft)
    db.commit()

    run_id, step_id = _make_run_and_step(
        db,
        kind=STEP_KIND_MAIL_DRAFT,
        postcondition_evidence=EVIDENCE_DRAFT_ID,
        risk_class=RISK_MUTATE_EXTERNAL,
        compensation=COMPENSATION_DISCARD_DRAFT,
    )
    # Simulate a prior attempt that created the draft but crashed before verifying.
    step_row = db.get(
        ExecutiveStepRow,
        db.query(ExecutiveStepRow).filter_by(run_id=run_id, step_id=step_id).first().id,
    )
    step_row.evidence_json = {"draft_id": str(draft.id)}
    db.commit()

    result = await _run(run_id, step_id)
    assert result["state"] == STEP_STATE_VERIFIED
    assert result["evidence"]["draft_id"] == str(draft.id)
    # exactly one MailDraftRow exists — never a second one created on retry
    assert db.query(MailDraftRow).count() == 1


# ---------------------------------------------------------------------- cancellation


def test_cancel_run_and_compensate_discards_a_verified_draft_and_cancels_the_rest(db) -> None:
    now = datetime.now(UTC)
    draft = MailDraftRow(
        id=uuid.uuid4(),
        kind="reply",
        to_json=["a@example.com"],
        cc_json=[],
        subject="Re: x",
        body="body",
        state=DRAFT_STATE_PREPARED,
        created_at=now,
        updated_at=now,
    )
    db.add(draft)
    run_id, step_id = _make_run_and_step(
        db,
        kind=STEP_KIND_MAIL_DRAFT,
        postcondition_evidence=EVIDENCE_DRAFT_ID,
        risk_class=RISK_MUTATE_EXTERNAL,
        compensation=COMPENSATION_DISCARD_DRAFT,
    )
    step_row = db.query(ExecutiveStepRow).filter_by(run_id=run_id, step_id=step_id).one()
    step_row.state = STEP_STATE_VERIFIED
    step_row.evidence_json = {"draft_id": str(draft.id)}
    run = db.get(ExecutiveRunRow, run_id)
    run.steps_total = 2
    pending = ExecutiveStepRow(
        id=uuid.uuid4(),
        run_id=run_id,
        step_id="s2",
        kind=STEP_KIND_SYNTHESIS,
        inputs_json={},
        precondition_json={},
        postcondition_json={"evidence": EVIDENCE_TEXT},
        retry_json={},
        timeout_s=60,
        risk_class=RISK_READ,
        compensation=COMPENSATION_NONE,
        state=STEP_STATE_PENDING,
        attempt=0,
        created_at=now,
        updated_at=now,
    )
    db.add(pending)
    db.commit()

    result = activities.cancel_run_and_compensate(run_id)
    assert result["state"] == STATE_CANCELLED

    db.expire_all()
    fresh_draft = db.get(MailDraftRow, draft.id)
    assert fresh_draft.state == DRAFT_STATE_DISCARDED, "nothing of the owner's is left dangling"
    fresh_step1 = db.query(ExecutiveStepRow).filter_by(run_id=run_id, step_id="s1").one()
    assert fresh_step1.state == STEP_STATE_COMPENSATED
    fresh_step2 = db.query(ExecutiveStepRow).filter_by(run_id=run_id, step_id="s2").one()
    assert fresh_step2.state == "cancelled"
    fresh_run = db.get(ExecutiveRunRow, run_id)
    assert fresh_run.state == STATE_CANCELLED


def test_cancel_run_and_compensate_is_idempotent(db) -> None:
    run_id, _step_id = _make_run_and_step(
        db, kind=STEP_KIND_SYNTHESIS, postcondition_evidence=EVIDENCE_TEXT, risk_class=RISK_READ
    )
    first = activities.cancel_run_and_compensate(run_id)
    assert first["already_settled"] is False
    second = activities.cancel_run_and_compensate(run_id)
    assert second["already_settled"] is True
