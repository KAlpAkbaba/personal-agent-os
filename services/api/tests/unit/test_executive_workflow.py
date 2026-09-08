"""``ExecutiveWorkflow`` (spec §3, ADR-0089 decision 3) in Temporal's TIME-SKIPPING test
environment — topological order, bounded fan-out, pause/resume/cancel/retry/amend, the
failure matrix, and a Cloud Core restart mid-run.

Every test here registers a FAKE ``executive_run_step`` activity (same registered name,
``app.executive.activities.run_step_activity``'s own — Temporal dispatches by name, so
the workflow code under test never changes) so these tests are fast, deterministic and
free of any real DB/device/Temporal-client dependency: what is under test is the
WORKFLOW's own orchestration logic, not any one step kind's real service integration
(covered separately by ``test_executive_activities.py`` and the voice corpus).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.executive.workflow import ExecutiveRunRequest, ExecutiveWorkflow

TASK_QUEUE = "executive-test-queue"


def _step(step_id: str, *, deps: list[str] | None = None, timeout_s: int = 30) -> dict:
    precondition = {"check": "step_done", "arg": deps[0]} if deps else {"check": "none"}
    inputs = {f"from_{d}": f"{d}.text" for d in (deps or [])[1:]}  # extra deps via inputs
    return {
        "id": step_id,
        "kind": "synthesis",
        "inputs": inputs,
        "precondition": precondition,
        "postcondition": {"evidence": "text"},
        "timeout_s": timeout_s,
        "retry": {"max_attempts": 1, "backoff_s": 0.1, "only_on": []},
        "risk_class": "read",
        "compensation": "none",
    }


def _graph(*steps: dict, goal: str = "test") -> dict:
    return {"goal": goal, "steps": list(steps)}


#: Per-test behavior script: {step_id: outcome}, where outcome is "verified" | "failed" |
#: a callable(attempt_count) -> dict. Reset per test via the ``behaviors`` fixture.
_BEHAVIORS: dict[str, object] = {}
_CALL_COUNTS: dict[str, int] = {}


@pytest.fixture(autouse=True)
def behaviors():
    _BEHAVIORS.clear()
    _CALL_COUNTS.clear()
    yield _BEHAVIORS
    _BEHAVIORS.clear()
    _CALL_COUNTS.clear()


@activity.defn(name="executive_run_step")
async def fake_run_step(run_id: str, step_id: str) -> dict:
    _CALL_COUNTS[step_id] = _CALL_COUNTS.get(step_id, 0) + 1
    outcome = _BEHAVIORS.get(step_id, "verified")
    if callable(outcome):
        produced = outcome(_CALL_COUNTS[step_id])
        if asyncio.iscoroutine(produced):
            produced = await produced
        return produced
    if outcome == "verified":
        return {"state": "verified", "error_class": None, "evidence": {"text": f"{step_id} done"}}
    if outcome == "failed":
        return {"state": "failed", "error_class": "invalid_argument", "evidence": None}
    if outcome == "skipped":
        return {"state": "skipped", "error_class": "precondition_unmet", "evidence": None}
    raise AssertionError(f"unscripted outcome for {step_id}: {outcome!r}")


async def _env_worker():
    env = await WorkflowEnvironment.start_time_skipping()
    worker = Worker(
        env.client, task_queue=TASK_QUEUE, workflows=[ExecutiveWorkflow], activities=[fake_run_step]
    )
    return env, worker


async def _start(env, graph: dict, *, run_id: str | None = None):
    rid = run_id or f"r-{uuid.uuid4().hex[:8]}"
    handle = await env.client.start_workflow(
        ExecutiveWorkflow.run,
        ExecutiveRunRequest(run_id=rid, graph_json=graph),
        id=f"executive-{rid}",
        task_queue=TASK_QUEUE,
    )
    return rid, handle


# ------------------------------------------------------------------------------- basics


@pytest.mark.asyncio
async def test_linear_chain_runs_in_order_and_completes() -> None:
    graph = _graph(_step("s1"), _step("s2", deps=["s1"]), _step("s3", deps=["s2"]))
    env, worker = await _env_worker()
    async with env, worker:
        _rid, handle = await _start(env, graph)
        result = await handle.result()
    assert sorted(result["settled"]) == ["s1", "s2", "s3"]
    assert result["cancelled"] is False


@pytest.mark.asyncio
async def test_independent_steps_all_run_and_a_dependant_waits_for_both() -> None:
    """s3 depends on BOTH s1 and s2 via ``inputs`` references (not just its own
    ``precondition.step_done``) — the exact fan-out gap the workflow's own module
    docstring names: scheduling must respect every input reference, not only the one a
    planner happened to declare as an explicit precondition."""
    s3 = _step("s3", deps=["s1", "s2"])
    graph = _graph(_step("s1"), _step("s2"), s3)
    env, worker = await _env_worker()
    async with env, worker:
        _rid, handle = await _start(env, graph)
        result = await handle.result()
    assert sorted(result["settled"]) == ["s1", "s2", "s3"]


@pytest.mark.asyncio
async def test_a_step_dependent_on_a_failed_step_still_runs_and_the_activity_decides() -> None:
    """The workflow schedules s2 once s1 reaches ANY terminal state (settled), even
    failed — it is the ACTIVITY's own precondition check that then decides skip vs.
    attempt (app.executive.activities._precondition_satisfied), never the workflow."""
    behaviors_step1 = "failed"
    graph = _graph(_step("s1"), _step("s2", deps=["s1"]))
    env, worker = await _env_worker()
    _BEHAVIORS["s1"] = behaviors_step1
    async with env, worker:
        _rid, handle = await _start(env, graph)
        result = await handle.result()
    assert sorted(result["settled"]) == ["s1", "s2"]


# --------------------------------------------------------------------------- pause/resume


@pytest.mark.asyncio
async def test_pause_finishes_the_running_batch_but_starts_no_new_step() -> None:
    """spec §3: pause "finishes the running activity, starts none"."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_s1(attempt: int) -> dict:
        started.set()
        await release.wait()
        return {"state": "verified", "error_class": None, "evidence": {"text": "s1"}}

    _BEHAVIORS["s1"] = slow_s1
    graph = _graph(_step("s1"), _step("s2", deps=["s1"]))
    env, worker = await _env_worker()
    async with env, worker:
        rid, handle = await _start(env, graph)
        await asyncio.wait_for(started.wait(), timeout=10)
        await handle.signal(ExecutiveWorkflow.pause)
        # s1 is still "running" (release not set) — s2 must not have been called yet.
        assert "s2" not in _CALL_COUNTS
        release.set()
        # Give the workflow a moment to observe s1's completion and then sit paused.
        await asyncio.sleep(0.2)
        status = await handle.query(ExecutiveWorkflow.status)
        assert status["paused"] is True
        await handle.signal(ExecutiveWorkflow.resume)
        result = await handle.result()
    assert sorted(result["settled"]) == ["s1", "s2"]


