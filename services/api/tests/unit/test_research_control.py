"""B31 req 203/204: a research paused and resumed - by voice and by REST - through ONE
durable function each, with the Temporal half a signal the workflow reads at its next
stage boundary.

The tool tests run the real handlers against the research tables; the route tests run the
real routes with the Temporal client patched (the same discipline as the cancel tests); the
workflow tests read the workflow's own source, because a Temporal workflow cannot be driven
without a server and a gate that is not at every stage boundary is a pause the owner would
not recognise.
"""

# ruff: noqa: F811 - the fixtures are imported from the suites that own them
from __future__ import annotations

import asyncio
import inspect
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.research import browser_workflow
from app.research import service as research_service
from app.research.models import STAGE_DISCOVERING, STAGES, ResearchRunRow
from app.security import step_up
from app.voice.intents import Intent, resolve_intent
from app.voice.realtime_sessions.tools import (
    ERROR_RESEARCH_ALREADY_PAUSED,
    ERROR_RESEARCH_NOT_PAUSED,
    ERROR_RESEARCH_NOTHING_RUNNING,
    research_cancel,
    research_pause,
    research_resume,
)
from tests.unit.test_daily_intent_tools import _ctx, _receipts, _run, research_db  # noqa: F401
from tests.unit.test_research_routes import (  # noqa: F401
    _enroll_online_device,
    _patched_temporal_client,
    client,
    engine,
)


class _Runtime:
    from app.config import Settings

    settings = Settings(_env_file=None)


# ----------------------------------------------------------------- the vocabulary


def test_the_tools_are_declared_and_governed() -> None:
    assert step_up.tier_of("research.pause") == step_up.TIER_SENSITIVE
    assert step_up.tier_of("research.resume") == step_up.TIER_SENSITIVE
    assert resolve_intent("Araştırmayı duraklat.").intent is Intent.RESEARCH_PAUSE
    assert resolve_intent("Araştırmayı beklet.").intent is Intent.RESEARCH_PAUSE
    assert resolve_intent("Araştırmaya devam et.").intent is Intent.RESEARCH_RESUME
    assert resolve_intent("Araştırmayı sürdür.").intent is Intent.RESEARCH_RESUME
    # The neighbours keep their meaning: "durdur" cancels, a bare "devam" is not ours.
    assert resolve_intent("Araştırmayı durdur.").intent is Intent.RESEARCH_CANCEL
    assert resolve_intent("Devam et.").intent is not Intent.RESEARCH_RESUME
    assert resolve_intent("Araştırmayı duraklatma.").intent is not Intent.RESEARCH_PAUSE


def test_paused_is_a_flag_never_a_stage() -> None:
    """A paused run keeps its stage: it is still in flight to active_research() and to
    the orphan sweep, and it is still where it was when the owner says devam."""
    assert "paused" not in STAGES
    assert not hasattr(research_service, "STAGE_PAUSED")


# ----------------------------------------------------------------------- service


def test_pause_then_resume_keep_the_stage_and_account_the_held_time(
    research_db: Session,
) -> None:
    task = _run(research_db, stage=STAGE_DISCOVERING)
    assert research_service.mark_research_paused(research_db, task.id) is True
    run = research_db.get(ResearchRunRow, task.id)
    assert run.stage == STAGE_DISCOVERING
    assert research_service.research_is_paused(run)
    assert research_service.active_research(research_db).task_id == task.id
    # Twice is a no-op, never a second record.
    assert research_service.mark_research_paused(research_db, task.id) is False
    assert research_service.mark_research_resumed(research_db, task.id) is True
    run = research_db.get(ResearchRunRow, task.id)
    assert not research_service.research_is_paused(run)
    assert run.progress_json["paused_seconds"] >= 0.0
    assert [e.get("paused") for e in run.events_json if "paused" in e] == [True, False]
    assert research_service.mark_research_resumed(research_db, task.id) is False


