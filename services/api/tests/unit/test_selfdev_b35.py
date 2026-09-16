"""B35 - SelfDev wired to the product and made safe (req 581, 583, 585, 598, 600, 601, 603,
608, 609, 615, 618-623, 680).

Six seams, each proven by executing it:

* the security review refuses what a generated patch must never carry (598/680) and is
  gated on the grant nothing consumed before B35;
* the gate and the shadow are read by the engine as facts of the run (600/608), and a red
  gate feeds the fix loop (603);
* the queue service: intake from three sources, the bridge (583), the promotion class
  consumed on finish (585), the bounds (618-620) as named refusals, the owner's decision
  from a verified capability only (609), CI red -> one bounded follow-up (603);
* the REST surface over the real application object (581, 609, 615, 621);
* the worker loop against a scripted queue (615);
* the voice: "Şu bug'ı kendin düzelt." queues a row and says the owner decides (622/623).
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Artifact, Task, TaskRun
from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.evolution.authority import (
    AuthorityError,
    Grant,
    LabAuthority,
    OwnerCapability,
    mint_owner_capability,
)
from app.evolution.models import Capability, CapabilityGap, EvolutionOpportunity, SkillVersion
from app.evolution.runtime import EvolutionRuntime
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.selfdev import bridge, security_review
from app.selfdev.gate import GATE_FAILED, GATE_PASSED, GATE_SKIPPED, GateResult, GitPushTrigger
from app.selfdev.models import (
    SOURCE_CI_FAILURE,
    SOURCE_OPPORTUNITY,
    SOURCE_OWNER_REST,
    SOURCE_OWNER_VOICE,
    STATE_APPROVED,
    STATE_AWAITING_OWNER,
    STATE_CLAIMED,
    STATE_QUARANTINED,
    STATE_QUEUED,
    STATE_REFUSED,
    SelfDevDefectRow,
)
from app.selfdev.service import (
    ERROR_BUDGET_EXHAUSTED,
    ERROR_DISK_FLOOR,
    ERROR_NOT_AWAITING_OWNER,
    ERROR_PARALLEL_LIMIT,
    ERROR_SELFDEV_DISABLED,
    BudgetPolicy,
    SelfDevService,
)
from app.selfdev.shadow import (
    SHADOW_FAILED,
    SHADOW_PASSED,
    ProcessShadowRunner,
    ShadowReport,
)
from app.selfdev.worker import ScriptedQueueClient, defect_spec_of, run_once
from app.voice.intents import Intent, resolve_intent
from app.voice.realtime_sessions.tools_selfdev import SELFDEV_TOOL_NAMES
from tests.identity_support import authenticate
from tests.voice_corpus.harness import build_harness

# ------------------------------------------------------------- the security review


def _review(edits: dict[str, str], base: dict[str, str | None] | None = None):
    return security_review.review_texts(edits, base_texts=base or {})


def test_the_security_review_names_each_refusal_with_its_path_and_line() -> None:
    edits = {
        "services/api/app/x.py": (
            "import os\n\n"
            'API_KEY = "sk-' + "abcdefghijklmnopqrstuvwxyz0123" + '"\n'  # split for CI's scan
            "def run(cmd):\n"
            "    return os.system(cmd)\n"
            'def fetch():\n    return httpx.get("https://example.com/x")\n'
        ),
        "services/api/app/identity/tokens.py": "SECRET_LEN = 64\n",
        "services/api/tests/unit/test_a.py": "def test_one():\n    assert True\n",
    }
    base = {
        "services/api/app/x.py": "import os\n",
        "services/api/app/identity/tokens.py": "SECRET_LEN = 32\n",
        "services/api/tests/unit/test_a.py": (
            "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n"
        ),
    }
    verdict = _review(edits, base)
    assert not verdict.passed
    found = {(f.check, f.path) for f in verdict.findings}
    assert (security_review.CHECK_SECRET, "services/api/app/x.py") in found
    assert (security_review.CHECK_DANGEROUS_CALL, "services/api/app/x.py") in found
    assert (security_review.CHECK_NETWORK, "services/api/app/x.py") in found
    assert (security_review.CHECK_GUARDED_PATH, "services/api/app/identity/tokens.py") in found
    assert (security_review.CHECK_TEST_REMOVAL, "services/api/tests/unit/test_a.py") in found
    secret = next(f for f in verdict.findings if f.check == security_review.CHECK_SECRET)
    assert secret.line == 3
    assert "x.py:3" in verdict.summary()
    # The verdict as the record keeps it: the grant it was run under, the checks, the findings.
    as_dict = verdict.as_dict()
    assert as_dict["grant"] == str(Grant.SECURITY_REVIEW_CANDIDATE)
    assert set(as_dict["checks"]) == set(security_review.CHECKS)


def test_a_line_the_base_already_had_is_not_a_finding_but_a_new_one_is() -> None:
    base_text = 'URL = "https://api.example.com"\n'
    unchanged = _review({"services/api/app/y.py": base_text}, {"services/api/app/y.py": base_text})
    assert unchanged.passed
    added = _review(
        {"services/api/app/y.py": base_text + 'OTHER = "https://evil.example.com"\n'},
        {"services/api/app/y.py": base_text},
    )
    assert [f.check for f in added.findings] == [security_review.CHECK_NETWORK]
    # A loopback URL is what a test writes; it is never egress.
    local = _review({"services/api/app/z.py": 'URL = "http://127.0.0.1:8000"\n'})
    assert local.passed


def test_the_review_is_gated_on_the_grant_and_on_being_an_authority() -> None:
    edits = {"services/api/app/ok.py": "X = 1\n"}
    with_grant = LabAuthority.issue("t", {Grant.SECURITY_REVIEW_CANDIDATE})
    assert security_review.review_candidate(with_grant, edits, base_texts={}).passed
    without = LabAuthority.read_only("t")
    with pytest.raises(AuthorityError) as refused:
        security_review.review_candidate(without, edits, base_texts={})
    assert refused.value.details["required_grant"] == str(Grant.SECURITY_REVIEW_CANDIDATE)
    with pytest.raises(AuthorityError):
        security_review.review_candidate(object(), edits, base_texts={})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        "scripts/secret-store.ps1",
        "scripts/cloud/deploy.ps1",
        "services/api/pyproject.toml",
        "apps/web/package.json",
        "services/api/app/security/step_up.py",
        "services/api/app/actions/confirmation_gate.py",
        "deploy/.env",
    ],
)
def test_the_guarded_paths_are_the_owners_not_a_candidates(path: str) -> None:
    assert security_review.is_guarded_path(path)
    assert not security_review.is_guarded_path("services/api/app/documents/service.py")


# ------------------------------------------------------------- the gate and the shadow


def test_a_gate_result_and_a_shadow_report_say_what_they_are() -> None:
    assert GateResult(GATE_PASSED).ok
    assert not GateResult(GATE_FAILED, "pytest: 1 failed").ok
    assert GateResult(GATE_SKIPPED, "no gate").as_dict()["state"] == GATE_SKIPPED
    report = ShadowReport(SHADOW_PASSED, port=5000)
    assert report.ok and report.as_dict()["mismatches"] == []


_SERVER = """
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        code = 200 if self.path == '/v1/system/health' else 404
        self.send_response(code); self.end_headers(); self.wfile.write(b'{}')
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()
"""


def test_the_process_shadow_runner_starts_probes_and_kills_a_real_process(tmp_path: Path) -> None:
    """Req 608 with a real subprocess: a loopback server standing in for the candidate's
    API answers the ready path, the probes are read, the process is gone afterwards."""
    package = tmp_path / "services" / "api"
    package.mkdir(parents=True)
    (package / "shadow_stub.py").write_text(_SERVER, encoding="utf-8")
    runner = ProcessShadowRunner(
        command=(sys.executable, "shadow_stub.py", "{port}"),
        probes=("/v1/system/health", "/v1/nothing"),
        startup_timeout_s=30.0,
    )
    report = runner.run(tmp_path)
    assert report.state == SHADOW_PASSED, report.detail
    statuses = {p.path: p.shadow_status for p in report.probes}
    assert statuses == {"/v1/system/health": 200, "/v1/nothing": 404}
    assert report.started_in_s is not None
    # And with a live baseline that disagrees, the report says so by path.
    disagreeing = ProcessShadowRunner(
        command=(sys.executable, "shadow_stub.py", "{port}"),
        probes=("/v1/nothing",),
        live_base_url="http://127.0.0.1:9",
        startup_timeout_s=30.0,
        status=lambda url, timeout: (200, "") if ":9/" in url else (404, ""),
    )
    mismatch = disagreeing.run(tmp_path)
    assert mismatch.state == SHADOW_FAILED
    assert mismatch.as_dict()["mismatches"] == ["/v1/nothing"]


def test_a_shadow_that_never_answers_is_a_failed_shadow_not_a_hang(tmp_path: Path) -> None:
    package = tmp_path / "services" / "api"
    package.mkdir(parents=True)
    (package / "never.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    runner = ProcessShadowRunner(
        command=(sys.executable, "never.py"), startup_timeout_s=1.0, probes=()
    )
    report = runner.run(tmp_path)
    assert report.state == SHADOW_FAILED
    assert "did not answer" in report.detail


def test_the_push_trigger_is_off_by_default_and_refuses_a_foreign_branch(tmp_path: Path) -> None:
    trigger = GitPushTrigger(tmp_path)
    off = trigger.push("selfdev/x")
    assert not off.pushed and "disabled" in off.detail
    trigger.enabled = True
    foreign = trigger.push("main")
    assert not foreign.pushed and "refused" in foreign.detail
    assert trigger.pushed == []


# ------------------------------------------------------------- the queue service


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (SelfDevDefectRow.__table__, ActivityEventRow.__table__):
        table.create(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _capability() -> OwnerCapability:
    class _Session:
        session_id = uuid.uuid4()
        client_kind = "web"
        scopes = ()

    return mint_owner_capability(_Session())


def _record(**overrides):
    base = {
        "run_id": "20260915-120000-x-abc123",
        "status": "STOPPED_AT_POLICY_BOUNDARY",
        "reason": "",
        "branch": "selfdev/20260915-120000-x-abc123",
        "candidate_sha": "c" * 40,
        "candidate_ci": {"state": "none", "detail": ""},
        "changed_paths": ["services/api/app/calc.py"],
        "risk": {"tier": 3, "reasons": ["app"]},
        "promotion_class": "OWNER_APPROVAL_REQUIRED",
        "security_review": {"passed": True, "findings": []},
        "gate": {"state": "passed"},
        "shadow": {"state": "passed", "mismatches": []},
        "model": {"input_tokens": 1200, "output_tokens": 300},
        "attempts": [{"n": 1, "checks": [{"name": "lint", "passed": True, "detail": "x" * 5000}]}],
    }
    base.update(overrides)
    return base


def test_intake_claim_finish_and_the_owners_decision_are_one_row(db) -> None:
    service = SelfDevService(policy=BudgetPolicy(max_parallel=1))
    row = service.intake(
        db, title="add() subtracts", evidence="add(2,3) == -1", source=SOURCE_OWNER_REST
    )
    assert row.state == STATE_QUEUED
    verdict = service.claim(db, worker_id="w1")
    assert verdict.claimed and verdict.row.state == STATE_CLAIMED
    # Req 620: a second claim while one is active is refused BY NAME.
    second = service.claim(db, worker_id="w2")
    assert not second.claimed and second.reason == ERROR_PARALLEL_LIMIT
    service.start(db, row.id, run_id="r1")
    finished = service.finish(db, row.id, _record())
    assert finished.state == STATE_AWAITING_OWNER
    assert finished.promotion_class == "OWNER_APPROVAL_REQUIRED"
    assert finished.tokens_used == 1500
    assert finished.candidate_sha == "c" * 40
    # The record is trimmed, never dropped: the check detail is bounded.
    assert len(finished.run_json["attempts"][0]["checks"][0]["detail"]) == 1500
    entry = service.entry(finished)
    assert entry["security_review"]["passed"] is True
    assert entry["never_auto_promote"] is False
    # Req 609: the decision needs the capability; a plain object is refused.
    with pytest.raises(AuthorityError):
        service.approve(db, row.id, object(), note="x")  # type: ignore[arg-type]
    approved = service.approve(db, row.id, _capability(), note="olur")
    assert approved.state == STATE_APPROVED and approved.decision == "approved"
    assert approved.decided_by.startswith("owner_session:")
    # Req 624: the ledger says promoted=False on the decision itself.
    decided = db.scalar(
        select(ActivityEventRow).where(ActivityEventRow.event_type == "selfdev.decided")
    )
    assert decided is not None and decided.detail_json["promoted"] is False
    with pytest.raises(ValueError, match=ERROR_NOT_AWAITING_OWNER):
        service.reject(db, row.id, _capability())


def test_the_bridge_turns_an_opportunity_into_one_defect_and_carries_its_class(db) -> None:
    opportunity = {
        "opportunity_id": "3f9d0c6e-0000-4000-8000-000000000001",
        "title": "Release rollback leaves the manifest stale",
        "statement": "After a rollback the manifest still names the failed release.",
        "source": "incident",
        "origin": [{"kind": "incident", "ref": "inc-9"}],
        "detail": {"paths": ["services/api/alembic/versions/x.py"], "priority": "P1"},
        "promotion_class": None,
    }
    spec = bridge.defect_from_opportunity(opportunity)
    assert spec["kind"] == "bug"
    assert spec["promotion_class"] == "NEVER_AUTO_PROMOTE"  # derived from the alembic path
    assert spec["priority"] == 1
    assert spec["scope"] == ["services/api/alembic/versions/x.py"]
    assert "inc-9" in spec["evidence"]
    service = SelfDevService()
    row = service.intake_opportunity(db, opportunity)
    assert row.source == SOURCE_OPPORTUNITY and row.opportunity_id == opportunity["opportunity_id"]
    with pytest.raises(ValueError, match="opportunity_already_queued"):
        service.intake_opportunity(db, opportunity)
    # Req 585: the class the bridge carried is the one the row keeps after the run, and
    # the entry says it is never promoted automatically.
    service.claim(db, worker_id="w")
    finished = service.finish(db, row.id, _record(promotion_class="AUTO_SAFE"))
    assert finished.promotion_class == "NEVER_AUTO_PROMOTE"
    assert service.entry(finished)["never_auto_promote"] is True
    # A gap-born opportunity with no paths is a feature over the default scope.
    feature = bridge.defect_from_opportunity(
        {"opportunity_id": "abc", "title": "t", "statement": "s", "source": "capability_gap"}
    )
    assert feature["kind"] == "feature" and feature["scope"] == list(bridge.DEFAULT_SCOPE)


def test_the_bounds_refuse_by_name_and_the_status_reads_them_beside_what_is_spent(
    db, tmp_path
) -> None:
    disabled = SelfDevService(enabled=False)
    assert disabled.claim(db, worker_id="w").reason == ERROR_SELFDEV_DISABLED
    with pytest.raises(AuthorityError):
        disabled.intake(db, title="t", evidence="", source=SOURCE_OWNER_REST)

    service = SelfDevService(
        policy=BudgetPolicy(daily_token_budget=1000, min_free_bytes=100, worktrees_root=tmp_path),
        disk_free=lambda path: 50,
    )
    service.intake(db, title="t", evidence="", source=SOURCE_OWNER_REST)
    # Req 619: the disk floor.
    assert service.claim(db, worker_id="w").reason == ERROR_DISK_FLOOR
    service = SelfDevService(
        policy=BudgetPolicy(daily_token_budget=1000, min_free_bytes=100, worktrees_root=tmp_path),
        disk_free=lambda path: 10**9,
    )
    # Req 618: a finished run today that spent the budget closes the day.
    service.intake(db, title="spent", evidence="", source=SOURCE_OWNER_REST)
    claimed = service.claim(db, worker_id="w").row
    assert claimed is not None
    service.finish(db, claimed.id, _record(model={"input_tokens": 900, "output_tokens": 200}))
    assert service.claim(db, worker_id="w").reason == ERROR_BUDGET_EXHAUSTED
    status = service.status(db)
    assert status["budget"]["tokens_used_today"] == 1100
    assert status["budget"]["tokens_remaining"] == 0
    assert status["budget"]["disk_free_bytes"] == 10**9
    assert status["last_refusal"] == ERROR_BUDGET_EXHAUSTED
    assert status["awaiting_owner"] == 1


def test_a_stale_claim_is_queued_again_and_the_engine_statuses_map_to_states(db) -> None:
    service = SelfDevService(policy=BudgetPolicy(claim_ttl_s=60))
    row = service.intake(db, title="t", evidence="", source=SOURCE_OWNER_VOICE)
    service.claim(db, worker_id="w", now=datetime.now(UTC) - timedelta(minutes=5))
    assert service.expire_stale_claims(db) == 1
    db.refresh(row)
    assert row.state == STATE_QUEUED and row.error_class == "claim_expired"
    for status, state in (
        ("REFUSED", STATE_REFUSED),
        ("QUARANTINED", STATE_QUARANTINED),
        ("BOOM", "failed"),
    ):
        service.intake(db, title=status, evidence="", source=SOURCE_OWNER_VOICE)
        claimed = service.claim(db, worker_id="w").row
        assert claimed is not None
        done = service.finish(db, claimed.id, _record(status=status, reason="why"))
        assert done.state == state and done.error_message == "why"


def test_a_red_ci_opens_one_bounded_follow_up_that_names_its_parent(db) -> None:
    """Req 603: the loop's open end, closed - and bounded."""
    service = SelfDevService(policy=BudgetPolicy(max_ci_fix_rounds=1))
    row = service.intake(db, title="t", evidence="", source=SOURCE_OWNER_REST)
    service.claim(db, worker_id="w")
    service.finish(db, row.id, _record(candidate_ci={"state": "failure", "detail": "1 failed"}))
    children = list(
        db.scalars(select(SelfDevDefectRow).where(SelfDevDefectRow.parent_id == row.id))
    )
    assert len(children) == 1
    child = children[0]
    assert child.source == SOURCE_CI_FAILURE and child.state == STATE_QUEUED
    assert "1 failed" in child.evidence and child.title.startswith("CI kırmızı")
    # The same red reported again does not open a second one.
    assert service.reconcile_ci(db, row.id, state="failure", detail="again").id == child.id
    # The child's own red CI is past the bound: no grandchild, the row says why.
    service.claim(db, worker_id="w")
    service.finish(db, child.id, _record(candidate_ci={"state": "failure", "detail": "still"}))
    db.refresh(child)
    assert child.error_class == "ci_fix_rounds_exhausted"
    assert db.scalar(select(SelfDevDefectRow).where(SelfDevDefectRow.parent_id == child.id)) is None
    # A green CI later changes the row's CI fact and opens nothing.
    assert service.reconcile_ci(db, row.id, state="success") is None
    db.refresh(row)
    assert row.run_json["candidate_ci"]["state"] == "success"