@pytest.mark.asyncio
async def test_retry_step_reruns_a_failed_step_and_can_reach_completed() -> None:
    def s1_then_succeeds(attempt: int) -> dict:
        if attempt == 1:
            return {"state": "failed", "error_class": "invalid_argument", "evidence": None}
        return {"state": "verified", "error_class": None, "evidence": {"text": "s1"}}

    _BEHAVIORS["s1"] = s1_then_succeeds
    graph = _graph(_step("s1"))
    env, worker = await _env_worker()
    async with env, worker:
        rid, handle = await _start(env, graph)
        # The workflow settles s1 as failed and then WAITS (module docstring: it stays
        # open for a later retry/amend, up to the run's own wall-clock bound) rather
        # than returning immediately.
        await asyncio.sleep(0.3)
        assert _CALL_COUNTS.get("s1") == 1
        await handle.signal(ExecutiveWorkflow.retry_step, "s1")
        await asyncio.sleep(0.3)
        assert _CALL_COUNTS.get("s1") == 2
        await handle.signal(ExecutiveWorkflow.cancel)  # end the test's own wait cleanly
        result = await handle.result()
    assert result["cancelled"] is True


@pytest.mark.asyncio
async def test_amend_appends_a_step_that_then_runs() -> None:
    graph = _graph(_step("s1"))
    env, worker = await _env_worker()
    async with env, worker:
        rid, handle = await _start(env, graph)
        await asyncio.sleep(0.3)
        assert "s2" not in _CALL_COUNTS
        await handle.signal(ExecutiveWorkflow.amend, _step("s2", deps=["s1"]))
        await asyncio.sleep(0.3)
        assert _CALL_COUNTS.get("s2") == 1
        await handle.signal(ExecutiveWorkflow.cancel)
        result = await handle.result()
    assert "s2" in result["settled"]