def test_a_finished_research_cannot_be_paused(research_db: Session) -> None:
    from app.research.models import STAGE_READY

    task = _run(research_db, stage=STAGE_READY)
    assert research_service.mark_research_paused(research_db, task.id) is False


# ------------------------------------------------------------------------- tools


def _signal_followup(ctx, task) -> AsyncMock:
    assert len(ctx.followups) == 1
    handle = AsyncMock()
    client_ = AsyncMock()
    client_.get_workflow_handle = (
        lambda workflow_id: handle if workflow_id == task.workflow_id else None
    )
    with patch("app.research.service.Client.connect", AsyncMock(return_value=client_)):
        asyncio.run(ctx.followups[0]())
    return handle


def test_pausing_the_running_research_is_recorded_and_signalled(research_db: Session) -> None:
    task = _run(research_db, stage=STAGE_DISCOVERING)
    ctx = _ctx(research_db, {"artifacts_runtime": _Runtime()})
    receipt = research_pause(ctx, {})
    assert receipt["execution_status"] == "executed", receipt
    assert receipt["terminal_status"] == "verified"
    assert receipt["speech"].startswith("Araştırmayı duraklattım")
    run = research_db.get(ResearchRunRow, task.id)
    assert research_service.research_is_paused(run)
    assert run.stage == STAGE_DISCOVERING
    events = {e.event_type for e in _receipts(research_db)}
    assert {"action.receipt", "research.paused"} <= events
    handle = _signal_followup(ctx, task)
    handle.signal.assert_awaited_once()
    signalled = handle.signal.await_args.args[0]
    assert signalled is browser_workflow.BrowserResearchWorkflow.pause


def test_resuming_a_paused_research_is_recorded_and_signalled(research_db: Session) -> None:
    task = _run(research_db, stage=STAGE_DISCOVERING)
    research_pause(_ctx(research_db, {"artifacts_runtime": _Runtime()}), {})
    ctx = _ctx(research_db, {"artifacts_runtime": _Runtime()})
    receipt = research_resume(ctx, {})
    assert receipt["execution_status"] == "executed", receipt
    assert receipt["speech"] == "Araştırmaya devam ediyorum efendim."
    assert not research_service.research_is_paused(research_db.get(ResearchRunRow, task.id))
    handle = _signal_followup(ctx, task)
    assert handle.signal.await_args.args[0] is browser_workflow.BrowserResearchWorkflow.resume
    events = {e.event_type for e in _receipts(research_db)}
    assert "research.resumed" in events


def test_pausing_twice_and_resuming_an_unpaused_run_are_refusals(research_db: Session) -> None:
    _run(research_db, stage=STAGE_DISCOVERING)
    ctx = _ctx(research_db, {"artifacts_runtime": _Runtime()})
    assert research_resume(ctx, {})["error_class"] == ERROR_RESEARCH_NOT_PAUSED
    assert research_pause(ctx, {})["execution_status"] == "executed"
    again = research_pause(_ctx(research_db, {"artifacts_runtime": _Runtime()}), {})
    assert again["execution_status"] == "refused"
    assert again["error_class"] == ERROR_RESEARCH_ALREADY_PAUSED
    assert "zaten" in again["speech"]


def test_nothing_running_is_a_refusal_for_both(research_db: Session) -> None:
    for tool in (research_pause, research_resume):
        receipt = tool(_ctx(research_db), {})
        assert receipt["execution_status"] == "refused"
        assert receipt["error_class"] == ERROR_RESEARCH_NOTHING_RUNNING


def test_a_paused_research_can_still_be_cancelled(research_db: Session) -> None:
    from app.research.models import STAGE_CANCELLED

    task = _run(research_db, stage=STAGE_DISCOVERING)
    research_pause(_ctx(research_db, {"artifacts_runtime": _Runtime()}), {})
    receipt = research_cancel(_ctx(research_db, {"artifacts_runtime": _Runtime()}), {})
    assert receipt["execution_status"] == "executed"
    assert research_db.get(ResearchRunRow, task.id).stage == STAGE_CANCELLED


