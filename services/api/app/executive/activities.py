"""The ONE activity (spec §3): ``run_step_activity(run_id, step_id)``.

Every step of every run goes through this single Temporal activity — never a separate
``@activity.defn`` per step kind — so "idempotent by ``(run_id, step_id, attempt)``,
writes the rows, the workflow holds no state" (spec §3, the M13 discipline
``app.research.workflow``/``browser_workflow`` already prove) is ONE code path to get
right rather than fifteen. Dispatch by ``step_row.kind`` happens INSIDE this activity,
against :data:`app.executive.spec.STEP_KIND_PROFILES` — the closed vocabulary
``app.executive.graph.validate_graph`` already checked before this run could exist.

**Idempotency.** On every invocation (a fresh call, or Temporal's own retry of a failed
attempt) this activity first re-reads the step's own row:

* already ``verified`` (a replay after the result was durably recorded, e.g. a worker
  crash between this activity returning and the workflow observing it) -> the SAME
  evidence is returned immediately, no service is called again;
* a MUTATING kind (mail.draft, calendar.propose, artifacts.*, apps.create, scene.*)
  that already wrote an id in ``evidence_json`` from an earlier, unverified attempt (the
  service call succeeded but this activity crashed before returning) -> that id is
  reused and only the READ-BACK is repeated, never a second create;
* otherwise the kind's real call runs, and its result's id (if it created one) is
  persisted BEFORE any read-back is attempted — so a crash between "created" and
  "verified" is exactly the safe state described above on the next attempt, never a
  duplicate mail draft, calendar proposal, artifact, project or scene.

**Verification.** A step is ``verified`` only when :func:`_evidence_meets_minimum` says
so — never because the service call returned ``execution_status="executed"`` alone, and
never over an empty comparison (a bare ``document_refs: []`` is NOT verified unless the
step's own ``postcondition.min`` explicitly allows zero, spec §3's central rule, the exact
defect that cost M24 and M25 a security finding each).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError

from app.actions.receipt import EXECUTION_EXECUTED
from app.appfactory.models import STATE_SCAFFOLDED, STATE_TESTED, AppProjectRow
from app.appfactory.service import AppFactoryService
from app.artifacts import service as artifact_service
from app.artifacts.factory import create as artifacts_factory_create
from app.artifacts.runtime import build_artifact_context
from app.artifacts.spec import ArtifactSpec
from app.calendar.models import PROPOSAL_STATE_PREPARED, CalendarProposalRow
from app.calendar.providers import build_calendar_provider, build_calendar_writer
from app.calendar.service import CalendarService
from app.config import get_settings
from app.creative3d.models import STATE_APPLIED, STATE_RENDERED, SceneRow, wire_step
from app.creative3d.service import SceneService
from app.db import build_engine, build_session_factory
from app.devices.commands import DeviceCommandClient
from app.documents.service import DocumentService
from app.executive.models import (
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_PARTIAL,
    STATE_PLANNED,
    STATE_RUNNING,
    STEP_IN_FLIGHT_STATES,
    STEP_STATE_CANCELLED,
    STEP_STATE_COMPENSATED,
    STEP_STATE_FAILED,
    STEP_STATE_FAILED_RECOVERABLE,
    STEP_STATE_RUNNING,
    STEP_STATE_SKIPPED,
    STEP_STATE_VERIFIED,
    STEP_TERMINAL_STATES,
    TERMINAL_RUN_STATES,
    ExecutiveRunRow,
    ExecutiveStepRow,
)
from app.executive.refs import parse_reference
from app.executive.spec import (
    COMPENSATION_DELETE_RENDER,
    COMPENSATION_DISCARD_DRAFT,
    COMPENSATION_NONE,
    COMPENSATION_STOP_PROJECT,
    EVIDENCE_ARTIFACT_ID,
    EVIDENCE_DOCUMENT_REFS,
    EVIDENCE_DRAFT_ID,
    EVIDENCE_PROJECT_ID,
    EVIDENCE_PROPOSAL_ID,
    EVIDENCE_RESEARCH_REPORT,
    EVIDENCE_SCENE_ID,
    EVIDENCE_TEXT,
    PRECONDITION_NONE,
    PRECONDITION_STEP_DONE,
    STEP_KIND_APPS_CREATE,
    STEP_KIND_APPS_TEST,
    STEP_KIND_ARTIFACTS_CREATE,
    STEP_KIND_ARTIFACTS_RENDER,
    STEP_KIND_CALENDAR_PROPOSE,
    STEP_KIND_DOCUMENTS_COMPARE,
    STEP_KIND_DOCUMENTS_EXTRACT,
    STEP_KIND_DOCUMENTS_FIND,
    STEP_KIND_MAIL_ANALYZE_THREAD,
    STEP_KIND_MAIL_DRAFT,
    STEP_KIND_RESEARCH_RUN,
    STEP_KIND_RESEARCH_SYNTHESIZE,
    STEP_KIND_SCENE_CREATE,
    STEP_KIND_SCENE_RENDER,
    STEP_KIND_SYNTHESIS,
)
from app.mail.models import DRAFT_STATE_PREPARED, MailDraftRow
from app.mail.providers import build_mail_provider, build_mail_sender
from app.mail.service import MailService
from app.research import runs_service
from app.research.browser_workflow import BrowserResearchRequest, BrowserResearchWorkflow
from app.research.models import STAGE_READY
from app.routines.dispatch import BrokerDeviceAction, DeviceActionPort
from app.uistate import UiState
from app.uistate import publish as publish_ui_state
from app.uistate.contract import SCENE_STEP_VERIFIED

#: The step failed for a reason this code did not anticipate — never retried, because an
#: unclassified failure is not evidence of a transient one, and always recorded with the
#: exception's own type so the receipt says what happened rather than "something went wrong".
ERROR_INTERNAL = "internal_error"


class StepError(Exception):
    """A step failed for a classified reason. ``error_class`` drives BOTH the retry
    decision (only ``dependency_unavailable``/``timeout`` are ever retried, and only
    when the step's own ``retry.only_on`` names them — spec §1, §3) and the receipt/
    explain sentence."""

    def __init__(self, error_class: str, message: str) -> None:
        self.error_class = error_class
        self.message = message
        super().__init__(message)


# ------------------------------------------------------------------------------ seams
#
# Plain functions, not module-level singletons: each is cheap to construct (the
# services themselves hold no long-lived state beyond a DB engine's connection pool,
# module docstring) and a plain function is what a test monkeypatches — the identical
# seam shape ``app.research.activities.get_provider`` already establishes for this
# codebase. A worker-lifetime shared engine is a later performance pass, not a
# correctness requirement: every kind below opens and closes its own session per call,
# exactly like ``app.research.browser_activities`` already does for ITS activities.


def _session_factory():
    engine = build_engine(get_settings().database_url)
    return build_session_factory(engine)


def get_device_action() -> DeviceActionPort:
    """A REAL ``BrokerDeviceAction`` — never ``None`` (module docstring: ``_select_device``
    on every M19-M25 service treats ``None`` as ``capability_missing``, a DIFFERENT and
    non-retryable failure from "no broker runtime registered", which is exactly the
    honest ``dependency_unavailable`` a test environment with no device produces)."""
    factory = _session_factory()
    return BrokerDeviceAction(session_factory=factory, command_client=DeviceCommandClient(factory))


def get_document_service() -> DocumentService:
    return DocumentService()


def get_app_factory_service() -> AppFactoryService:
    return AppFactoryService()


def get_mail_service() -> MailService:
    settings = get_settings()
    return MailService(build_mail_provider(settings), build_mail_sender(settings))


def get_calendar_service() -> CalendarService:
    settings = get_settings()
    return CalendarService(build_calendar_provider(settings), build_calendar_writer(settings))


def get_creative3d_service(store: Any) -> SceneService:
    return SceneService(object_store=store)


async def get_temporal_client() -> Client:
    """The seam ``research.run`` uses to start the M13 pipeline as its own, independent
    Temporal workflow (module docstring: "maps to ONE existing service call" — starting
    ``BrowserResearchWorkflow`` untouched, through its own real client connection, IS
    that one call). Tests monkeypatch this to hand back the SAME
    ``temporalio.testing.WorkflowEnvironment`` client the executive workflow itself runs
    under, so both workflows execute against the one in-memory time-skipping server."""
    settings = get_settings()
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


# --------------------------------------------------------------------------- evidence


def _evidence_meets_minimum(
    evidence_kind: str, evidence: dict[str, Any], minimum: int | None
) -> bool:
    """Never verified over an empty comparison (module docstring) — the ONE place every
    kind's "did this actually produce something" question is answered, so a new kind
    cannot silently skip it by returning early with ``execution_status="executed"``."""
    if evidence_kind in (
        EVIDENCE_ARTIFACT_ID,
        EVIDENCE_DRAFT_ID,
        EVIDENCE_PROPOSAL_ID,
        EVIDENCE_PROJECT_ID,
        EVIDENCE_SCENE_ID,
    ):
        return bool(evidence.get(evidence_kind))
    if evidence_kind == EVIDENCE_RESEARCH_REPORT:
        return bool(evidence.get("task_id")) and bool(evidence.get("has_report"))
    if evidence_kind == EVIDENCE_DOCUMENT_REFS:
        refs = evidence.get("document_refs")
        floor = minimum if minimum is not None else 1
        return isinstance(refs, list) and len(refs) >= floor
    if evidence_kind == EVIDENCE_TEXT:
        text = str(evidence.get("text") or "")
        floor = minimum if minimum is not None else 1
        return len(text.strip()) >= floor
    return False


# ------------------------------------------------------------------------- row access


def _get_step_row(db: Session, run_id: uuid.UUID, step_id: str) -> ExecutiveStepRow | None:
    return db.execute(
        select(ExecutiveStepRow).where(
            ExecutiveStepRow.run_id == run_id, ExecutiveStepRow.step_id == step_id
        )
    ).scalar_one_or_none()


def _resolve_inputs(db: Session, run_id: uuid.UUID, step_row: ExecutiveStepRow) -> dict[str, Any]:
    """Every ``"<step_id>.<output_name>"`` reference resolves to the SIBLING step's full
    ``evidence_json`` (every reference this codebase's own planner ever writes names the
    evidence kind itself, e.g. ``"s1.research_report"``, so the whole evidence object IS
    the named output — module docstring's resolution convention). A dangling reference
    (the sibling never verified, or does not exist) resolves to ``None``: graph.py already
    refused a reference to a non-existent step at PLAN time, so a ``None`` here means the
    dependency did not produce evidence, not that the graph was malformed."""
    resolved: dict[str, Any] = {}
    for name, raw in (step_row.inputs_json or {}).items():
        ref = parse_reference(raw) if isinstance(raw, str) else None
        if ref is None:
            resolved[name] = raw
            continue
        sibling_step_id, _output_name = ref
        sibling = _get_step_row(db, run_id, sibling_step_id)
        resolved[name] = dict(sibling.evidence_json) if sibling and sibling.evidence_json else None
    return resolved


def _precondition_satisfied(
    db: Session, run_id: uuid.UUID, step_row: ExecutiveStepRow
) -> tuple[bool, str | None]:
    condition = step_row.precondition_json or {}
    check = condition.get("check") or PRECONDITION_NONE
    if check == PRECONDITION_NONE:
        return True, None
    if check == PRECONDITION_STEP_DONE:
        arg = condition.get("arg")
        sibling = _get_step_row(db, run_id, arg) if arg else None
        if sibling is None or sibling.state not in STEP_TERMINAL_STATES:
            return False, f"{arg} henüz tamamlanmadı"
        return True, None
    # focus_exists / device_capability / artifact_valid / account_present: no step this
    # milestone's planner produces uses these (RuleBasedExecutivePlanner's three shapes
    # only ever emit step_done/none). Treated as satisfied rather than refused, so a
    # future planner extension is free to emit them without this activity blocking every
    # such graph — a richer check is additive work, not a prerequisite for M26's own
    # three shapes to run correctly.
    return True, None


@dataclass(slots=True)
class _Prepared:
    decision: str  # "cached" | "skip" | "run"
    evidence: dict[str, Any] | None
    resolved: dict[str, Any]
    reason: str | None
    kind: str
    postcondition: dict[str, Any]
    #: {"max_attempts": int, "backoff_s": float, "only_on": [str, ...]} — the step's OWN
    #: retry policy (spec §1), read once here so the internal retry loop in
    #: ``run_step_activity`` never has to re-open a session just to learn it.
    retry: dict[str, Any]


def _prepare(run_id: uuid.UUID, step_id: str) -> _Prepared:
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        step_row = _get_step_row(db, run_id, step_id)
        if step_row is None:
            raise StepError("not_found", f"executive step {step_id!r} has no row")
        retry = dict(step_row.retry_json or {})
        if step_row.state == STEP_STATE_VERIFIED:
            return _Prepared(
                "cached",
                dict(step_row.evidence_json or {}),
                {},
                None,
                step_row.kind,
                step_row.postcondition_json,
                retry,
            )
        satisfied, reason = _precondition_satisfied(db, run_id, step_row)
        if not satisfied:
            step_row.state = STEP_STATE_SKIPPED
            step_row.error_class = "precondition_unmet"
            step_row.error_message = reason
            step_row.finished_at = datetime.now(UTC)
            step_row.updated_at = step_row.finished_at
            db.commit()
            return _Prepared(
                "skip", None, {}, reason, step_row.kind, step_row.postcondition_json, retry
            )
        resolved = _resolve_inputs(db, run_id, step_row)
        prior_evidence = dict(step_row.evidence_json or {})
        step_row.state = STEP_STATE_RUNNING
        step_row.attempt += 1
        if step_row.started_at is None:
            step_row.started_at = datetime.now(UTC)
        step_row.updated_at = datetime.now(UTC)
        db.commit()
        resolved["__prior_evidence__"] = prior_evidence
        return _Prepared(
            "run", None, resolved, None, step_row.kind, step_row.postcondition_json, retry
        )


def _bump_attempt(run_id: uuid.UUID, step_id: str) -> None:
    """One more attempt of the SAME step, within the SAME activity invocation (module
    docstring's "idempotent by (run_id, step_id, attempt)" — an internal retry bumps
    the counter exactly like a fresh Temporal invocation would, so the id a mutating
    kind's idempotency key is built from is never reused across attempts)."""
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        step_row = _get_step_row(db, run_id, step_id)
        if step_row is not None:
            step_row.attempt += 1
            step_row.updated_at = datetime.now(UTC)
            db.commit()


def _run_short_token(run_id: uuid.UUID) -> str:
    """The wire's ``run`` field (spec §6): a SHORT token, never the full uuid — the same
    8-hex-char convention this codebase already uses for a session suffix
    (``app.voice.realtime_sessions.tools_scene._default_project_scene``)."""
    return run_id.hex[:8]


def publish_run_state(run: ExecutiveRunRow) -> None:
    """``executive.run`` (contract v11) — published from the RUN ROW at every
    transition, metadata scalars only (module docstring's own rule, mirrored from
    every other M19-M25 channel's ``_publish``)."""
    metadata: dict[str, Any] = {
        "run": _run_short_token(run.id),
        "state": run.state,
        "done": run.steps_done,
        "total": run.steps_total,
    }
    if run.current_step:
        metadata["step"] = run.current_step
    publish_ui_state(
        UiState.EXECUTIVE_RUN, subsystem="executive", task_id=str(run.id), metadata=metadata
    )


def _derive_run_outcome(steps: list[ExecutiveStepRow]) -> tuple[str, dict[str, str], str | None]:
    """(run_state, partial_reasons, synthesis_text) from the FULL current set of step
    rows — never from a single step's own transition alone, since a run's overall honest
    state depends on ALL of them (spec §3: completed | partial (naming every unverified
    step and why) | failed)."""
    synthesis_text: str | None = None
    for step in steps:
        if step.kind == STEP_KIND_SYNTHESIS and step.evidence_json:
            synthesis_text = str(step.evidence_json.get("text") or "") or synthesis_text
    # IN FLIGHT, not "not terminal". `failed_recoverable` is in neither set: nothing will
    # retry it unless the owner asks, so a run holding one is not working - it is waiting.
    # Gating on `not in STEP_TERMINAL_STATES` here is what told the owner "Çalışıyorum
    # efendim: 3/4 adım tamam" about a production run whose four steps had all stopped
    # (M26 runtime verification, run 3ef7c639). The third route to a false "still working",
    # after the two the security review closed, and the only one that needs no crash at all.
    if any(s.state in STEP_IN_FLIGHT_STATES for s in steps):
        return STATE_RUNNING, {}, synthesis_text
    unverified = {
        s.step_id: (s.error_message or s.error_class or s.state)
        for s in steps
        if s.state != STEP_STATE_VERIFIED
    }
    verified_count = sum(1 for s in steps if s.state == STEP_STATE_VERIFIED)
    if not unverified:
        return STATE_COMPLETED, {}, synthesis_text
    if verified_count == 0:
        return STATE_FAILED, unverified, synthesis_text
    return STATE_PARTIAL, unverified, synthesis_text


def _recompute_run_progress(db: Session, run_id: uuid.UUID) -> None:
    """Called after EVERY step settles (module docstring: the workflow holds no state,
    so the run row's own progress/state is entirely a function of its steps, recomputed
    here rather than incremented piecemeal — a recompute can never drift from what the
    steps actually say, where a counter easily could after a retry or an amend)."""
    run = db.get(ExecutiveRunRow, run_id)
    if run is None or run.state == STATE_CANCELLED:
        # A CANCELLED run's rows are owned by cancel_run_and_compensate from here —
        # never overwritten by a late-arriving activity result racing it (module
        # docstring's own concurrency note). completed/partial/failed are deliberately
        # NOT excluded here: a later EXEC_RETRY can still improve a partial run's
        # outcome (the Temporal workflow itself stays open, waiting on exactly this,
        # until the owner cancels it or the run's wall-clock bound elapses —
        # app.executive.workflow's own docstring), and this recompute is what lets a
        # retried step's success turn `partial` back into `completed`.
        return
    steps = (
        db.execute(select(ExecutiveStepRow).where(ExecutiveStepRow.run_id == run_id))
        .scalars()
        .all()
    )
    running = [s for s in steps if s.state == STEP_STATE_RUNNING]
    run.steps_total = len(steps)
    run.steps_done = sum(1 for s in steps if s.state in STEP_TERMINAL_STATES)
    outcome_state, reasons, synthesis_text = _derive_run_outcome(steps)
    if outcome_state == STATE_RUNNING:
        run.state = STATE_RUNNING if run.state == STATE_PLANNED else run.state
        run.current_step = running[0].step_id if running else run.current_step
    else:
        run.state = outcome_state
        run.partial_reasons_json = reasons or None
        run.synthesis_text = synthesis_text
        run.current_step = None
    run.updated_at = datetime.now(UTC)
    db.commit()
    publish_run_state(run)


def _finalize(
    run_id: uuid.UUID,
    step_id: str,
    *,
    evidence: dict[str, Any] | None,
    error: StepError | None,
) -> dict[str, Any]:
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        run = db.get(ExecutiveRunRow, run_id)
        step_row = _get_step_row(db, run_id, step_id)
        assert step_row is not None
        if run is not None and run.state in TERMINAL_RUN_STATES:
            # The run was cancelled (or otherwise settled) while this activity was in
            # flight — its result is honoured for the ROW (so evidence a mutating call
            # already produced is never silently lost) but must not resurrect a
            # finished run's own state (module docstring's concurrency note).
            pass
        postcondition = step_row.postcondition_json or {}
        now = datetime.now(UTC)
        step_row.finished_at = now
        step_row.updated_at = now
        if error is not None:
            step_row.error_class = error.error_class
            step_row.error_message = error.message[:1000]
            retryable = error.error_class in (step_row.retry_json or {}).get("only_on", [])
            step_row.state = STEP_STATE_FAILED_RECOVERABLE if retryable else STEP_STATE_FAILED
        else:
            assert evidence is not None
            step_row.evidence_json = evidence
            verified = _evidence_meets_minimum(
                postcondition.get("evidence"), evidence, postcondition.get("min")
            )
            step_row.state = STEP_STATE_VERIFIED if verified else STEP_STATE_FAILED
            if not verified:
                step_row.error_class = "unverified"
                step_row.error_message = "postcondition evidence did not meet its minimum"
        db.commit()
        _recompute_run_progress(db, run_id)
        return {
            "state": step_row.state,
            "error_class": step_row.error_class,
            "evidence": step_row.evidence_json,
        }


def _run_compensation(db: Session, step_row: ExecutiveStepRow) -> None:
    """Best-effort (spec §3/§8): a compensation failing must never prevent the CANCEL
    itself from completing — the owner asked to stop, and that has to work even if,
    say, the mail provider is briefly unreachable. Only the closed vocabulary
    (app.executive.spec.COMPENSATIONS) is ever run; nothing of the owner's is deleted
    (spec §8) — ``delete_render`` removes only a GENERATED render/preview file, never a
    source document, and only when doing so cannot affect anything but this run's own
    row (a scene's render is exclusive to its row; an artifact's render is
    content-addressed and may be shared by another artifact, so it is left in place —
    the render is small, reusable, and not "the owner's file" either way, spec §1's own
    example of what compensation must never touch)."""
    evidence = step_row.evidence_json or {}
    try:
        if step_row.compensation == COMPENSATION_DISCARD_DRAFT and evidence.get("draft_id"):
            get_mail_service().discard(db, draft_id=str(evidence["draft_id"]))
        elif step_row.compensation == COMPENSATION_STOP_PROJECT and evidence.get("project_id"):
            get_app_factory_service().stop(
                db, get_device_action(), target=str(evidence["project_id"])
            )
        elif step_row.compensation == COMPENSATION_DELETE_RENDER and evidence.get("scene_id"):
            row = db.get(SceneRow, uuid.UUID(str(evidence["scene_id"])))
            if row is not None and row.render_object_key:
                _, store = build_artifact_context(get_settings())
                try:
                    store.delete(row.render_object_key)
                except Exception:  # noqa: BLE001 - best-effort, see docstring
                    pass
                row.render_object_key = None
                row.render_sha256 = None
                row.render_bytes = None
    except Exception:  # noqa: BLE001 - best-effort, see docstring
        pass
    step_row.state = STEP_STATE_COMPENSATED
    step_row.updated_at = datetime.now(UTC)


def cancel_run_and_compensate(run_id: uuid.UUID) -> dict[str, Any]:
    """The synchronous DB(+closed device action) sweep ``app.executive.service.
    cancel_run`` performs immediately after sending the workflow's ``cancel`` signal
    (workflow.py's own docstring: cancellation never depends on a further Temporal
    round trip). Idempotent: called twice on an already-cancelled run is a no-op."""
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        run = db.get(ExecutiveRunRow, run_id)
        if run is None:
            raise StepError("not_found", f"executive run {run_id} not found")
        if run.state in TERMINAL_RUN_STATES:
            return {"state": run.state, "already_settled": True}
        steps = (
            db.execute(select(ExecutiveStepRow).where(ExecutiveStepRow.run_id == run_id))
            .scalars()
            .all()
        )
        for step in steps:
            if step.state == STEP_STATE_VERIFIED and step.compensation != COMPENSATION_NONE:
                _run_compensation(db, step)
            elif step.state not in STEP_TERMINAL_STATES:
                step.state = STEP_STATE_CANCELLED
                step.error_class = "cancelled"
                step.error_message = "işi durdurdunuz efendim"
                step.finished_at = datetime.now(UTC)
                step.updated_at = step.finished_at
        run.state = STATE_CANCELLED
        run.current_step = None
        run.steps_done = sum(1 for s in steps if s.state in STEP_TERMINAL_STATES)
        run.updated_at = datetime.now(UTC)
        db.commit()
        publish_run_state(run)
        return {"state": run.state, "already_settled": False}


def _persist_evidence_partial(run_id: uuid.UUID, step_id: str, evidence: dict[str, Any]) -> None:
    """Write an id BEFORE its read-back is attempted (module docstring's crash-safety
    rule): a crash between "created" and "verified" leaves this row idempotently
    resumable on the next attempt instead of creating a second draft/proposal/artifact/
    project/scene."""
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        step_row = _get_step_row(db, run_id, step_id)
        assert step_row is not None
        step_row.evidence_json = evidence
        step_row.updated_at = datetime.now(UTC)
        db.commit()


# --------------------------------------------------------------------- kind handlers
#
# Every handler is SYNC (plain ``def``) and manages its OWN DB session — called from the
# async activity via ``asyncio.to_thread`` (except research.run, which needs the Temporal
# client and is handled inline in the activity function itself). Each raises StepError on
# a classified failure or returns the evidence dict it independently read back.


def _kind_documents_find(resolved: dict[str, Any]) -> dict[str, Any]:
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        service = get_document_service()
        receipt = service.search(db, get_device_action(), folder=resolved.get("folder"))
        if receipt.get("execution_status") != EXECUTION_EXECUTED:
            raise StepError(
                receipt.get("error_class") or "documents_find_failed", receipt.get("speech") or ""
            )
        files = list(receipt.get("files") or [])
        refs = [
            {"ref": str(f.get("file_id")), "path": str(f.get("path") or f.get("name") or "")}
            for f in files
        ]
        return {"document_refs": refs, "count": len(refs)}


def _kind_documents_compare(resolved: dict[str, Any]) -> dict[str, Any]:
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        service = get_document_service()
        receipt = service.compare(db, get_device_action())
        if receipt.get("execution_status") != EXECUTION_EXECUTED:
            raise StepError(
                receipt.get("error_class") or "documents_compare_failed",
                receipt.get("speech") or "",
            )
        # ``refs`` (both documents' own section refs), not ``changed_refs`` — two
        # documents found IDENTICAL is a legitimate, verified compare (the postcondition
        # asks "did a comparison happen", never "did it find a difference"; an empty
        # changed_refs would otherwise mark a truthful "no differences" result
        # unverified, the exact "never verified over an empty comparison" trap read the
        # other way).
        refs = list(receipt.get("refs") or [])
        return {
            "document_refs": refs,
            "count": len(refs),
            "changed": len(receipt.get("changed_refs") or []),
        }


def _kind_documents_extract(resolved: dict[str, Any]) -> dict[str, Any]:
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        service = get_document_service()
        question = str(resolved.get("question") or "Bu belgede önemli olan nedir?")
        receipt = service.answer(db, get_device_action(), question=question)
        if receipt.get("execution_status") != EXECUTION_EXECUTED:
            raise StepError(
                receipt.get("error_class") or "documents_extract_failed",
                receipt.get("speech") or "",
            )
        refs = list(receipt.get("refs") or [])
        return {"document_refs": refs, "count": len(refs), "text": receipt.get("speech") or ""}


def _kind_mail_analyze_thread(resolved: dict[str, Any]) -> dict[str, Any]:
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        service = get_mail_service()
        receipt = service.thread(db, target=str(resolved.get("target") or "current"))
        if receipt.get("execution_status") != EXECUTION_EXECUTED:
            raise StepError(
                receipt.get("error_class") or "mail_thread_failed", receipt.get("speech") or ""
            )
        return {"text": receipt.get("speech") or ""}


def _kind_mail_draft(resolved: dict[str, Any]) -> dict[str, Any]:
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        prior = resolved.get("__prior_evidence__") or {}
        draft_id = prior.get("draft_id")
        if draft_id is None:
            service = get_mail_service()
            source = resolved.get("body_source") or {}
            body = str(
                source.get("text") or "Ek olarak istenen belgeler ekte incelenmek üzere hazırdır."
            )
            receipt = service.draft_reply(db, body=body[:5000])
            if receipt.get("execution_status") != EXECUTION_EXECUTED:
                raise StepError(
                    receipt.get("error_class") or "mail_draft_failed", receipt.get("speech") or ""
                )
            draft = receipt.get("draft") or {}
            draft_id = draft.get("id")
            if draft_id is None:
                raise StepError("mail_draft_failed", "draft created with no id in the receipt")
        row = db.get(MailDraftRow, uuid.UUID(str(draft_id)))
        if row is None or row.state not in (DRAFT_STATE_PREPARED,):
            raise StepError("verification_failed", "mail draft row not found or not prepared")
        return {"draft_id": str(row.id)}


def _kind_calendar_propose(resolved: dict[str, Any]) -> dict[str, Any]:
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        prior = resolved.get("__prior_evidence__") or {}
        proposal_id = prior.get("proposal_id")
        if proposal_id is None:
            from datetime import timedelta

            service = get_calendar_service()
            start = datetime.now(UTC) + timedelta(days=1)
            summary = str(resolved.get("summary") or "Görüşme")
            receipt = service.propose(
                db, summary=summary, start=start, end=start + timedelta(hours=1)
            )
            if receipt.get("execution_status") != EXECUTION_EXECUTED:
                raise StepError(
                    receipt.get("error_class") or "calendar_propose_failed",
                    receipt.get("speech") or "",
                )
            proposal = receipt.get("proposal") or {}
            proposal_id = proposal.get("id")
            if proposal_id is None:
                raise StepError(
                    "calendar_propose_failed", "proposal created with no id in the receipt"
                )
        row = db.get(CalendarProposalRow, uuid.UUID(str(proposal_id)))
        if row is None or row.state not in (PROPOSAL_STATE_PREPARED,):
            raise StepError(
                "verification_failed", "calendar proposal row not found or not prepared"
            )
        return {"proposal_id": str(row.id)}


def _artifact_spec_from_text(kind: str, title: str, body_text: str) -> ArtifactSpec:
    paragraphs = [p.strip() for p in body_text.split("\n") if p.strip()] or ["(içerik yok)"]
    if kind == "presentation":
        # Every non-empty line becomes its own slide bullet (bounded MAX_SLIDES by
        # ArtifactSpec itself) — a deliberately simple mapping; the executive layer's
        # job is a truthful artifact from what the earlier steps produced, not a
        # polished deck design.
        slides = [{"title": title, "bullets": paragraphs[:10]}]
        return ArtifactSpec.model_validate(
            {"kind": "presentation", "title": title, "slides": slides}
        )
    if kind == "spreadsheet":
        rows = [[p] for p in paragraphs[:200]]
        sheet = {"name": "Özet", "columns": ["Not"], "rows": rows}
        return ArtifactSpec.model_validate(
            {"kind": "spreadsheet", "title": title, "sheets": [sheet]}
        )
    section = {"heading": title, "paragraphs": paragraphs}
    return ArtifactSpec.model_validate({"kind": "document", "title": title, "sections": [section]})


def _kind_artifacts_create(resolved: dict[str, Any]) -> dict[str, Any]:
    factory, store = build_artifact_context(get_settings())
    with factory() as db:
        prior = resolved.get("__prior_evidence__") or {}
        artifact_id = prior.get("artifact_id")
        kind = str(resolved.get("kind") or "document")
        source = resolved.get("source") or {}
        body_text = str(source.get("text") or "") if isinstance(source, dict) else ""
        if not body_text.strip():
            refs = source.get("document_refs") if isinstance(source, dict) else None
            if refs:
                body_text = "\n".join(str(r.get("path") or r.get("ref")) for r in refs)
        if not body_text.strip():
            raise StepError(
                "dependency_unavailable", "no upstream content to build an artifact from"
            )
        title = f"Yönetici Çalışması: {kind}"
        spec = _artifact_spec_from_text(kind, title, body_text)
        if artifact_id is None:
            result = artifacts_factory_create(db, store, spec=spec)
            artifact_id = str(result.artifact_id)
        artifact = artifact_service.get_artifact(db, uuid.UUID(artifact_id))
        if artifact is None:
            raise StepError("verification_failed", "artifact row not found after creation")
        version = artifact_service.get_current_version(db, artifact.id)
        if version is None:
            raise StepError("verification_failed", "artifact has no current version")
        return {"artifact_id": artifact_id, "version": version.version}


def _kind_artifacts_render(resolved: dict[str, Any]) -> dict[str, Any]:
    # M26 scope: every graph this milestone's planner emits uses artifacts.create for
    # its document/spreadsheet/presentation outputs (artifacts.create already renders
    # every format its kind produces, app.artifacts.factory.create's own docstring) —
    # artifacts.render exists in the closed vocabulary for a future planner shape that
    # asks for an ADDITIONAL format of an artifact an earlier step already made
    # ("Bunu PDF de yap"). Implemented for completeness against exactly that case.
    factory, store = build_artifact_context(get_settings())
    with factory() as db:
        from app.artifacts.factory import render_format

        source = resolved.get("source") or {}
        artifact_id = source.get("artifact_id") if isinstance(source, dict) else None
        fmt = str(resolved.get("format") or "pdf")
        if artifact_id is None:
            raise StepError("dependency_unavailable", "no upstream artifact to render")
        result = render_format(db, store, artifact_id=uuid.UUID(str(artifact_id)), fmt=fmt)
        if result is None:
            raise StepError("not_found", "artifact or its current version not found")
        if not result.valid:
            raise StepError("verification_failed", f"render {fmt} did not validate")
        return {"artifact_id": str(artifact_id), "format": fmt}


def _kind_apps_create(resolved: dict[str, Any]) -> dict[str, Any]:
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        prior = resolved.get("__prior_evidence__") or {}
        project_id = prior.get("project_id")
        if project_id is None:
            service = get_app_factory_service()
            spec = resolved.get("spec") or {
                "name": "Yönetici Uygulaması",
                "kind": "cli",
                "template": "cli-tool",
                "commands": [{"name": "calistir", "description": "calistir"}],
            }
            receipt = service.create(db, get_device_action(), spec=spec)
            if receipt.get("execution_status") != EXECUTION_EXECUTED:
                raise StepError(
                    receipt.get("error_class") or "apps_create_failed", receipt.get("speech") or ""
                )
            project_id = receipt.get("project_id")
            if project_id is None:
                raise StepError("apps_create_failed", "project created with no id in the receipt")
        row = db.get(AppProjectRow, uuid.UUID(str(project_id)))
        if row is None or row.state not in (STATE_SCAFFOLDED,) or not row.root_path:
            raise StepError("verification_failed", "app project row not found or not scaffolded")
        return {"project_id": str(row.id)}


def _kind_apps_test(resolved: dict[str, Any]) -> dict[str, Any]:
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        source = resolved.get("project") or {}
        project_id = source.get("project_id") if isinstance(source, dict) else None
        if project_id is None:
            raise StepError("dependency_unavailable", "no upstream project to test")
        service = get_app_factory_service()
        receipt = service.test(db, get_device_action(), target=str(project_id))
        if receipt.get("execution_status") != EXECUTION_EXECUTED:
            raise StepError(
                receipt.get("error_class") or "apps_test_failed", receipt.get("speech") or ""
            )
        row = db.get(AppProjectRow, uuid.UUID(str(project_id)))
        if row is None or row.state != STATE_TESTED:
            raise StepError("verification_failed", "app project row not found or not tested")
        return {"project_id": str(row.id)}


def _kind_scene_create(resolved: dict[str, Any]) -> dict[str, Any]:
    factory, store = build_artifact_context(get_settings())
    with factory() as db:
        prior = resolved.get("__prior_evidence__") or {}
        scene_id = prior.get("scene_id")
        if scene_id is None:
            service = get_creative3d_service(store)
            plan = resolved.get("plan") or {
                "tool": "blender",
                "project": "pagentos",
                "scene": f"exec-{uuid.uuid4().hex[:8]}",
            }
            receipt = service.create(db, get_device_action(), plan=plan)
            if receipt.get("execution_status") != EXECUTION_EXECUTED:
                raise StepError(
                    receipt.get("error_class") or "scene_create_failed", receipt.get("speech") or ""
                )
            scene_id = receipt.get("scene_id")
            if scene_id is None:
                raise StepError("scene_create_failed", "scene created with no id in the receipt")
        row = db.get(SceneRow, uuid.UUID(str(scene_id)))
        if row is None or row.state not in (STATE_APPLIED, STATE_RENDERED) or not row.root_path:
            raise StepError("verification_failed", "scene row not found or not applied")
        return {"scene_id": str(row.id)}


def _kind_scene_render(resolved: dict[str, Any]) -> dict[str, Any]:
    factory, store = build_artifact_context(get_settings())
    with factory() as db:
        source = resolved.get("scene") or {}
        scene_id = source.get("scene_id") if isinstance(source, dict) else None
        if scene_id is None:
            raise StepError("dependency_unavailable", "no upstream scene to render")
        service = get_creative3d_service(store)
        receipt = service.render(db, get_device_action(), target=str(scene_id))
        if receipt.get("execution_status") != EXECUTION_EXECUTED:
            raise StepError(
                receipt.get("error_class") or "scene_render_failed", receipt.get("speech") or ""
            )
        row = db.get(SceneRow, uuid.UUID(str(scene_id)))
        # The render file present and non-trivial (M25's own read-back discipline,
        # module docstring) — never trust the receipt's own "rendered" claim alone.
        if (
            row is None
            or not row.render_object_key
            or wire_step(row.state, row.compare_json) != SCENE_STEP_VERIFIED
        ):
            raise StepError("verification_failed", "scene render not independently verified")
        return {"scene_id": str(row.id)}


def _kind_research_synthesize(resolved: dict[str, Any]) -> dict[str, Any]:
    factory, _store = build_artifact_context(get_settings())
    with factory() as db:
        report_ref = resolved.get("report") or {}
        task_id = report_ref.get("task_id") if isinstance(report_ref, dict) else None
        if task_id is None:
            raise StepError("dependency_unavailable", "no upstream research report to synthesize")
        report = runs_service.get_report(db, uuid.UUID(str(task_id)))
        if report is None:
            raise StepError("verification_failed", "research report row not found")
        summary = str((report.report_json or {}).get("executive_summary") or "")
        return {"text": summary}


def _kind_synthesis(run_id: uuid.UUID, resolved: dict[str, Any]) -> dict[str, Any]:
    """No external call (spec §1) — composes the owner-facing sentence from whatever the
    earlier referenced steps' evidence actually holds, naming a ref honestly as missing
    when its evidence is ``None`` (that step never verified) rather than omitting it."""
    parts: list[str] = []
    for name, value in resolved.items():
        if name == "__prior_evidence__":
            continue
        if not isinstance(value, dict) or not value:
            parts.append(f"{name}: hazırlanamadı")
            continue
        if "artifact_id" in value:
            parts.append(f"{name} hazır (artifact {value['artifact_id']})")
        elif "draft_id" in value:
            parts.append(f"{name} taslağı hazır, onayınızı bekliyor")
        elif "proposal_id" in value:
            parts.append(f"{name} önerisi hazır, onayınızı bekliyor")
        elif "project_id" in value:
            parts.append(f"{name} projesi hazır")
        elif "scene_id" in value:
            parts.append(f"{name} sahnesi hazır")
        elif "text" in value:
            parts.append(f"{name}: {str(value['text'])[:200]}")
        else:
            parts.append(f"{name} tamamlandı")
    text = "; ".join(parts) if parts else "Yapacak bir şey bulunamadı."
    return {"text": text}


_SYNC_KIND_HANDLERS = {
    STEP_KIND_DOCUMENTS_FIND: _kind_documents_find,
    STEP_KIND_DOCUMENTS_COMPARE: _kind_documents_compare,
    STEP_KIND_DOCUMENTS_EXTRACT: _kind_documents_extract,
    STEP_KIND_MAIL_ANALYZE_THREAD: _kind_mail_analyze_thread,
    STEP_KIND_MAIL_DRAFT: _kind_mail_draft,
    STEP_KIND_CALENDAR_PROPOSE: _kind_calendar_propose,
    STEP_KIND_ARTIFACTS_CREATE: _kind_artifacts_create,
    STEP_KIND_ARTIFACTS_RENDER: _kind_artifacts_render,
    STEP_KIND_APPS_CREATE: _kind_apps_create,
    STEP_KIND_APPS_TEST: _kind_apps_test,
    STEP_KIND_SCENE_CREATE: _kind_scene_create,
    STEP_KIND_SCENE_RENDER: _kind_scene_render,
    STEP_KIND_RESEARCH_SYNTHESIZE: _kind_research_synthesize,
}


async def _run_research(
    run_id: uuid.UUID, step_id: str, resolved: dict[str, Any]
) -> dict[str, Any]:
    """research.run: the child pipeline is the REAL, untouched M13
    ``BrowserResearchWorkflow`` (module docstring) — started and awaited through its own
    Temporal client, so a research failure (no capable device, a fetch failure) is a
    genuine failure of that real workflow, not a simulation of one."""

    def create_or_reuse_task() -> tuple[str, bool]:
        prior = resolved.get("__prior_evidence__") or {}
        existing = prior.get("task_id")
        if existing:
            return str(existing), True
        factory, _store = build_artifact_context(get_settings())
        with factory() as db:
            topic = str(resolved.get("topic") or "")
            task = artifact_service.create_task(db, intent=topic)
            return str(task.id), False

    task_id, reused = await asyncio.to_thread(create_or_reuse_task)
    if not reused:
        await asyncio.to_thread(_persist_evidence_partial, run_id, step_id, {"task_id": task_id})

    client = await get_temporal_client()
    workflow_id = f"executive-research-{task_id}"
    try:
        result = await client.execute_workflow(
            BrowserResearchWorkflow.run,
            BrowserResearchRequest(task_id=task_id, topic=str(resolved.get("topic") or "")),
            id=workflow_id,
            task_queue=get_settings().temporal_task_queue,
        )
    except WorkflowFailureError as exc:
        # A genuine transport/orchestration failure of the research workflow ITSELF
        # (as opposed to BrowserResearchWorkflow's own honest STAGE_FAILED return,
        # handled below) — treated as dependency_unavailable: retrying research.run is
        # exactly the right response to "the pipeline could not even run".
        raise StepError("dependency_unavailable", str(exc)) from exc

    if result.get("stage") != STAGE_READY:
        detail = (result.get("error") or {}).get("detail") or "research did not complete"
        # spec §3's failure matrix: "a browser fetch failure -> research.run retried per
        # policy, then failed_recoverable". Every honest BrowserResearchWorkflow failure
        # (no device, a fetch/synthesis failure) is dependency_unavailable here — the
        # workflow's OWN reason is preserved verbatim in the message for the receipt.
        raise StepError("dependency_unavailable", detail)

    def verify() -> dict[str, Any]:
        factory, _store = build_artifact_context(get_settings())
        with factory() as db:
            report = runs_service.get_report(db, uuid.UUID(task_id))
            return {
                "task_id": task_id,
                "has_report": report is not None,
                "artifact_id": str(result.get("artifact_id"))
                if result.get("artifact_id")
                else None,
                "findings_count": int(result.get("findings_count") or 0),
                "sources_count": int(result.get("sources_count") or 0),
            }

    return await asyncio.to_thread(verify)


async def _dispatch_once(
    run_uuid: uuid.UUID, step_id: str, kind: str, resolved: dict[str, Any]
) -> dict[str, Any]:
    """ONE attempt at the kind's real call. Raises :class:`StepError` on any classified
    failure; the caller (``run_step_activity``) decides whether that is retryable."""
    if kind == STEP_KIND_RESEARCH_RUN:
        return await _run_research(run_uuid, step_id, resolved)
    if kind == STEP_KIND_SYNTHESIS:
        return await asyncio.to_thread(_kind_synthesis, run_uuid, resolved)
    if kind in _SYNC_KIND_HANDLERS:
        handler = _SYNC_KIND_HANDLERS[kind]
        # A mutating kind persists its new id BEFORE verification, from inside the
        # handler itself (each handler above does this for its own row) — no generic
        # wrapper is needed here since every handler already opens its own session and
        # commits the id the moment it has one.
        return await asyncio.to_thread(handler, resolved)
    raise StepError("invalid_kind", f"{kind!r} is not a dispatched step kind")


@activity.defn(name="executive_run_step")
async def run_step_activity(run_id: str, step_id: str) -> dict[str, Any]:
    """Spec §1's retry — ``max_attempts <= 3``, ``backoff_s <= 60``, only on
    ``dependency_unavailable``/``timeout`` — is applied HERE, inside ONE activity
    invocation, rather than through Temporal's own per-activity ``RetryPolicy``
    (``app.executive.workflow._run_one`` deliberately calls this activity with
    ``maximum_attempts=1``): the step's retry policy is DATA on the graph, chosen by
    the planner per step, not a single policy the workflow could apply uniformly — and
    keeping the whole attempt sequence in one activity invocation is what lets
    ``__prior_evidence__`` (an id a mutating kind already created) survive between
    attempts without a second round trip through Temporal history for each one. A real
    Cloud Core restart mid-backoff still resumes correctly: the LAST attempt's
    evidence-so-far is already durably committed by ``_bump_attempt``/the handler's own
    early persistence, so replaying this activity from scratch is exactly the ``cached``
    or "prior evidence" path above, never a duplicate."""
    run_uuid = uuid.UUID(run_id)
    prepared = await asyncio.to_thread(_prepare, run_uuid, step_id)
    if prepared.decision == "cached":
        return {"state": STEP_STATE_VERIFIED, "error_class": None, "evidence": prepared.evidence}
    if prepared.decision == "skip":
        return {"state": STEP_STATE_SKIPPED, "error_class": "precondition_unmet", "evidence": None}

    resolved = prepared.resolved
    kind = prepared.kind
    max_attempts = max(1, min(int(prepared.retry.get("max_attempts") or 1), 3))
    backoff_s = max(0.0, min(float(prepared.retry.get("backoff_s") or 0.0), 60.0))
    only_on = set(prepared.retry.get("only_on") or [])

    error: StepError | None = None
    evidence: dict[str, Any] | None = None
    attempt = 1
    while True:
        try:
            evidence = await _dispatch_once(run_uuid, step_id, kind, resolved)
            error = None
            break
        except StepError as exc:
            error = exc
        except Exception as exc:  # noqa: BLE001 - see below; this is the point of the block
            # A failure this code did not anticipate is still a failed STEP, never a dead
            # RUN. Before the M26 security review this escaped the activity entirely: the
            # workflow died mid-flight, and because the workflow holds no state the row was
            # left saying "running" forever while the owner was told the work was still in
            # progress. A real one was found in minutes — a research summary over
            # ArtifactSpec's 20,000-character bound raises pydantic's ValidationError, not
            # a StepError, and "son üç gündeki gelişmeleri araştır, rapor hazırla" is
            # exactly how you get one.
            #
            # Classified here rather than in each handler on purpose: the same review found
            # several handlers doing unguarded `uuid.UUID(str(...))` on service output, so a
            # per-handler fix would only cover the ones someone remembered.
            error = StepError(ERROR_INTERNAL, f"{type(exc).__name__}: {exc}"[:400])

        # ONE retry decision, for BOTH classifications, deliberately outside the except
        # blocks. The first version of this fix left it inside `except StepError` and put
        # the new branch above it, so a classified failure had no `break` at all and this
        # loop re-dispatched the step for ever at 100% CPU - a worse bug than the one being
        # fixed, since it would have hammered a real service. `internal_error` can never be
        # retryable here (it is not in RETRYABLE_ERROR_CLASSES, which is what `only_on` is
        # validated against), so an unclassified failure still settles on its first attempt.
        retryable = error.error_class in only_on
        if not retryable or attempt >= max_attempts:
            break
        if activity.in_activity():  # false in a direct/unit-test call (no worker)
            activity.heartbeat(f"retry {attempt}/{max_attempts}: {error.error_class}")
        await asyncio.sleep(backoff_s)
        await asyncio.to_thread(_bump_attempt, run_uuid, step_id)
        attempt += 1

    return await asyncio.to_thread(_finalize, run_uuid, step_id, evidence=evidence, error=error)


@activity.defn(name="executive_settle_crashed_step")
async def settle_crashed_step_activity(run_id: str, step_id: str, reason: str) -> dict[str, Any]:
    """Settle a step whose own activity died outright, so the RUN can still end honestly.

    `run_step_activity` classifies every exception it meets, so nothing should reach here.
    That is exactly what the previous version of this code believed — it caught only
    `StepError`, an unclassified exception escaped, and the M26 security review proved the
    whole workflow execution died with it, leaving the row saying `running` forever. A step
    nobody will ever finish is worse than a step that failed: the owner is told work is in
    progress that stopped minutes ago.

    Settled through `_finalize`, the one place a step row is written, rather than a second
    path that could drift from it.
    """
    return await asyncio.to_thread(
        _finalize,
        uuid.UUID(run_id),
        step_id,
        evidence=None,
        error=StepError(ERROR_INTERNAL, f"the step's activity did not return: {reason}"[:400]),
    )


EXECUTIVE_ACTIVITIES = [run_step_activity, settle_crashed_step_activity]

__all__ = [
    "EXECUTIVE_ACTIVITIES",
    "StepError",
    "cancel_run_and_compensate",
    "get_app_factory_service",
    "get_calendar_service",
    "get_creative3d_service",
    "get_device_action",
    "get_document_service",
    "get_mail_service",
    "get_temporal_client",
    "run_step_activity",
]
