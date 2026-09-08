"""``ExecutiveWorkflow`` (spec §3, ADR-0089 decision 3): topological order with bounded
fan-out, one idempotent activity per step, the workflow itself holding no state — the M13
discipline ``app.research.workflow``/``browser_workflow`` already prove, applied to a
graph instead of a fixed pipeline.

The workflow's only local variables are a SCHEDULING projection derived once from the
graph it was given (which step depends on which — a plain dict of id -> dependency ids,
never the steps' own kind/inputs/postcondition, which live in ``executive_steps`` rows and
are read by :func:`app.executive.activities.run_step_activity` itself) plus the signal
flags (paused/cancelled/retry-requested/amended). None of that is the durable record —
Temporal's own event history makes it durable ACROSS a worker restart, but the OWNER-
FACING truth (a step's state, its evidence, the run's own state) is always the
``executive_runs``/``executive_steps`` rows, written by the activity, never invented here.

Dependency = the union of a step's ``precondition.step_done`` argument and every OTHER
step id referenced by its own ``inputs`` (module docstring's own reasoning: a planner may
declare only one as an explicit precondition while still reading the other's output, and
scheduling must respect BOTH or a step could start before something it reads from has
finished — the exact fan-out gap a graph with two independent steps feeding one synthesis
step exposes).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.executive.activities import run_step_activity, settle_crashed_step_activity
    from app.executive.refs import parse_reference
    from app.executive.spec import MAX_RUN_WALL_CLOCK_S, MAX_TIMEOUT_S

#: spec §3: "fanning out independent steps (<= 3 concurrent)".
MAX_CONCURRENT_STEPS = 3


def _step_dependencies(step: dict[str, Any]) -> set[str]:
    deps: set[str] = set()
    precondition = step.get("precondition") or {}
    if precondition.get("check") == "step_done" and precondition.get("arg"):
        deps.add(str(precondition["arg"]))
    for value in (step.get("inputs") or {}).values():
        if not isinstance(value, str):
            continue
        ref = parse_reference(value)
        if ref is not None:
            deps.add(ref[0])
    return deps


def _crash_reason(exc: BaseException) -> str:
    """The most specific honest description of why a step's activity died.

    Temporal wraps whatever an activity raised in an ``ActivityError`` whose own str is
    "Activity task failed" - true, and useless on a receipt the owner reads. What actually
    happened is at the end of the cause chain: an ``ApplicationError`` carrying the
    original exception's class name in its ``type``, or a timeout/cancellation of its own.
    So walk to the root and name THAT, which is the difference between "the step did not
    return" and "ValueError: something nobody classified".
    """
    root = exc
    for _ in range(5):  # bounded: a malformed chain must not spin the workflow
        if root.__cause__ is None:
            break
        root = root.__cause__
    declared = getattr(root, "type", None)
    name = declared if isinstance(declared, str) and declared else type(root).__name__
    return f"{name}: {root}"[:400]


def _retry_policy(step: dict[str, Any]) -> RetryPolicy:
    retry = step.get("retry") or {}
    max_attempts = int(retry.get("max_attempts") or 1)
    backoff_s = float(retry.get("backoff_s") or 1.0)
    return RetryPolicy(
        initial_interval=timedelta(seconds=max(1.0, backoff_s)),
        maximum_interval=timedelta(seconds=max(1.0, backoff_s)),
        maximum_attempts=max_attempts,
        # A step's own ``retry.only_on`` is enforced by the ACTIVITY, not by this policy
        # (app.executive.activities._finalize: ``failed`` (non-retryable) vs.
        # ``failed_recoverable`` (retryable) is decided from the step's error_class
        # against its own only_on list). Temporal's automatic retry only ever fires on an
        # ACTIVITY-level exception, and ``run_step_activity`` never raises one for a
        # classified step failure (module docstring of activities.py: it returns a
        # result dict either way) — so this policy governs only genuine ORCHESTRATION
        # failures (the activity itself crashing / timing out at the Temporal layer),
        # which is exactly what "retries <= 3 with backoff <= 60s" is measuring against.
    )


@dataclass
class ExecutiveRunRequest:
    run_id: str
    #: The validated ``TaskGraph``, as ``TaskGraph.model_dump(mode="json")`` — a plain
    #: dict, never re-parsed into pydantic inside the workflow sandbox (module
    #: docstring: the workflow only needs the dependency SHAPE, not the full spec).
    graph_json: dict[str, Any]


@dataclass
class _StepView:
    step_id: str
    deps: set[str]
    timeout_s: int


@workflow.defn
class ExecutiveWorkflow:
    def __init__(self) -> None:
        self._paused = False
        self._cancelled = False
        self._retry_requests: set[str] = set()
        self._amendments: list[dict[str, Any]] = []
        self._current_step: str | None = None
        self._status_note = "planlandı"

    # --------------------------------------------------------------------- signals

    @workflow.signal
    def pause(self) -> None:
        self._paused = True

    @workflow.signal
    def resume(self) -> None:
        self._paused = False

    @workflow.signal
    def cancel(self) -> None:
        self._cancelled = True

    @workflow.signal
    def retry_step(self, step_id: str) -> None:
        self._retry_requests.add(step_id)

    @workflow.signal
    def amend(self, step: dict[str, Any]) -> None:
        """A step ALREADY validated and persisted by ``app.executive.service.amend_run``
        before this signal was sent (module docstring) — the workflow only learns its
        scheduling shape (id, deps, timeout), never re-validates the graph itself."""
        self._amendments.append(step)

    # ---------------------------------------------------------------------- queries

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {
            "current_step": self._current_step,
            "paused": self._paused,
            "cancelled": self._cancelled,
            "note": self._status_note,
        }

    @workflow.query
    def explain(self) -> str:
        if self._cancelled:
            return "İptal ediliyor efendim."
        if self._paused:
            return f"Duraklatıldı: {self._status_note}"
        if self._current_step:
            return f"Şu an {self._current_step} adımını yürütüyorum: {self._status_note}"
        return self._status_note

    # ------------------------------------------------------------------------- run

    @workflow.run
    async def run(self, request: ExecutiveRunRequest) -> dict[str, Any]:
        steps: dict[str, _StepView] = {}
        for raw in request.graph_json.get("steps") or []:
            sid = str(raw["id"])
            steps[sid] = _StepView(
                step_id=sid,
                deps=_step_dependencies(raw),
                timeout_s=min(int(raw.get("timeout_s") or 60), MAX_TIMEOUT_S),
            )
        raw_by_id = {str(r["id"]): r for r in (request.graph_json.get("steps") or [])}

        settled: set[str] = set()  # every step that reached a TERMINAL state
        # spec §4: <= 60 min wall clock is this workflow's own natural lifetime — an
        # owner may still say "tekrar dene" or "sunumu da ekle" long after the ready
        # steps ran out (a `partial` run's own DB row already reflects that outcome via
        # app.executive.activities._recompute_run_progress the moment its steps settle;
        # this workflow staying open a while longer is what lets a LATER retry/amend
        # still reach it — it is not what determines when the OWNER sees a result).
        deadline = workflow.now() + timedelta(seconds=MAX_RUN_WALL_CLOCK_S)

        while True:
            if self._amendments:
                for raw in self._amendments:
                    sid = str(raw["id"])
                    if sid in steps:
                        continue  # already applied (a redelivered/duplicate signal)
                    steps[sid] = _StepView(
                        step_id=sid,
                        deps=_step_dependencies(raw),
                        timeout_s=min(int(raw.get("timeout_s") or 60), MAX_TIMEOUT_S),
                    )
                    raw_by_id[sid] = raw
                self._amendments = []

            if self._cancelled:
                break

            # pause: "finishes the running activity, starts none" (spec §3) — checked
            # only at a BATCH BOUNDARY, never mid-batch, so this wait never interrupts an
            # activity already in flight.
            if self._paused:
                self._status_note = "duraklatıldı, devam bekleniyor"
                await workflow.wait_condition(lambda: not self._paused or self._cancelled)
                if self._cancelled:
                    break

            # A retried id is scheduled REGARDLESS of dep state (the owner asked for it
            # explicitly) — if its dependency truly never finished, the activity's own
            # precondition check (app.executive.activities._precondition_satisfied)
            # marks it `skipped` with the honest reason, rather than this loop silently
            # dropping the request.
            #
            # A dependency is "ready to build on" once it reaches ANY terminal state —
            # settled, not only verified/skipped. A step scheduled after a FAILED
            # dependency is exactly how spec §3's "the synthesis still runs over what
            # exists" becomes true: the activity's own input resolution
            # (app.executive.activities._resolve_inputs) hands a failed dependency's
            # evidence through as None, and the step's own handler (or precondition
            # check) decides what that means — never this loop. Gating on
            # verified-only here left every step downstream of a single failure stuck
            # PENDING forever (found while writing this workflow's own Temporal tests):
            # synthesis never ran, the run never reached `partial`, and it hung until
            # the wall-clock bound.
            retry_now = {sid for sid in self._retry_requests if sid in steps}
            self._retry_requests -= retry_now
            ready = {
                sid for sid, view in steps.items() if sid not in settled and view.deps <= settled
            } | retry_now
            if not ready:
                remaining = deadline - workflow.now()
                if remaining.total_seconds() <= 0:
                    break
                self._status_note = "bekleniyor: yeni bir istek (tekrar dene / ekle) ya da iptal"
                try:
                    await workflow.wait_condition(
                        lambda: (
                            self._cancelled or bool(self._retry_requests) or bool(self._amendments)
                        ),
                        timeout=remaining,
                    )
                except TimeoutError:
                    break
                continue

            batch = sorted(ready)[:MAX_CONCURRENT_STEPS]
            leftover_retry = retry_now - set(batch)
            self._retry_requests |= leftover_retry
            self._current_step = batch[0]
            self._status_note = f"{len(batch)} adım çalışıyor"

            # The per-step outcome (verified/failed/skipped/...) is not this loop's to
            # interpret — it is already durably the STEP ROW's own state, written by
            # the activity; this workflow only needs to know the step SETTLED, so
            # something else can now be scheduled.
            # `return_exceptions=True` so ONE step's activity failure cannot end the
            # run. It could, before the M26 security review: an unclassified exception
            # escaped `run_step_activity`, propagated out of this gather, and terminated
            # the whole execution - leaving the row saying `running` forever, holding one
            # of the two run slots, and telling the owner the work was still going. The
            # activity now classifies those itself (`ERROR_INTERNAL`); this is the second
            # wall, for an activity that dies for a reason neither layer predicted. The
            # step's own row is settled by `_settle_crashed_step` so the run still reaches
            # an honest terminal state instead of waiting on a step nobody will finish.
            outcomes = await asyncio.gather(
                *(self._run_one(request.run_id, sid, steps[sid].timeout_s) for sid in batch),
                return_exceptions=True,
            )
            for sid, outcome in zip(batch, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    await workflow.execute_activity(
                        settle_crashed_step_activity,
                        args=[request.run_id, sid, _crash_reason(outcome)],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
            for sid in batch:
                settled.add(sid)
            self._current_step = None

        # A step never started when the run was cancelled is left `pending` HERE (this
        # workflow writes no rows at all — module docstring) and marked `cancelled`,
        # and any VERIFIED step's compensation is run, by
        # ``app.executive.service.finalize_cancelled_run`` immediately after this
        # workflow returns (a plain DB(+closed, idempotent device action) sweep over the
        # run's own rows — never a further Temporal round trip the owner's "stop" would
        # then be waiting on).
        return {
            "run_id": request.run_id,
            "settled": sorted(settled),
            "cancelled": self._cancelled,
        }

    async def _run_one(self, run_id: str, step_id: str, timeout_s: int) -> str:
        # maximum_attempts=1 is deliberate (app.executive.activities.run_step_activity's
        # own docstring): the step's OWN retry policy (spec §1's per-step data, not one
        # policy this workflow could apply uniformly) is already applied INSIDE that one
        # activity invocation. heartbeat_timeout lets the activity's internal backoff
        # loop prove liveness between attempts rather than the whole call looking stuck.
        result = await workflow.execute_activity(
            run_step_activity,
            args=[run_id, step_id],
            start_to_close_timeout=timedelta(seconds=timeout_s),
            heartbeat_timeout=timedelta(seconds=min(timeout_s, 90)),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        return str(result.get("state"))


__all__ = ["ExecutiveRunRequest", "ExecutiveWorkflow", "MAX_CONCURRENT_STEPS"]