# ------------------------------------------------------------- the worker


class _ScriptedEngine:
    def __init__(self, record):
        self.record = record
        self.seen = []

    def run(self, defect, *, base_sha, targeted_tests):
        self.seen.append((defect, base_sha, targeted_tests))
        if isinstance(self.record, Exception):
            raise self.record
        return self.record


def test_the_worker_claims_starts_runs_and_finishes_once_and_reports_a_crash(tmp_path) -> None:
    entry = {
        "defect_id": "11111111-2222-4333-8444-555555555555",
        "title": "t",
        "evidence": "e",
        "scope": ["services/api/app/x.py"],
        "failing_test": "services/api/tests/unit/test_x.py",
    }
    client = ScriptedQueueClient(claims=[{"defect": entry}])
    engine = _ScriptedEngine(_record())
    posted = run_once(client, engine, worker_id="w", base_sha=lambda: "b" * 40)
    assert posted["status"] == "STOPPED_AT_POLICY_BOUNDARY"
    assert client.started == [(entry["defect_id"], "pending-11111111")]
    assert client.finished[0][0] == entry["defect_id"]
    defect, base, targeted = engine.seen[0]
    assert defect.defect_id == entry["defect_id"] and base == "b" * 40
    assert targeted == ["services/api/tests/unit/test_x.py"]
    # No claim: nothing runs.
    assert run_once(client, engine, worker_id="w", base_sha=lambda: "b") is None
    # A crash is posted as a FAILED record naming the exception, never swallowed.
    crashing = ScriptedQueueClient(claims=[{"defect": entry}])
    run_once(crashing, _ScriptedEngine(RuntimeError("disk")), worker_id="w", base_sha=lambda: "b")
    assert crashing.finished[0][1]["status"] == "FAILED"
    assert "RuntimeError: disk" in crashing.finished[0][1]["reason"]
    assert defect_spec_of({"defect_id": "x"}).scope == ("services/api/app/", "services/api/tests/")


