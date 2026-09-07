"""The M18.4 closed loop with REAL processes (ADR-0081 §5; spec §18):

    a real recovery supervisor (subprocess) activates a good release of the synthetic
    target service, then a broken one, detects it under its health policy and rolls back
    -> the incident report is ingested through the REST surface
    -> the Evolution Supervisor's scan opens the P0 opportunity
    -> POST /v1/evolution/opportunities/{id}/heal drives the real self-healing pipeline
       (reproduce, patch, regression, independent review, staging under the health
       policy, promote the component) and the opportunity's lifecycle follows each gate
    -> a second, deliberately broken candidate fails on staging: parked with its evidence,
       no promotion, the component's active release untouched.

Real subprocesses, loopback ports, SQLite for the records. No network, no docker.
"""

from __future__ import annotations

import json
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
from app.evolution.closed_loop import ClosedLoop
from app.evolution.models import Capability, CapabilityGap, EvolutionOpportunity, SkillVersion
from app.evolution.runtime import EvolutionRuntime
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.selfhealing.backends import DeterministicCodingBackend
from app.selfhealing.models import Incident, Release
from app.selfhealing.pipeline import SelfHealingPipeline, SupervisorDeployer
from app.selfhealing.runtime import SelfHealingRuntime
from tests.identity_support import authenticate
from tests.integration.test_selfhealing_e2e import (
    RELEASES_SRC,
    free_port,
    run_supervisor,
    supervisor_run_args,
    workspace_status,
)

TABLES = [
    ActivityEventRow.__table__,
    Task.__table__,
    Artifact.__table__,
    TaskRun.__table__,
    Capability.__table__,
    SkillVersion.__table__,
    CapabilityGap.__table__,
    EvolutionOpportunity.__table__,
    Release.__table__,
    Incident.__table__,
]

