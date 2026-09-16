"""B38 - the general execution planner (req 536-538, 544, 546, 549-557).

Measured before: the planner knew three shapes and refused everything else; the model seam
raised NotImplementedError; a graph could carry only step_done/none preconditions and a
retry policy; no step waited for the owner; the research shape ran its document and its
slides one after the other; nothing looped; explain named the current step's kind and
nothing of why it was in the plan; the REST route took a directive and never a graph.

Every seam is proven by executing it: the spec and validator on the new fields, the
activity's precondition checks on real rows, the bounded loop through
``run_step_activity`` with a scripted dispatch, the approval gate in the real Temporal
test environment, the model planner through a scripted model, and the routes through the
real application object with Temporal faked at the client seam.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.executive import activities, model_planner
from app.executive.graph import GraphValidationError, validate_graph
from app.executive.models import (
    STATE_RUNNING,
    STEP_STATE_FAILED,
    STEP_STATE_PENDING,
    STEP_STATE_VERIFIED,
    ExecutiveRunRow,
    ExecutiveStepRow,
)
from app.executive.planner import (
    ClaudeExecutivePlanner,
    PlanningClarificationNeeded,
    RuleBasedExecutivePlanner,
)
from app.executive.spec import (
    DEFAULT_TIMEOUT_S_BY_KIND,
    EVIDENCE_TEXT,
    PLANNER_MODEL,
    PLANNER_OWNER,
    PLANNER_RULE,
    PRECONDITION_OWNER_APPROVAL,
    PRECONDITION_STEP_FAILED,
    PRECONDITION_STEP_VERIFIED,
    RISK_READ,
    STEP_KIND_DOCUMENTS_FIND,
    STEP_KIND_PROFILES,
    STEP_KIND_RESEARCH_SYNTHESIZE,
    STEP_KIND_SYNTHESIS,
    Postcondition,
    Precondition,
    Repeat,
    Step,
    TaskGraph,
)
from app.executive.workflow import ExecutiveRunRequest, ExecutiveWorkflow
from app.ledger.models import ActivityEventRow
from app.object_store import InMemoryObjectStore
from app.uistate.publisher import UiStatePublisher, set_publisher
from tests.voice_corpus.harness import build_harness

TABLES = [ExecutiveRunRow.__table__, ExecutiveStepRow.__table__, ActivityEventRow.__table__]

_RESEARCH = (
    "Son üç gündeki AI gelişmelerini araştır, bana etkisini çıkar, Word raporu ve sunum hazırla."
)


def _text_step(step_id: str, kind: str = STEP_KIND_RESEARCH_SYNTHESIZE, **overrides: Any) -> Step:
    profile = STEP_KIND_PROFILES[kind]
    base: dict[str, Any] = {
        "id": step_id,
        "kind": kind,
        "postcondition": Postcondition(
            evidence=profile.evidence_kind, min=overrides.pop("min", None)
        ),
        "risk_class": profile.risk_class,
        "compensation": profile.compensation,
    }
    base.update(overrides)
    return Step(**base)


# ------------------------------------------------------------- the spec and the validator


def test_the_new_fields_are_bounded_and_the_validator_reads_them() -> None:
    fallback = _text_step(
        "s2",
        precondition=Precondition(check=PRECONDITION_STEP_FAILED, arg="s1"),
        rationale="Yedek dal.",
    )
    strict = _text_step("s3", precondition=Precondition(check=PRECONDITION_STEP_VERIFIED, arg="s1"))
    gated = _text_step("s4", precondition=Precondition(check=PRECONDITION_OWNER_APPROVAL))
    loop = _text_step("s5", repeat=Repeat(max_rounds=3), min=200)
    graph = TaskGraph(
        goal="test", steps=[_text_step("s1"), fallback, strict, gated, loop], planner=PLANNER_MODEL
    )
    validate_graph(graph)
    assert graph.step("s2").rationale == "Yedek dal."
    # A fallback on a step that comes LATER is a DAG violation, named.
    with pytest.raises(GraphValidationError, match="step_failed names 's9'"):
        validate_graph(
            TaskGraph(
                goal="t",
                steps=[
                    _text_step(
                        "s1", precondition=Precondition(check=PRECONDITION_STEP_FAILED, arg="s9")
                    )
                ],
            )
        )
    # A loop with nothing to reach is refused.
    with pytest.raises(
        GraphValidationError, match="repeat.max_rounds > 1 needs a postcondition.min"
    ):
        validate_graph(TaskGraph(goal="t", steps=[_text_step("s1", repeat=Repeat(max_rounds=2))]))
    with pytest.raises(GraphValidationError, match="unknown planner"):
        validate_graph(TaskGraph(goal="t", steps=[_text_step("s1")], planner="oracle"))
    with pytest.raises(ValueError):
        Repeat(max_rounds=4)
    with pytest.raises(ValueError):
        _text_step("s1", rationale="x" * 301)


def test_the_research_shape_runs_its_document_and_slides_side_by_side_with_reasons() -> None:
    graph = RuleBasedExecutivePlanner().plan(_RESEARCH)
    by_id = {s.id: s for s in graph.steps}
    # Req 553: both artifact steps depend on s2 only - siblings, not a chain.
    assert by_id["s3"].precondition.arg == "s2" and by_id["s4"].precondition.arg == "s2"
    assert by_id["s4"].precondition.arg != "s3"
    # Req 546: every step says why it is there.
    assert all(s.rationale for s in graph.steps)
    assert graph.planner is None  # the rule shapes tag nothing; the composite tags PLANNER_RULE


# ------------------------------------------------------------- the model planner (550-552)


def _proposal(**overrides: Any) -> dict[str, Any]:
    steps = [
        {
            "id": "s1",
            "kind": STEP_KIND_DOCUMENTS_FIND,
            "inputs": {"folder": "Belgeler"},
            "rationale": "Klasördeki dosyaları bulmak için.",
        },
        {
            "id": "s2",
            "kind": "documents.extract",
            "inputs": {"refs": "s1.document_refs", "question": "Toplam kaç?"},
            "precondition": {"check": "step_done", "arg": "s1"},
            "rationale": "Her dosyadan toplamı çıkarmak için.",
            "min": 1,
        },
        {
            "id": "s3",
            "kind": STEP_KIND_SYNTHESIS,
            "inputs": {"extract": "s2.text"},
            "precondition": {"check": "step_done", "arg": "s2"},
            "rationale": "Sonucu özetlemek için.",
        },
    ]
    return {"steps": steps, **overrides}


def test_a_scripted_proposal_becomes_a_validated_graph_with_the_fixed_fields_looked_up() -> None:
    model = model_planner.ScriptedPlannerModel(_proposal())
    graph = model_planner.ModelExecutivePlanner(model).plan("Belgelerdeki toplamları çıkar")
    assert graph.planner == PLANNER_MODEL
    assert [s.kind for s in graph.steps] == [
        STEP_KIND_DOCUMENTS_FIND,
        "documents.extract",
        STEP_KIND_SYNTHESIS,
    ]
    extract = graph.step("s2")
    # Risk class, compensation and evidence kind came from the profile, not the proposal.
    assert extract.risk_class == STEP_KIND_PROFILES["documents.extract"].risk_class
    assert extract.postcondition.evidence == STEP_KIND_PROFILES["documents.extract"].evidence_kind
    # A timeout the proposal omitted is the kind's default (556).
    assert extract.timeout_s == DEFAULT_TIMEOUT_S_BY_KIND["documents.extract"]
    assert model.asked == ["Belgelerdeki toplamları çıkar"]
    vocab = model_planner.vocabulary()
    assert set(vocab["kinds"]) == set(STEP_KIND_PROFILES)
    assert "owner_approval" in vocab["precondition_checks"]


@pytest.mark.parametrize(
    ("proposal", "reason"),
    [
        (
            {"steps": [{"id": "s1", "kind": "mail.send", "rationale": "x"}]},
            "bilmediğim bir adım türü",
        ),
        (
            {
                # s1 reads s2, which comes LATER: a DAG violation the validator names.
                "steps": [
                    {
                        "id": "s1",
                        "kind": STEP_KIND_SYNTHESIS,
                        "inputs": {"a": "s2.text"},
                        "rationale": "x",
                    },
                    {"id": "s2", "kind": STEP_KIND_SYNTHESIS, "rationale": "x"},
                ]
            },
            "kurallara uymadı",
        ),
        ({"steps": []}, "adım önermedi"),
        (
            {
                "steps": [
                    {
                        "id": "s1",
                        "kind": STEP_KIND_SYNTHESIS,
                        "repeat": {"max_rounds": 2},
                        "rationale": "x",
                    }
                ]
            },
            "kurallara uymadı",
        ),
        ("not a dict", "adım önermedi"),
    ],
)
def test_a_proposal_outside_the_vocabulary_or_the_rules_is_a_clarification(
    proposal, reason
) -> None:
    with pytest.raises(PlanningClarificationNeeded) as refused:
        model_planner.graph_from_proposal(proposal, "x")
    assert reason in refused.value.speech


def test_the_composite_plans_the_shapes_itself_and_asks_the_model_only_under_the_flag() -> None:
    model = model_planner.ScriptedPlannerModel(_proposal())
    off = model_planner.CompositeExecutivePlanner(
        model=model_planner.ModelExecutivePlanner(model), enabled=False
    )
    shaped = off.plan(_RESEARCH)
    assert shaped.planner == PLANNER_RULE and model.asked == []
    with pytest.raises(PlanningClarificationNeeded):
        off.plan("Belgelerdeki toplamları çıkar")
    assert model.asked == []
    on = model_planner.CompositeExecutivePlanner(
        model=model_planner.ModelExecutivePlanner(model), enabled=lambda: True
    )
    assert on.plan(_RESEARCH).planner == PLANNER_RULE
    dynamic = on.plan("Belgelerdeki toplamları çıkar")
    assert dynamic.planner == PLANNER_MODEL and model.asked == ["Belgelerdeki toplamları çıkar"]
    # The M26 seam is no longer inert: it takes a model, and still raises without one.
    with pytest.raises(NotImplementedError):
        ClaudeExecutivePlanner().plan("x")
    assert (
        ClaudeExecutivePlanner(model).plan("Belgelerdeki toplamları çıkar").planner == PLANNER_MODEL
    )
    # The process-wide planner is a composite that plans the shapes when nothing installed one.
    model_planner.set_executive_planner(None)
    assert model_planner.get_executive_planner().plan(_RESEARCH).planner == PLANNER_RULE


# ----------------------------------------------------- the preconditions on rows (536, 544, 554)


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
def _seams(monkeypatch, db_factory):
    store = InMemoryObjectStore()
    monkeypatch.setattr(activities, "build_artifact_context", lambda _settings: (db_factory, store))
    set_publisher(UiStatePublisher())
    yield
    set_publisher(UiStatePublisher())


def _seed(db, *, steps: list[dict[str, Any]], approvals: dict[str, str] | None = None) -> uuid.UUID:
    now = datetime.now(UTC)
    run = ExecutiveRunRow(
        id=uuid.uuid4(),
        goal="t",
        graph_json={"steps": steps},
        state=STATE_RUNNING,
        steps_total=len(steps),
        steps_done=0,
        source="rest",
        approvals_json=approvals,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    for s in steps:
        db.add(
            ExecutiveStepRow(
                id=uuid.uuid4(),
                run_id=run.id,
                step_id=s["id"],
                kind=s.get("kind", STEP_KIND_RESEARCH_SYNTHESIZE),
                inputs_json=s.get("inputs", {}),
                precondition_json=s.get("precondition", {"check": "none"}),
                postcondition_json=s.get(
                    "postcondition", {"evidence": EVIDENCE_TEXT, "min": s.get("min")}
                ),
                retry_json={},
                repeat_json=s.get("repeat", {}),
                timeout_s=60,
                risk_class=RISK_READ,
                compensation="none",
                state=s.get("state", STEP_STATE_PENDING),
                attempt=0,
                evidence_json=s.get("evidence"),
                created_at=now,
                updated_at=now,
            )
        )
    db.commit()
    return run.id


def _row(db, run_id, step_id):
    return activities._get_step_row(db, run_id, step_id)


def test_step_failed_step_verified_and_owner_approval_decide_by_the_rows(db) -> None:
    run_id = _seed(
        db,
        steps=[
            {"id": "s1", "state": STEP_STATE_FAILED},
            {"id": "s2", "state": STEP_STATE_VERIFIED, "evidence": {"text": "ok"}},
            {"id": "f1", "precondition": {"check": PRECONDITION_STEP_FAILED, "arg": "s1"}},
            {"id": "f2", "precondition": {"check": PRECONDITION_STEP_FAILED, "arg": "s2"}},
            {"id": "v1", "precondition": {"check": PRECONDITION_STEP_VERIFIED, "arg": "s1"}},
            {"id": "v2", "precondition": {"check": PRECONDITION_STEP_VERIFIED, "arg": "s2"}},
            {"id": "a1", "precondition": {"check": PRECONDITION_OWNER_APPROVAL}},
            {"id": "p1", "precondition": {"check": PRECONDITION_STEP_FAILED, "arg": "zz"}},
        ],
    )
    ok = lambda sid: activities._precondition_satisfied(db, run_id, _row(db, run_id, sid))  # noqa: E731
    assert ok("f1") == (True, None)  # the fallback runs after the failure
    assert ok("f2")[0] is False and "yedek dal gerekmedi" in ok("f2")[1]
    assert ok("v1")[0] is False and "doğrulanamadı" in ok("v1")[1]
    assert ok("v2") == (True, None)
    assert ok("a1")[0] is False and "onay" in ok("a1")[1]
    assert ok("p1")[0] is False  # an unknown sibling is not settled
    run = db.get(ExecutiveRunRow, run_id)
    run.approvals_json = {"a1": datetime.now(UTC).isoformat()}
    db.commit()
    assert ok("a1") == (True, None)


@pytest.mark.asyncio
async def test_a_step_repeats_bounded_until_its_minimum_is_met_and_the_last_round_is_judged(
    monkeypatch, db
) -> None:
    """Req 555: three rounds allowed, the third reaches the minimum; a step that never reaches
    it after its rounds is failed 'unverified', never re-run for ever."""
    run_id = _seed(db, steps=[{"id": "s1", "repeat": {"max_rounds": 3}, "min": 12}])
    calls: list[int] = []

    async def fake_dispatch(run_uuid, step_id, kind, resolved):
        calls.append(int(resolved.get("__round__") or 1))
        return {"text": "x" * (4 * len(calls))}

    monkeypatch.setattr(activities, "_dispatch_with_heartbeat", fake_dispatch)
    result = await activities.run_step_activity(str(run_id), "s1")
    assert result["state"] == STEP_STATE_VERIFIED and calls == [1, 2, 3]
    assert _row(db, run_id, "s1").attempt == 3

    run_id2 = _seed(db, steps=[{"id": "s1", "repeat": {"max_rounds": 2}, "min": 100}])
    calls.clear()
    result = await activities.run_step_activity(str(run_id2), "s1")
    assert result["state"] == STEP_STATE_FAILED and result["error_class"] == "unverified"
    assert calls == [1, 2]


def test_mark_awaiting_approval_writes_the_row_and_clears_it_once_approved(db) -> None:
    run_id = _seed(db, steps=[{"id": "a1", "precondition": {"check": PRECONDITION_OWNER_APPROVAL}}])
    marked = activities._mark_awaiting_approval(run_id, "a1")
    assert marked["awaiting_step"] == "a1"
    db.expire_all()
    assert db.get(ExecutiveRunRow, run_id).awaiting_step == "a1"
    run = db.get(ExecutiveRunRow, run_id)
    run.approvals_json = {"a1": "now"}
    db.commit()
    assert activities._mark_awaiting_approval(run_id, "a1")["awaiting_step"] is None


# ------------------------------------------------------------- the workflow's approval gate (544)

TASK_QUEUE = "executive-b38-test"
_RAN: list[str] = []
_MARKED: list[str] = []


@activity.defn(name="executive_run_step")
async def fake_run_step(run_id: str, step_id: str) -> dict:
    _RAN.append(step_id)
    return {"state": "verified", "error_class": None, "evidence": {"text": step_id}}


@activity.defn(name="executive_settle_crashed_step")
async def fake_settle(run_id: str, step_id: str, reason: str) -> dict:
    return {"state": "failed", "error_class": "internal_error", "evidence": None}


@activity.defn(name="executive_mark_awaiting_approval")
async def fake_mark(run_id: str, step_id: str) -> dict:
    _MARKED.append(step_id)
    return {"run_id": run_id, "awaiting_step": step_id}


def _wf_step(step_id: str, *, deps: list[str] | None = None, approval: bool = False) -> dict:
    precondition = (
        {"check": "owner_approval"}
        if approval
        else ({"check": "step_done", "arg": deps[0]} if deps else {"check": "none"})
    )
    return {
        "id": step_id,
        "kind": "synthesis",
        "inputs": {f"in{i}": f"{d}.text" for i, d in enumerate(deps or [])},
        "precondition": precondition,
        "timeout_s": 30,
    }


@pytest.mark.asyncio
async def test_the_workflow_parks_on_an_approval_step_and_runs_it_after_the_owners_signal() -> None:
    _RAN.clear()
    _MARKED.clear()
    env = await WorkflowEnvironment.start_time_skipping()
    worker = Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[ExecutiveWorkflow],
        activities=[fake_run_step, fake_settle, fake_mark],
    )
    async with env, worker:
        graph = {
            "steps": [
                _wf_step("s1"),
                _wf_step("s2", deps=["s1"], approval=True),
                _wf_step("s3", deps=["s2"]),
            ]
        }
        handle = await env.client.start_workflow(
            ExecutiveWorkflow.run,
            ExecutiveRunRequest(run_id="r-approval", graph_json=graph),
            id="executive-r-approval",
            task_queue=TASK_QUEUE,
        )
        for _ in range(50):
            status = await handle.query(ExecutiveWorkflow.status)
            if status.get("awaiting_step") == "s2":
                break
            await asyncio.sleep(0.05)
        assert status["awaiting_step"] == "s2", status
        assert _RAN == ["s1"] and _MARKED == ["s2"]
        assert "onay bekleniyor" in await handle.query(ExecutiveWorkflow.explain)
        await handle.signal(ExecutiveWorkflow.approve_step, "s2")
        result = await asyncio.wait_for(handle.result(), timeout=30)
    assert result["settled"] == ["s1", "s2", "s3"] and _RAN == ["s1", "s2", "s3"]


# ------------------------------------------------------------- the routes (544, 546, 552)


def _fake_temporal_client():
    handle = AsyncMock()
    handle.signal = AsyncMock(return_value=None)
    client = AsyncMock()
    client.start_workflow = AsyncMock(return_value=None)
    client.get_workflow_handle = lambda *_a, **_k: handle
    return client, handle


def test_an_owner_graph_reaches_every_kind_and_the_plan_route_reads_the_rationale(
    monkeypatch,
) -> None:
    h = build_harness()
    store = InMemoryObjectStore()
    monkeypatch.setattr(activities, "build_artifact_context", lambda _settings: (h.factory, store))
    client, handle = _fake_temporal_client()
    graph = {
        "goal": "Sahnenin kendisi",
        "steps": [
            {
                "id": "s1",
                "kind": "scene.create",
                "inputs": {"kind": "cube"},
                "postcondition": {"evidence": STEP_KIND_PROFILES["scene.create"].evidence_kind},
                "risk_class": STEP_KIND_PROFILES["scene.create"].risk_class,
                "compensation": STEP_KIND_PROFILES["scene.create"].compensation,
                "rationale": "Sahneyi kurmak için.",
            },
            {
                "id": "s2",
                "kind": "scene.render",
                "inputs": {"scene": "s1.scene_id"},
                "precondition": {"check": "owner_approval"},
                "postcondition": {"evidence": STEP_KIND_PROFILES["scene.render"].evidence_kind},
                "risk_class": STEP_KIND_PROFILES["scene.render"].risk_class,
                "compensation": STEP_KIND_PROFILES["scene.render"].compensation,
                "rationale": "Onayınızla render almak için.",
            },
            {
                "id": "s3",
                "kind": "synthesis",
                "inputs": {"render": "s2." + STEP_KIND_PROFILES["scene.render"].evidence_kind},
                "precondition": {"check": "step_done", "arg": "s2"},
                "postcondition": {"evidence": STEP_KIND_PROFILES["synthesis"].evidence_kind},
                "risk_class": STEP_KIND_PROFILES["synthesis"].risk_class,
                "compensation": STEP_KIND_PROFILES["synthesis"].compensation,
                "rationale": "Sonucu söylemek için.",
            },
        ],
    }
    with patch("app.executive.routes.Client.connect", AsyncMock(return_value=client)):
        started = h.client.post("/v1/executive/runs", json={"graph": graph})
        assert started.status_code == 200, started.text
        run_id = started.json()["run_id"]
        assert started.json()["planner"] == PLANNER_OWNER
        plan = h.client.get(f"/v1/executive/runs/{run_id}/plan").json()
        assert plan["planner"] == PLANNER_OWNER
        assert [s["kind"] for s in plan["steps"]] == ["scene.create", "scene.render", "synthesis"]
        assert plan["steps"][1]["rationale"] == "Onayınızla render almak için."
        # Nothing waits yet: approving is refused by name.
        nothing = h.client.post(f"/v1/executive/runs/{run_id}/approve", json={})
        assert nothing.status_code == 422 and "onay bekleyen" in nothing.json()["detail"]["message"]
        # The workflow parks s2 (the activity writes the row); the owner approves it.
        activities._mark_awaiting_approval(uuid.UUID(run_id), "s2")
        listed = h.client.get("/v1/executive/runs").json()["runs"]
        assert next(r for r in listed if r["run_id"] == run_id)["awaiting_step"] == "s2"
        approved = h.client.post(f"/v1/executive/runs/{run_id}/approve", json={})
        assert approved.status_code == 200, approved.text
        assert approved.json()["step_id"] == "s2"
        handle.signal.assert_awaited()
        plan = h.client.get(f"/v1/executive/runs/{run_id}/plan").json()
        assert plan["awaiting_step"] is None and "s2" in plan["approvals"]
        # A step that never asked cannot be approved.
        wrong = h.client.post(f"/v1/executive/runs/{run_id}/approve", json={"step_id": "s1"})
        assert wrong.status_code == 422
        # An owner graph outside the rules is refused with the reason.
        bad = h.client.post(
            "/v1/executive/runs",
            json={
                "graph": {
                    "goal": "x",
                    "steps": [
                        {
                            "id": "s1",
                            "kind": "mail.send",
                            "postcondition": {"evidence": "text"},
                            "risk_class": "read",
                        }
                    ],
                }
            },
        )
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_graph"
        # The rule shapes still plan a directive, and the row says who planned it.
        shaped = h.client.post("/v1/executive/runs", json={"directive": _RESEARCH})
        assert shaped.status_code == 200 and shaped.json()["planner"] == PLANNER_RULE