def test_the_rest_routes_and_the_tools_share_one_function_each() -> None:
    from pathlib import Path

    routes = Path(research_service.__file__).with_name("routes.py").read_text("utf-8")
    tools = Path(research_pause.__code__.co_filename).read_text("utf-8")
    for name in ("mark_research_paused", "mark_research_resumed"):
        assert f"research_service.{name}(" in routes, name
        assert f"research_service.{name}" in tools, name


# ------------------------------------------------------------------------ routes


def test_pause_and_resume_routes_record_and_signal(client: TestClient) -> None:
    _enroll_online_device(client)
    with _patched_temporal_client() as connect:
        started = client.post("/v1/research", json={"input": "konu"})
        task_id = started.json()["task_id"]
        paused = client.post(f"/v1/research/{task_id}/pause")
        assert paused.status_code == 200, paused.text
        assert paused.json()["status"] == "paused"
        detail = client.get(f"/v1/research/{task_id}").json()
        assert detail["progress"]["paused"] is True
        assert detail["stage"] != "paused", "paused is a flag, never a stage"
        again = client.post(f"/v1/research/{task_id}/pause")
        assert again.status_code == 409
        resumed = client.post(f"/v1/research/{task_id}/resume")
        assert resumed.status_code == 200, resumed.text
        assert client.get(f"/v1/research/{task_id}").json()["progress"]["paused"] is False
        assert client.post(f"/v1/research/{task_id}/resume").status_code == 409
        fake_client = connect.return_value
        handle = fake_client.get_workflow_handle()
        assert handle.signal.await_count == 2
        assert client.post(f"/v1/research/{uuid.uuid4()}/pause").status_code == 404


# ---------------------------------------------------------------------- workflow


def test_the_workflow_declares_the_signals_and_gates_every_stage_boundary() -> None:
    wf = browser_workflow.BrowserResearchWorkflow
    assert callable(wf.pause) and callable(wf.resume)
    assert wf().paused() is False
    source = inspect.getsource(wf.run)
    # Before every discovery query, before the first fetch wave, at the top of every
    # later wave, and before synthesis: four gates, so a pause is felt within one activity.
    assert source.count("await self._gate()") >= 4, source.count("await self._gate()")
    assert "_discover_with_handoff(" in source.split("await self._gate()", 1)[1]
    assert "self._elapsed_s(run_start)" in source, "the budget must exclude the held time"
    assert "(workflow.now() - run_start).total_seconds()" not in source
    gate = inspect.getsource(wf._gate)
    assert "workflow.wait_condition(lambda: not self._paused)" in gate


def test_the_gate_holds_and_accounts_time_in_a_fake_workflow_clock() -> None:
    """The gate's own arithmetic, with workflow.now/wait_condition replaced by a clock
    that advances when the condition is waited on."""
    wf = browser_workflow.BrowserResearchWorkflow()
    clock = {"t": 0.0}

    class _Now:
        def __init__(self, t: float) -> None:
            self.t = t

        def __sub__(self, other: _Now):  # type: ignore[override]
            from datetime import timedelta

            return timedelta(seconds=self.t - other.t)

    async def wait_condition(predicate):
        # The owner resumes 12.5 s later.
        clock["t"] += 12.5
        wf.resume()
        assert predicate()

    with (
        patch.object(browser_workflow.workflow, "now", lambda: _Now(clock["t"])),
        patch.object(browser_workflow.workflow, "wait_condition", wait_condition),
    ):
        asyncio.run(wf._gate())  # not paused: no wait, no time
        assert wf.paused_seconds() == 0.0
        wf.pause()
        asyncio.run(wf._gate())
        assert wf.paused_seconds() == pytest.approx(12.5)
        assert wf.paused() is False
        assert wf._elapsed_s(_Now(0.0)) == pytest.approx(0.0)
