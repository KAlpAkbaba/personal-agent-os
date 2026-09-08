"""``app.executive.service`` — the sync DB halves every caller (REST, voice) shares:
the <= 2 active-run bound, the receipt sentences, and every ``ExecutiveServiceError``
precondition. The async signal halves are exercised by ``test_executive_workflow.py``
(the Temporal side) and the voice corpus (the full relay); what matters here is that a
refusal is refused for the RIGHT reason before any signal is ever attempted.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.executive import service as executive_service
from app.executive.models import (
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_PARTIAL,
    STATE_PAUSED,
    STATE_PLANNED,
    STATE_RUNNING,
    STEP_STATE_FAILED,
    STEP_STATE_PENDING,
    STEP_STATE_VERIFIED,
    ExecutiveRunRow,
    ExecutiveStepRow,
)
from app.executive.service import ExecutiveServiceError
from app.executive.spec import MAX_ACTIVE_RUNS, STEP_KIND_ARTIFACTS_CREATE
from app.uistate.publisher import UiStatePublisher, set_publisher

TABLES = [ExecutiveRunRow.__table__, ExecutiveStepRow.__table__]
_RESEARCH_DIRECTIVE = (
    "Son üç gündeki AI gelişmelerini araştır, bana etkisini çıkar, Word raporu ve sunum hazırla."
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _publisher():
    set_publisher(UiStatePublisher())
    yield
    set_publisher(UiStatePublisher())


def _start(db, directive: str = _RESEARCH_DIRECTIVE) -> ExecutiveRunRow:
    return executive_service.start_run_db(db, directive=directive, source="voice", session_id="s1")


# ------------------------------------------------------------------------------- start


def test_start_run_transitions_straight_to_running(db) -> None:
    run = _start(db)
    assert run.state == STATE_RUNNING
    assert (
        run.steps_total == 5
    )  # research shape: run, synthesize, document, presentation, synthesis
    steps = executive_service.list_steps(db, run.id)
    assert len(steps) == run.steps_total
    assert all(s.state == STEP_STATE_PENDING for s in steps)


def test_start_run_beyond_the_active_bound_is_refused(db) -> None:
    for _ in range(MAX_ACTIVE_RUNS):
        _start(db)
    with pytest.raises(ExecutiveServiceError) as exc_info:
        _start(db)
    assert exc_info.value.error_class == "too_many_active_runs"


def test_start_run_after_one_completes_is_accepted_again(db) -> None:
    """The bound counts ACTIVE runs, never the total ever started."""
    run_a = _start(db)
    for _ in range(MAX_ACTIVE_RUNS - 1):
        _start(db)
    run_a.state = STATE_COMPLETED
    db.commit()
    _start(db)  # must not raise — one of the MAX_ACTIVE_RUNS slots freed up


def test_start_run_with_unrecognised_directive_raises_clarification(db) -> None:
    with pytest.raises(ExecutiveServiceError) as exc_info:
        executive_service.start_run_db(db, directive="Bana yardım eder misin?", source="voice")
    assert exc_info.value.error_class == "clarification_needed"


# ------------------------------------------------------------------------------ status


def test_status_speech_names_the_progress(db) -> None:
    run = _start(db)
    status = executive_service.get_status(db, run.id)
    assert status["state"] == STATE_RUNNING
    assert "0/5" in status["speech"]


def test_status_on_unknown_run_is_not_found(db) -> None:
    with pytest.raises(ExecutiveServiceError) as exc_info:
        executive_service.get_status(db, uuid.uuid4())
    assert exc_info.value.error_class == "not_found"


def test_partial_status_speech_names_every_unverified_step_and_why(db) -> None:
    run = _start(db)
    run.state = STATE_PARTIAL
    run.partial_reasons_json = {"s3": "not_found", "s4": "dependency_unavailable"}
    run.synthesis_text = "Rapor hazır, sunum yazılamadı."
    db.commit()
    status = executive_service.get_status(db, run.id)
    assert "s3" in status["speech"]
    assert "not_found" in status["speech"]
    assert "Rapor hazır" in status["speech"]


def test_explain_names_the_current_step_kind(db) -> None:
    run = _start(db)
    step = executive_service.list_steps(db, run.id)[0]
    run.current_step = step.step_id
    db.commit()
    explain = executive_service.get_explain(db, run.id)
    from app.executive.spec import STEP_KIND_PROFILES

    assert STEP_KIND_PROFILES[step.kind].description in explain["speech"]
    assert explain["step_id"] == step.step_id


def test_explain_with_no_current_step_falls_back_to_status(db) -> None:
    run = _start(db)
    explain = executive_service.get_explain(db, run.id)
    assert "step_id" not in explain


# ------------------------------------------------------------------------ pause/resume


def test_pause_requires_running(db) -> None:
    run = _start(db)
    run.state = STATE_PLANNED
    db.commit()
    with pytest.raises(ExecutiveServiceError) as exc_info:
        executive_service.pause_run_db(db, run.id)
    assert exc_info.value.error_class == "invalid_state"


def test_pause_from_running_succeeds(db) -> None:
    run = _start(db)
    updated = executive_service.pause_run_db(db, run.id)
    assert updated.state == STATE_PAUSED


def test_resume_requires_paused(db) -> None:
    run = _start(db)  # running, never paused
    with pytest.raises(ExecutiveServiceError) as exc_info:
        executive_service.resume_run_db(db, run.id)
    assert exc_info.value.error_class == "invalid_state"


def test_resume_from_paused_succeeds(db) -> None:
    run = _start(db)
    executive_service.pause_run_db(db, run.id)
    updated = executive_service.resume_run_db(db, run.id)
    assert updated.state == STATE_RUNNING


# ---------------------------------------------------------------------------- cancel


def test_cancel_on_a_terminal_run_is_refused(db) -> None:
    run = _start(db)
    run.state = STATE_CANCELLED
    db.commit()
    with pytest.raises(ExecutiveServiceError) as exc_info:
        executive_service.cancel_run_validate(db, run.id)
    assert exc_info.value.error_class == "invalid_state"


def test_cancel_on_an_active_run_validates_cleanly(db) -> None:
    run = _start(db)
    executive_service.cancel_run_validate(db, run.id)  # must not raise


# ----------------------------------------------------------------------------- retry


def test_retry_on_unknown_step_is_not_found(db) -> None:
    run = _start(db)
    with pytest.raises(ExecutiveServiceError) as exc_info:
        executive_service.retry_step_validate(db, run.id, "s99")
    assert exc_info.value.error_class == "not_found"


def test_retry_on_a_pending_step_is_refused_with_an_honest_reason(db) -> None:
    """Never "zaten başarılı" (already succeeded) for a step that simply has not run
    yet — found and fixed via the corpus (exec.retry.ordinal)."""
    run = _start(db)
    step = executive_service.list_steps(db, run.id)[1]
    with pytest.raises(ExecutiveServiceError) as exc_info:
        executive_service.retry_step_validate(db, run.id, step.step_id)
    assert "başarılı" not in exc_info.value.speech


def test_retry_on_a_failed_step_validates_cleanly(db) -> None:
    run = _start(db)
    step = executive_service.list_steps(db, run.id)[1]
    step.state = STEP_STATE_FAILED
    db.commit()
    validated = executive_service.retry_step_validate(db, run.id, step.step_id)
    assert validated.step_id == step.step_id


def test_retry_on_a_verified_step_is_refused_as_already_successful(db) -> None:
    run = _start(db)
    step = executive_service.list_steps(db, run.id)[1]
    step.state = STEP_STATE_VERIFIED
    db.commit()
    with pytest.raises(ExecutiveServiceError) as exc_info:
        executive_service.retry_step_validate(db, run.id, step.step_id)
    assert "başarılı" in exc_info.value.speech


def test_retry_on_a_cancelled_run_is_refused(db) -> None:
    run = _start(db)
    step = executive_service.list_steps(db, run.id)[1]
    step.state = STEP_STATE_FAILED
    run.state = STATE_CANCELLED
    db.commit()
    with pytest.raises(ExecutiveServiceError) as exc_info:
        executive_service.retry_step_validate(db, run.id, step.step_id)
    assert exc_info.value.error_class == "invalid_state"


# ----------------------------------------------------------------------------- amend


def test_amend_appends_a_validated_step_and_persists_it(db) -> None:
    run = _start(db)
    new_step = {
        "id": "s6",
        "kind": STEP_KIND_ARTIFACTS_CREATE,
        "inputs": {"kind": "presentation", "source": "s2.text"},
        "precondition": {"check": "step_done", "arg": "s2"},
        "postcondition": {"evidence": "artifact_id"},
        "timeout_s": 120,
        "retry": {"max_attempts": 1, "backoff_s": 1.0, "only_on": []},
        "risk_class": "mutate_local",
        "compensation": "delete_render",
    }
    candidate = executive_service.amend_run_db(db, run.id, new_step)
    assert candidate.id == "s6"
    fresh_run = db.get(ExecutiveRunRow, run.id)
    assert fresh_run.steps_total == 6
    steps = executive_service.list_steps(db, run.id)
    assert any(s.step_id == "s6" for s in steps)


def test_amend_rejecting_an_invalid_step_never_persists_anything(db) -> None:
    run = _start(db)
    before = executive_service.list_steps(db, run.id)
    with pytest.raises(ExecutiveServiceError) as exc_info:
        executive_service.amend_run_db(db, run.id, {"id": "s6", "kind": "not.a.real.kind"})
    assert exc_info.value.error_class == "invalid_step"
    after = executive_service.list_steps(db, run.id)
    assert len(after) == len(before)


def test_amend_on_a_finished_run_is_refused(db) -> None:
    run = _start(db)
    run.state = STATE_CANCELLED
    db.commit()
    with pytest.raises(ExecutiveServiceError) as exc_info:
        executive_service.amend_run_db(db, run.id, {"id": "s6", "kind": STEP_KIND_ARTIFACTS_CREATE})
    assert exc_info.value.error_class == "invalid_state"


def test_amend_duplicate_step_id_is_refused(db) -> None:
    run = _start(db)
    existing = executive_service.list_steps(db, run.id)[0]
    with pytest.raises(ExecutiveServiceError):
        executive_service.amend_run_db(
            db,
            run.id,
            {
                "id": existing.step_id,
                "kind": STEP_KIND_ARTIFACTS_CREATE,
                "postcondition": {"evidence": "artifact_id"},
                "risk_class": "mutate_local",
                "compensation": "delete_render",
            },
        )