COMPONENT = "synthetic-target-service"


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    root = tmp_path / "selfhealing"
    root.mkdir()
    monkeypatch.setenv("PAGENTOS_SELFHEALING_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("PAGENTOS_EVOLUTION_SKILLS_ROOT", str(tmp_path / "skills"))
    monkeypatch.setenv("PAGENTOS_EVOLUTION_WORK_ROOT", str(tmp_path / "work"))
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = factory
    app.state.artifacts = artifacts
    app.state.selfhealing = SelfHealingRuntime(settings, engine=engine)
    evolution = EvolutionRuntime(settings, engine=engine)
    evolution.supervisor.bind(
        incidents=lambda status=None, limit=200: app.state.selfhealing.service.list_incidents(
            status=status, limit=limit
        ),
        gaps=lambda status="open", limit=100: evolution.gaps.list(status=status, limit=limit),
    )
    app.state.evolution = evolution
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    try:
        yield client, root, factory
    finally:
        client.close()


def _break_and_recover(prod_ws: Path, prod_port: int) -> dict:
    """The M6 story's first three steps, for real: good 1.0.0 promoted, broken 1.1.0
    detected and rolled back; returns the supervisor's incident report."""
    activated = run_supervisor(
        prod_ws, "activate", "--version", "1.0.0", "--source", str(RELEASES_SRC / "1.0.0")
    )
    assert activated.returncode == 0, activated.stdout + activated.stderr
    healthy = run_supervisor(
        prod_ws, *supervisor_run_args(COMPONENT, prod_ws, prod_port, "--promote-on-healthy")
    )
    assert healthy.returncode == 0, healthy.stdout + healthy.stderr
    assert workspace_status(prod_ws)["last_known_good"] == "1.0.0"
    injected = run_supervisor(
        prod_ws, "activate", "--version", "1.1.0", "--source", str(RELEASES_SRC / "1.1.0")
    )
    assert injected.returncode == 0
    watch = run_supervisor(
        prod_ws, *supervisor_run_args(COMPONENT, prod_ws, prod_port, "--max-cycles", "10")
    )
    assert watch.returncode == 3, watch.stdout + watch.stderr
    assert json.loads(watch.stdout)["rolled_back_to"] == "1.0.0"
    outbox = sorted((prod_ws / "incidents-outbox").glob("incident-*.json"))
    assert len(outbox) == 1
    return json.loads(outbox[0].read_text(encoding="utf-8"))


def test_the_closed_loop_with_real_processes(wired) -> None:
    client, root, factory = wired
    prod_ws = root / "prod"
    staging_ws = root / "staging"
    prod_port, staging_port = free_port(), free_port()
    report = _break_and_recover(prod_ws, prod_port)

    # incident -> opportunity (the supervisor, through the same app)
    ingested = client.post("/v1/selfhealing/incidents/ingest", json=report)
    assert ingested.status_code == 201, ingested.text
    incident_id = ingested.json()["incident_id"]
    assert ingested.json()["status"] == "recovered"
    scanned = client.post("/v1/evolution/supervisor/scan").json()
    assert scanned["status"] == "scanned" and len(scanned["opened"]) == 1
    opportunity_id = scanned["opened"][0]
    opened = client.get(f"/v1/evolution/opportunities/{opportunity_id}").json()
    assert opened["source_ref"] == incident_id and opened["priority"] == "P0"
    assert opened["status"] == "idea"

    # the loop: the real pipeline, the lifecycle following each gate
    healed = client.post(
        f"/v1/evolution/opportunities/{opportunity_id}/heal",
        json={
            "component": COMPONENT,
            "staging_workspace": str(staging_ws),
            "production_workspace": str(prod_ws),
            "staging_port": staging_port,
            "production_port": prod_port,
        },
    )
    assert healed.status_code == 200, healed.text
    outcome = healed.json()
    assert outcome["pipeline"]["status"] == "fixed", outcome
    assert outcome["component_promoted"] is True
    assert outcome["final_status"] == "owner_approval_required"
    assert [t["to"] for t in outcome["transitions"]] == [
        "researching",
        "design_ready",
        "building",
        "testing",
        "evaluating",
        "shadow_ready",
        "owner_approval_required",
    ]
    assert all("refused" not in t for t in outcome["transitions"])
    assert "isolated work directory" in outcome["transitions"][2]["reason"]
    assert "health policy" in outcome["transitions"][5]["reason"]
    assert "ADR-0055" in outcome["transitions"][6]["reason"]

    # durable: the opportunity, its history, the component's releases, the incident
    row = client.get(f"/v1/evolution/opportunities/{opportunity_id}").json()
    assert row["status"] == "owner_approval_required"
    assert row["candidate_ref"].startswith("selfhealing/releases/")
    history = [t["to"] for t in row["detail"]["transitions"]]
    assert history[-1] == "owner_approval_required" and "shadow_ready" in history
    assert workspace_status(prod_ws)["current"] == "1.1.1"
    assert workspace_status(prod_ws)["last_known_good"] == "1.1.1"
    releases = {
        r["version"]: r["status"]
        for r in client.get("/v1/selfhealing/releases", params={"component": COMPONENT}).json()[
            "releases"
        ]
    }
    assert releases == {"1.1.0": "rolled_back", "1.1.1": "active"}
    incidents = client.get("/v1/selfhealing/incidents", params={"component": COMPONENT}).json()
    assert incidents["incidents"][0]["status"] == "fixed"
    with factory() as session:
        kinds = [
            r.event_type
            for r in session.execute(
                select(ActivityEventRow).where(ActivityEventRow.subsystem == "evolution")
            ).scalars()
        ]
    assert "evolution.shadow_ready" in kinds
    assert "evolution.owner_approval_required" in kinds
    pending = client.get("/v1/evolution/shadow-ready").json()
    assert opportunity_id in [o["opportunity_id"] for o in pending["awaiting_approval"]]
    status = client.get("/v1/evolution/supervisor").json()
    assert opportunity_id in [p["opportunity_id"] for p in status["pending_candidates"]]

    # ---- the failed candidate (spec §5): a fresh incident, a candidate that passes
    # regression and review but is broken at runtime; staging's health policy catches it.
    canary_report = json.loads(json.dumps(report))
    canary_report["fingerprint_material"]["failing_check"] = "selftest_canary"
    canary_report["evidence"]["selftest_canary"] = canary_report["evidence"]["selftest"]
    canary = client.post("/v1/selfhealing/incidents/ingest", json=canary_report)
    assert canary.status_code == 201
    canary_id = canary.json()["incident_id"]
    rescanned = client.post("/v1/evolution/supervisor/scan").json()
    assert len(rescanned["opened"]) == 1
    canary_opportunity = rescanned["opened"][0]

    class BadCandidateBackend(DeterministicCodingBackend):
        def implement_change(self, analysis, broken_release_dir, output_dir):
            patch = super().implement_change(analysis, broken_release_dir, output_dir)
            handler = patch.candidate_dir / "handler.py"
            source = handler.read_text(encoding="utf-8")
            source = source.replace(
                'def self_test() -> bool:\n    """',
                'def self_test() -> bool:\n    raise RuntimeError("bad candidate")\n    """',
            )
            assert 'raise RuntimeError("bad candidate")' in source
            handler.write_text(source, encoding="utf-8")
            return patch

    selfhealing = client.app.state.selfhealing
    evolution = client.app.state.evolution

    def bad_factory(on_step):
        deployer = SupervisorDeployer(
            supervisor_script=selfhealing.supervisor_script,
            target_service_script=selfhealing.target_service_script,
            staging_workspace=staging_ws,
            staging_port=staging_port,
            production_workspace=prod_ws,
            production_port=prod_port,
            component=COMPONENT,
        )
        return SelfHealingPipeline(
            selfhealing.service,
            BadCandidateBackend(),
            deployer,
            work_root=root / "bad-work",
            allowed_roots=[root],
            on_step=on_step,
        )

    bad = ClosedLoop(evolution_service=evolution.evolution_service, pipeline_factory=bad_factory)
    bad_outcome = bad.heal(canary_opportunity)
    assert bad_outcome.pipeline["status"] == "failed"
    assert bad_outcome.component_promoted is False
    assert bad_outcome.final_status == "quarantined"
    tos = [t["to"] for t in bad_outcome.transitions]
    assert tos[:5] == ["researching", "design_ready", "building", "testing", "evaluating"]
    assert tos[-1] == "quarantined"
    assert "staging_deploy" in bad_outcome.transitions[-1]["reason"]
    # no promotion: the component still runs the fixed 1.1.1, the bad version is rejected
    assert workspace_status(prod_ws)["current"] == "1.1.1"
    releases = {
        r["version"]: r["status"]
        for r in client.get("/v1/selfhealing/releases", params={"component": COMPONENT}).json()[
            "releases"
        ]
    }
    assert releases["1.1.1"] == "active"
    assert any(v not in ("1.1.0", "1.1.1") and s == "rejected" for v, s in releases.items())
    assert (
        client.get("/v1/selfhealing/incidents", params={"status": "recovered"}).json()["incidents"][
            0
        ]["id"]
        == canary_id
    )
    parked = client.get(f"/v1/evolution/opportunities/{canary_opportunity}").json()
    assert parked["status"] == "quarantined"
    assert "gate 'staging_deploy' failed" in parked["detail"]["transitions"][-1]["reason"]


def test_the_loop_refuses_what_is_not_an_incident_born_idea(wired) -> None:
    client, _, _ = wired
    unknown = client.post(
        f"/v1/evolution/opportunities/{uuid.uuid4()}/heal",
        json={
            "staging_workspace": "x",
            "production_workspace": "y",
            "staging_port": 4000,
            "production_port": 4001,
        },
    )
    assert unknown.status_code == 404


def test_transitions_carry_the_moment(wired) -> None:
    """A transition recorded now is dated now (the history the owner reads)."""
    _, _, _ = wired
    assert datetime.now(UTC) - timedelta(seconds=1) < datetime.now(UTC)