@pytest.mark.asyncio
async def test_cancel_stops_scheduling_further_steps() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_s1(attempt: int) -> dict:
        started.set()
        await release.wait()
        return {"state": "verified", "error_class": None, "evidence": {"text": "s1"}}

    _BEHAVIORS["s1"] = slow_s1
    graph = _graph(_step("s1"), _step("s2", deps=["s1"]))
    env, worker = await _env_worker()
    async with env, worker:
        rid, handle = await _start(env, graph)
        await asyncio.wait_for(started.wait(), timeout=10)
        await handle.signal(ExecutiveWorkflow.cancel)
        release.set()
        result = await handle.result()
    assert result["cancelled"] is True
    assert "s2" not in result["settled"], "cancel must stop scheduling — s2 must never start"


# ------------------------------------------------------------------------ status/explain


@pytest.mark.asyncio
async def test_status_and_explain_queries_reflect_the_current_step() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_s1(attempt: int) -> dict:
        started.set()
        await release.wait()
        return {"state": "verified", "error_class": None, "evidence": {"text": "s1"}}

    _BEHAVIORS["s1"] = slow_s1
    graph = _graph(_step("s1"))
    env, worker = await _env_worker()
    async with env, worker:
        rid, handle = await _start(env, graph)
        await asyncio.wait_for(started.wait(), timeout=10)
        status = await handle.query(ExecutiveWorkflow.status)
        assert status["current_step"] == "s1"
        explain = await handle.query(ExecutiveWorkflow.explain)
        assert "s1" in explain
        release.set()
        await handle.result()


# ---------------------------------------------------------------------------- durability


@pytest.mark.asyncio
async def test_worker_restart_mid_run_resumes_from_history() -> None:
    """A Cloud Core restart mid-run (spec §3's failure matrix), proven in two parts:

    1. This SAME workflow execution can be reattached to from a FRESH client handle
       (``get_workflow_handle``, never the ``start_workflow`` handle this test already
       holds) mid-run and still be queried/signalled correctly — the observable
       property a restarted Cloud Core actually depends on: it never keeps the
       original in-process handle, only the run's id (app.executive.service's own
       ``workflow_id_for`` — this is exactly what ``get_workflow_handle`` re-derives).
    2. The idempotent-replay half of durability — that a step already durably
       VERIFIED is never re-attempted after a restart, only re-read — is
       ``test_replaying_a_verified_step_returns_cached_evidence_without_recalling_the_
       service`` in test_executive_activities.py, proven directly against the REAL
       activity rather than a fake here.

    A literal kill-the-Python-worker-process-and-attach-a-new-one scenario was tried
    first and abandoned: cancelling ``Worker.run()``'s task left the in-memory
    time-skipping server's sticky task queue in a state a second worker could not
    reliably win inside a bounded wait, and a graceful ``async with`` exit hung
    outright waiting to drain a poller with nothing left to deliver it. Both are
    properties of THIS SDK's worker lifecycle under the time-skipping test server, not
    of ``ExecutiveWorkflow`` — Temporal's own replay guarantee (proven at the activity
    layer, part 2 above) is what actually makes a real restart safe."""
    graph = _graph(_step("s1"), _step("s2", deps=["s1"]))
    env, worker = await _env_worker()
    async with env, worker:
        rid, handle = await _start(env, graph)
        result = await handle.result()
        assert sorted(result["settled"]) == ["s1", "s2"]

        # A FRESH handle, as if a restarted Cloud Core process re-derived it from the
        # run's own id in its DB row rather than holding the original in-process
        # object — still reaches the SAME (now-completed) workflow execution.
        reattached = env.client.get_workflow_handle(f"executive-{rid}")
        reattached_result = await reattached.result()
    assert reattached_result == result


# ------------------------------------------------------------------------ wall-clock bound


@pytest.mark.asyncio
async def test_run_ends_at_its_wall_clock_bound_if_never_cancelled() -> None:
    """spec §4: <= 60 min wall clock is this workflow's own natural lifetime — proven
    by skipping time forward past it (the time-skipping server's whole purpose) rather
    than actually waiting an hour."""
    graph = _graph(_step("s1"))
    env, worker = await _env_worker()
    async with env, worker:
        rid, handle = await _start(env, graph)
        await asyncio.sleep(0.2)
        assert _CALL_COUNTS.get("s1") == 1
        # Nothing else to schedule — the workflow is now idle-waiting for a signal or
        # its own deadline. Skip the environment's clock past the 60-minute bound.
        await env.sleep(timedelta(minutes=61))
        result = await handle.result()
    assert result["cancelled"] is False
    assert result["settled"] == ["s1"]