# ------------------------------------------------------------- the REST surface

TABLES = [
    SelfDevDefectRow.__table__,
    ActivityEventRow.__table__,
    Task.__table__,
    Artifact.__table__,
    TaskRun.__table__,
    Capability.__table__,
    SkillVersion.__table__,
    CapabilityGap.__table__,
    EvolutionOpportunity.__table__,
]


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PAGENTOS_EVOLUTION_SKILLS_ROOT", str(tmp_path / "skills"))
    monkeypatch.setenv("PAGENTOS_EVOLUTION_WORK_ROOT", str(tmp_path / "work"))
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine)
    app.state.artifacts = artifacts
    app.state.evolution = EvolutionRuntime(settings, engine=engine)
    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    yield test_client
    test_client.close()


def test_the_queue_is_reachable_from_the_product_surface_end_to_end(client: TestClient) -> None:
    """Req 581/609/615/621 over the real application object: create, claim, finish, list
    pending, approve - and the approve answer says promoted=False (req 624)."""
    created = client.post(
        "/v1/selfdev/defects",
        json={
            "title": "add() subtracts",
            "evidence": "add(2,3) == -1",
            "scope": ["services/api/app/calc.py"],
        },
    )
    assert created.status_code == 201, created.text
    defect_id = created.json()["defect"]["defect_id"]
    assert created.json()["defect"]["state"] == STATE_QUEUED

    claimed = client.post("/v1/selfdev/worker/claim", json={"worker_id": "dev-1"})
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["defect"]["defect_id"] == defect_id
    assert claimed.json()["budget"]["max_parallel"] == 1
    again = client.post("/v1/selfdev/worker/claim", json={"worker_id": "dev-2"})
    assert again.json()["defect"] is None and again.json()["reason"] == ERROR_PARALLEL_LIMIT

    started = client.post(f"/v1/selfdev/defects/{defect_id}/start", json={"run_id": "r1"})
    assert started.status_code == 200
    finished = client.post(f"/v1/selfdev/defects/{defect_id}/finish", json={"record": _record()})
    assert finished.status_code == 200, finished.text
    assert finished.json()["defect"]["state"] == STATE_AWAITING_OWNER

    pending = client.get("/v1/selfdev/defects/pending").json()["pending"]
    assert [p["defect_id"] for p in pending] == [defect_id]
    assert pending[0]["gate"]["state"] == "passed" and pending[0]["shadow"]["state"] == "passed"
    status = client.get("/v1/selfdev/status").json()
    assert status["awaiting_owner"] == 1 and status["budget"]["tokens_used_today"] == 1500

    approved = client.post(f"/v1/selfdev/defects/{defect_id}/approve", json={"note": "olur"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["promoted"] is False
    assert approved.json()["defect"]["state"] == STATE_APPROVED
    twice = client.post(f"/v1/selfdev/defects/{defect_id}/reject", json={})
    assert twice.status_code == 409
    assert twice.json()["detail"]["error_class"] == ERROR_NOT_AWAITING_OWNER
    missing = client.get(f"/v1/selfdev/defects/{uuid.uuid4()}")
    assert missing.status_code == 404
    listed = client.get("/v1/selfdev/defects?state=approved").json()["defects"]
    assert len(listed) == 1
    bad_state = client.get("/v1/selfdev/defects?state=nope")
    assert bad_state.status_code == 422


def test_the_bridge_route_reads_the_one_backlog_and_a_red_ci_route_opens_the_follow_up(
    client: TestClient,
) -> None:
    from tests.unit.test_evolution_routes import evidence_backed_body

    body = evidence_backed_body(client, title="Stale manifest after rollback")
    body["detail"] = {"paths": ["services/api/app/deployment/manifest.py"]}
    created = client.post("/v1/evolution/opportunities", json=body)
    assert created.status_code in (200, 201), created.text
    opportunity_id = created.json()["opportunity_id"]

    bridged = client.post(f"/v1/selfdev/defects/from-opportunity/{opportunity_id}")
    assert bridged.status_code == 201, bridged.text
    defect = bridged.json()["defect"]
    assert defect["opportunity_id"] == opportunity_id
    assert defect["promotion_class"] in (
        "OWNER_APPROVAL_REQUIRED",
        "NEVER_AUTO_PROMOTE",
        "AUTO_CANARY",
        "AUTO_SAFE",
    )
    duplicate = client.post(f"/v1/selfdev/defects/from-opportunity/{opportunity_id}")
    assert duplicate.status_code == 409
    unknown = client.post(f"/v1/selfdev/defects/from-opportunity/{uuid.uuid4()}")
    assert unknown.status_code == 404

    client.post("/v1/selfdev/worker/claim", json={"worker_id": "dev-1"})
    client.post(f"/v1/selfdev/defects/{defect['defect_id']}/finish", json={"record": _record()})
    red = client.post(
        f"/v1/selfdev/defects/{defect['defect_id']}/ci",
        json={"state": "failure", "detail": "2 failed"},
    )
    assert red.status_code == 200, red.text
    follow_up = red.json()["follow_up"]
    assert follow_up is not None and follow_up["parent_id"] == defect["defect_id"]
    assert follow_up["source"] == SOURCE_CI_FAILURE and follow_up["state"] == STATE_QUEUED


def test_a_disabled_queue_answers_503_in_the_owners_words(client: TestClient) -> None:
    client.app.state.selfdev_service = SelfDevService(enabled=False)
    refused = client.post("/v1/selfdev/defects", json={"title": "t"})
    assert refused.status_code == 503
    assert refused.json()["detail"]["error_class"] == ERROR_SELFDEV_DISABLED
    assert "kapalı" in refused.json()["detail"]["message"]


def test_every_selfdev_route_requires_an_owner_session(tmp_path, monkeypatch) -> None:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    with TestClient(app) as anonymous:
        for method, path in (
            ("get", "/v1/selfdev/defects"),
            ("get", "/v1/selfdev/status"),
            ("post", "/v1/selfdev/defects"),
            ("post", "/v1/selfdev/worker/claim"),
        ):
            response = anonymous.request(method.upper(), path, json={})
            assert response.status_code == 401, (method, path, response.status_code)


# ------------------------------------------------------------- the voice


def test_the_owner_assigns_a_bug_by_voice_and_the_row_carries_their_words() -> None:
    h = build_harness()
    sid = h.new_session()
    said = h.say(sid, "Şu bug'ı kendin düzelt: alarm iki kere çalıyor.")
    assert said["resolved_intents"][-1]["intent"] == "selfdev_fix"
    call = h.tool(sid, "c-1", "selfdev.defect", {"content": "the model's paraphrase"})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert "onayınızı" in body["speech"] and "canlıya almam" in body["speech"]
    assert body["observed_after"]["server"]["promoted"] is False
    assert body["defect"]["source"] == SOURCE_OWNER_VOICE
    # The owner's sentence, not the model's argument.
    assert body["defect"]["evidence"].startswith("Şu bug'ı kendin düzelt")
    assert h.device.capabilities_called() == []
    with h.factory() as db:
        rows = list(db.scalars(select(SelfDevDefectRow)))
    assert len(rows) == 1 and rows[0].kind == "bug" and rows[0].state == STATE_QUEUED

    feature = h.say(sid, "Şu özelliği kendine ekle: sabah özetinde hava durumu.", turn=2)
    assert feature["resolved_intents"][-1]["intent"] == "selfdev_feature"
    call = h.tool(sid, "c-2", "selfdev.feature", {})
    assert call["result"]["defect"]["kind"] == "feature"
    status = h.tool(sid, "c-3", "selfdev.status", {})
    assert "Kuyrukta" in status["result"]["speech"]
    assert len(status["result"]["defects"]) == 2


def test_without_the_owners_words_or_an_argument_the_tool_refuses_rather_than_guessing() -> None:
    h = build_harness()
    sid = h.new_session()
    call = h.tool(sid, "c-1", "selfdev.defect", {})
    assert call["status"] == "succeeded"
    assert call["result"]["execution_status"] == "refused"
    assert call["result"]["error_class"] == "invalid_defect"


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Şu bug'ı kendin düzelt.", Intent.SELFDEV_FIX),
        ("Bu hatayı kendin düzelt.", Intent.SELFDEV_FIX),
        ("Şu hatayı sen düzelt.", Intent.SELFDEV_FIX),
        ("Şu özelliği kendine ekle.", Intent.SELFDEV_FEATURE),
        ("Bu yeteneği kendine kazandır.", Intent.SELFDEV_FEATURE),
        ("Kendinde ne düzeltiyorsun?", Intent.SELFDEV_STATUS),
        # The neighbours keep what was theirs.
        ("Kendi kendini geliştirmeyi duraklat.", Intent.EVOLUTION_PAUSE),
        ("Kendini geliştirmeye devam et.", Intent.EVOLUTION_RESUME),
        ("Bunu düzelt.", Intent.MEMORY_CORRECT),
        # No self-reference: the sentence is about the owner's world, not the system's code.
        ("Bu hatayı düzelt.", Intent.EXPLAIN),
        ("Şu bug'ı düzelt.", Intent.MEMORY_CORRECT),
        ("Renkleri biraz düzelt.", Intent.CREATIVE_ADJUST),
    ],
)
def test_the_selfdev_intents_and_their_neighbours(text: str, intent: Intent) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is intent, (text, resolved.intent, resolved.matched)
    if intent in (Intent.SELFDEV_FIX, Intent.SELFDEV_FEATURE):
        assert resolved.selfdev_request == text
    else:
        assert resolved.selfdev_request is None


def test_the_three_tools_are_registered_and_tiered() -> None:
    from app.security import step_up
    from app.voice.realtime_sessions.tools import default_registry

    registered = set(default_registry().names())
    assert set(SELFDEV_TOOL_NAMES) <= registered
    assert step_up.tier_of("selfdev.defect") == step_up.TIER_SENSITIVE
    assert step_up.tier_of("selfdev.status") == step_up.TIER_OPEN
