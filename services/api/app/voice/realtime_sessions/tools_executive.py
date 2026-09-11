"""Executive Autonomy's voice tools (docs/M26_EXECUTIVE_AUTONOMY_SPEC.md §5). Eight
tools, registered from ``tools.default_registry()`` by ONE added line
(:func:`register_executive_tools`), the same discipline ``tools_apps``/``tools_scene``
already establish for their own families.

``executive.start`` mirrors ``research.start`` exactly (``app.voice.realtime_sessions.
tools.research_start``'s own docstring): the SYNCHRONOUS half
(``app.executive.service.start_run_db``) runs inside this tool call's own DB
transaction, so a spoken directive and a REST-initiated run are the same run from the
first row written on; the ASYNCHRONOUS half (starting the Temporal workflow) is handed
to ``ctx.followups`` because ``Client.start_workflow`` only exists as a coroutine and
this handler is sync. The other seven tools follow the SAME split
(``app.executive.service``'s own module docstring): the DB half (validate + transition)
runs here and is what the OWNER is told about; the Temporal SIGNAL is best-effort
delivery to the workflow, deferred to a followup for the identical reason.

A refusal (no such run, wrong state, an unrecognised directive shape) is returned as
``{"status": "needs_clarification", "speech": ...}`` — the SAME shape
``app.documents.service``/``app.mail.service`` already use for their own refusals,
never a raised error for something this ordinary.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Final

from app.executive import service as executive_service
from app.executive.models import (
    STATE_PAUSED,
    STATE_RUNNING,
    TERMINAL_RUN_STATES,
    ExecutiveRunRow,
    ExecutiveStepRow,
)
from app.executive.service import ExecutiveServiceError
from app.executive.spec import STEP_KIND_PROFILES
from app.logging import get_logger

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.tools_executive")

TOOL_EXECUTIVE_START: Final = "executive.start"
TOOL_EXECUTIVE_STATUS: Final = "executive.status"
TOOL_EXECUTIVE_EXPLAIN: Final = "executive.explain"
TOOL_EXECUTIVE_PAUSE: Final = "executive.pause"
TOOL_EXECUTIVE_RESUME: Final = "executive.resume"
TOOL_EXECUTIVE_RETRY: Final = "executive.retry"
TOOL_EXECUTIVE_AMEND: Final = "executive.amend"
TOOL_EXECUTIVE_CANCEL: Final = "executive.cancel"

EXECUTIVE_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_EXECUTIVE_START,
    TOOL_EXECUTIVE_STATUS,
    TOOL_EXECUTIVE_EXPLAIN,
    TOOL_EXECUTIVE_PAUSE,
    TOOL_EXECUTIVE_RESUME,
    TOOL_EXECUTIVE_RETRY,
    TOOL_EXECUTIVE_AMEND,
    TOOL_EXECUTIVE_CANCEL,
)

SPEECH_NO_RUN = "Şu anda yürüttüğüm bir iş yok efendim."


def _turn_record(ctx: ToolContext) -> dict[str, Any]:
    """The ONE router's record of this turn (``exec_shape``/``exec_step_ordinal``/
    ``exec_kind_hint``/``exec_amend_kind``/``folder`` — the owner's own WORDS,
    preferred over the model's own argument, the same rule M20/M23/M25's own tools
    already follow)."""
    return dict(ctx.context.get("last_utterance") or {})


def _clarification(speech: str, *, error_class: str = "clarification_needed") -> dict[str, Any]:
    return {"status": "needs_clarification", "speech": speech, "error_class": error_class}


def _resolve_run(ctx: ToolContext, *, require_active: bool = False) -> ExecutiveRunRow | None:
    """The owner's own most recent run — "current" in every sense the object-focus
    families already give "current" (document/artifact/app/scene): there are <= 2
    active runs at once (spec §4), so "the run" almost never needs disambiguating
    further. ``require_active`` narrows to a non-terminal run (spec §5's own negative
    cases: "Devam et" with nothing paused, "Bunu iptal et" with no run)."""
    if ctx.db is None:
        return None
    from sqlalchemy import select

    stmt = select(ExecutiveRunRow).order_by(ExecutiveRunRow.created_at.desc())
    if require_active:
        stmt = stmt.where(ExecutiveRunRow.state.notin_(TERMINAL_RUN_STATES))
    return ctx.db.execute(stmt.limit(1)).scalars().first()


def _resolve_step(ctx: ToolContext, run: ExecutiveRunRow) -> ExecutiveStepRow | None:
    """The step a retry's WORDS pointed at — an ordinal ("ikinci adımı") against the
    run's OWN steps in creation order, or a kind hint ("araştırmayı") against the run's
    step kinds. Neither found -> None (the caller asks which, spec §5's own negative
    case: "İkinci adımı tekrar dene" with only one step)."""
    assert ctx.db is not None
    turn = _turn_record(ctx)
    steps = executive_service.list_steps(ctx.db, run.id)
    ordinal = turn.get("exec_step_ordinal")
    if isinstance(ordinal, int) and 1 <= ordinal <= len(steps):
        return steps[ordinal - 1]
    hint = turn.get("exec_kind_hint")
    if isinstance(hint, str) and hint:
        matches = [s for s in steps if s.kind.startswith(hint)]
        if len(matches) == 1:
            return matches[0]
    return None


def executive_start(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    turn = _turn_record(ctx)
    # The owner's own free text (module docstring: EXEC_START is the one intent whose
    # tool needs full free text) is preferred when the model supplied none at all —
    # never overriding a model argument that IS present, the same "owner's words win
    # only when they actually said something" rule every other field in this file
    # follows; a directive is the one place that fallback is the MODEL argument's own
    # absence rather than the router's field being empty.
    directive = str(arguments.get("directive") or turn.get("exec_directive_text") or "").strip()[
        :2000
    ]
    if not directive:
        return _clarification(
            "Ne yapmamı istediğinizi söyler misiniz efendim?", error_class="invalid_argument"
        )
    if ctx.db is None:
        return _clarification("Bu işi başlatmak için veritabanına ihtiyacım var efendim.")
    folder = turn.get("folder") if isinstance(turn.get("folder"), str) else None

    try:
        run = executive_service.start_run_db(
            ctx.db,
            directive=directive,
            folder=folder,
            source="voice",
            session_id=str(ctx.session_id),
        )
    except ExecutiveServiceError as exc:
        return _clarification(exc.speech, error_class=exc.error_class)

    artifacts_runtime = ctx.live.get("artifacts_runtime")
    run_id = run.id
    task_queue = (
        artifacts_runtime.settings.temporal_task_queue if artifacts_runtime else "pagentos-core"
    )

    async def _start_workflow_followup() -> None:
        try:
            from app.research.service import connect_temporal

            client = await connect_temporal(artifacts_runtime)
            await executive_service.start_run_workflow(
                client, run, task_queue=task_queue, artifacts=artifacts_runtime
            )
        except Exception as exc:  # noqa: BLE001 - reported via the run row, never raised here
            logger.exception("voice_executive_workflow_start_failed", run_id=str(run_id))
            # Phase 8: "reported via the run row" - and until 2026-09-11 nothing wrote it, so
            # the run stayed active for ever and counted against the two-run bound.
            if artifacts_runtime is not None:
                with artifacts_runtime.session() as db_run:
                    executive_service.fail_unstarted_run(
                        db_run, run_id, detail=f"{type(exc).__name__}: {exc}"
                    )

    ctx.add_followup(_start_workflow_followup)
    return {
        "run_id": str(run.id),
        "state": run.state,
        "steps_total": run.steps_total,
        "speech": "Başlıyorum efendim.",
    }


def executive_status(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if ctx.db is None:
        return _clarification(SPEECH_NO_RUN)
    run = _resolve_run(ctx)
    if run is None:
        return _clarification(SPEECH_NO_RUN, error_class="not_found")
    return executive_service.get_status(ctx.db, run.id)


def executive_explain(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if ctx.db is None:
        return _clarification(SPEECH_NO_RUN)
    run = _resolve_run(ctx)
    if run is None:
        return _clarification(SPEECH_NO_RUN, error_class="not_found")
    return executive_service.get_explain(ctx.db, run.id)


def _signal_followup(ctx: ToolContext, coro_factory: Any, run_id: uuid.UUID, *extra: Any) -> None:
    async def _followup() -> None:
        try:
            from app.research.service import connect_temporal

            artifacts_runtime = ctx.live.get("artifacts_runtime")
            client = await connect_temporal(artifacts_runtime)
            await coro_factory(client, run_id, *extra)
        except Exception:  # noqa: BLE001 - the DB half already told the owner; this is
            # best-effort delivery to the live workflow (module docstring).
            logger.exception("voice_executive_signal_failed", run_id=str(run_id))

    ctx.add_followup(_followup)


def executive_pause(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if ctx.db is None:
        return _clarification(SPEECH_NO_RUN)
    run = _resolve_run(ctx, require_active=True)
    if run is None or run.state != STATE_RUNNING:
        return _clarification(
            "Duraklatacak, çalışan bir iş yok efendim.", error_class="invalid_state"
        )
    try:
        run = executive_service.pause_run_db(ctx.db, run.id)
    except ExecutiveServiceError as exc:
        return _clarification(exc.speech, error_class=exc.error_class)
    _signal_followup(ctx, executive_service.pause_run_signal, run.id)
    return {"run_id": str(run.id), "speech": "Duraklatıyorum efendim."}


def executive_resume(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if ctx.db is None:
        return _clarification(SPEECH_NO_RUN)
    run = _resolve_run(ctx, require_active=True)
    if run is None or run.state != STATE_PAUSED:
        return _clarification(
            "Duraklatılmış bir iş yok, devam ettirecek bir şey yok efendim.",
            error_class="invalid_state",
        )
    try:
        run = executive_service.resume_run_db(ctx.db, run.id)
    except ExecutiveServiceError as exc:
        return _clarification(exc.speech, error_class=exc.error_class)
    _signal_followup(ctx, executive_service.resume_run_signal, run.id)
    return {"run_id": str(run.id), "speech": "Devam ediyorum efendim."}


def executive_cancel(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if ctx.db is None:
        return _clarification(SPEECH_NO_RUN)
    run = _resolve_run(ctx, require_active=True)
    if run is None:
        return _clarification(SPEECH_NO_RUN, error_class="not_found")
    try:
        executive_service.cancel_run_validate(ctx.db, run.id)
    except ExecutiveServiceError as exc:
        return _clarification(exc.speech, error_class=exc.error_class)
    _signal_followup(ctx, executive_service.cancel_run_signal_and_sweep, run.id)
    return {"run_id": str(run.id), "speech": "İptal ediyorum efendim."}


def executive_retry(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if ctx.db is None:
        return _clarification(SPEECH_NO_RUN)
    run = _resolve_run(ctx)
    if run is None:
        return _clarification(SPEECH_NO_RUN, error_class="not_found")
    step = _resolve_step(ctx, run)
    step_id = step.step_id if step is not None else str(arguments.get("step_id") or "")
    if not step_id:
        return _clarification(
            "Hangi adımı tekrar denememi istersiniz efendim?", error_class="clarification_needed"
        )
    try:
        executive_service.retry_step_validate(ctx.db, run.id, step_id)
    except ExecutiveServiceError as exc:
        return _clarification(exc.speech, error_class=exc.error_class)
    _signal_followup(ctx, executive_service.retry_step_signal, run.id, step_id)
    return {
        "run_id": str(run.id),
        "step_id": step_id,
        "speech": f"{step_id} adımını tekrar deniyorum efendim.",
    }


#: The deliverable kind words the router recognised (exec_amend_kind) map onto a
#: minimal, valid amendment step — spec §1's own closed vocabulary
#: (app.executive.spec.STEP_KIND_ARTIFACTS_CREATE), never a free-form step the model
#: could otherwise invent (ADR-0089 decision 1: EVERY graph, amended or not, is data
#: validated by graph.py, and this is what keeps that true for an amendment too).
def _amend_step_for_kind(kind: str, precondition_step: str) -> dict[str, Any]:
    from app.executive.spec import (
        COMPENSATION_DELETE_RENDER,
        EVIDENCE_ARTIFACT_ID,
        PRECONDITION_STEP_DONE,
        RISK_MUTATE_LOCAL,
        STEP_KIND_ARTIFACTS_CREATE,
    )

    return {
        # <= app.executive.spec.MAX_STEP_ID_CHARS (8) — "amend" + 4 hex chars was 9
        # and silently failed Step's own length bound every time (found via the
        # corpus: every exec.amend.* case answered "Bu eklemeyi anlayamadım efendim.").
        "id": f"am{uuid.uuid4().hex[:6]}",
        "kind": STEP_KIND_ARTIFACTS_CREATE,
        "inputs": {"kind": kind, "source": f"{precondition_step}.text"},
        "precondition": {"check": PRECONDITION_STEP_DONE, "arg": precondition_step},
        "postcondition": {"evidence": EVIDENCE_ARTIFACT_ID},
        "timeout_s": 180,
        "retry": {"max_attempts": 2, "backoff_s": 10.0, "only_on": ["timeout"]},
        "risk_class": RISK_MUTATE_LOCAL,
        "compensation": COMPENSATION_DELETE_RENDER,
    }


def executive_amend(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if ctx.db is None:
        return _clarification(SPEECH_NO_RUN)
    run = _resolve_run(ctx, require_active=True)
    if run is None:
        return _clarification(SPEECH_NO_RUN, error_class="not_found")
    turn = _turn_record(ctx)
    kind = turn.get("exec_amend_kind")
    if not isinstance(kind, str) or not kind:
        return _clarification(
            "Ne eklememi istersiniz efendim: sunum mu, Excel mi, rapor mu?",
            error_class="clarification_needed",
        )
    steps = executive_service.list_steps(ctx.db, run.id)
    # The new step depends on the SYNTHESIS-adjacent content step (the last one that
    # actually produces owner-facing text) — a reasonable default an amendment can
    # always attach after; a graph with no such step at all is not one this planner
    # ever produces (every shape ends in a text-producing step, app.executive.planner).
    text_producing = [
        s
        for s in steps
        if STEP_KIND_PROFILES.get(s.kind) and STEP_KIND_PROFILES[s.kind].evidence_kind == "text"
    ]
    if not text_producing:
        return _clarification(
            "Bu işe şu an bir şey ekleyemiyorum efendim.", error_class="invalid_state"
        )
    precondition_step = text_producing[0].step_id
    new_step = _amend_step_for_kind(kind, precondition_step)
    try:
        candidate = executive_service.amend_run_db(ctx.db, run.id, new_step)
    except ExecutiveServiceError as exc:
        return _clarification(exc.speech, error_class=exc.error_class)
    _signal_followup_amend(ctx, run.id, candidate)
    return {
        "run_id": str(run.id),
        "step_id": candidate.id,
        "speech": f"{candidate.id} adımını ekledim efendim.",
    }


def _signal_followup_amend(ctx: ToolContext, run_id: uuid.UUID, candidate: Any) -> None:
    async def _followup() -> None:
        try:
            from app.research.service import connect_temporal

            artifacts_runtime = ctx.live.get("artifacts_runtime")
            client = await connect_temporal(artifacts_runtime)
            await executive_service.amend_run_signal(client, run_id, candidate)
        except Exception:  # noqa: BLE001 - see _signal_followup's own note
            logger.exception("voice_executive_amend_signal_failed", run_id=str(run_id))

    ctx.add_followup(_followup)


def register_executive_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_EXECUTIVE_START,
            description=(
                "Çok adımlı bir işi başlatır: araştırma+rapor, klasör karşılaştırma "
                "veya mail taslağı hazırlama."
            ),
            parameters={
                "type": "object",
                "properties": {"directive": {"type": "string", "maxLength": 2000}},
                "required": ["directive"],
                "additionalProperties": False,
            },
            handler=executive_start,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_EXECUTIVE_STATUS,
            description="Yürüyen işin durumunu ve kaç adımının bittiğini söyler.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=executive_status,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_EXECUTIVE_EXPLAIN,
            description="Şu an tam olarak hangi adımı yaptığını tek cümleyle anlatır.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=executive_explain,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_EXECUTIVE_PAUSE,
            description="Yürüyen işi duraklatır; çalışan adım biter, yenisi başlamaz.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=executive_pause,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_EXECUTIVE_RESUME,
            description="Duraklatılmış işi devam ettirir.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=executive_resume,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_EXECUTIVE_RETRY,
            description="Başarısız olan bir adımı tekrar dener.",
            parameters={
                "type": "object",
                "properties": {"step_id": {"type": "string", "maxLength": 8}},
                "additionalProperties": False,
            },
            handler=executive_retry,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_EXECUTIVE_AMEND,
            description="Yürüyen işe yeni bir çıktı ekler (sunum, Excel, rapor).",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=executive_amend,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_EXECUTIVE_CANCEL,
            description="Yürüyen işi iptal eder.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=executive_cancel,
        )
    )
    return reg


__all__ = [
    "EXECUTIVE_TOOL_NAMES",
    "TOOL_EXECUTIVE_AMEND",
    "TOOL_EXECUTIVE_CANCEL",
    "TOOL_EXECUTIVE_EXPLAIN",
    "TOOL_EXECUTIVE_PAUSE",
    "TOOL_EXECUTIVE_RESUME",
    "TOOL_EXECUTIVE_RETRY",
    "TOOL_EXECUTIVE_START",
    "TOOL_EXECUTIVE_STATUS",
    "executive_amend",
    "executive_cancel",
    "executive_explain",
    "executive_pause",
    "executive_resume",
    "executive_retry",
    "executive_start",
    "executive_status",
    "register_executive_tools",
]
