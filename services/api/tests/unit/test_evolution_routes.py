"""Unit tests: the /v1/evolution REST surface against an injected SQLite runtime
(mirrors the selfhealing routes test discipline; everything offline)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Artifact, Task, TaskRun
from app.config import Settings
from app.evolution.models import (
    Capability,
    CapabilityGap,
    EvolutionOpportunity,
    SkillVersion,
)
from app.evolution.runtime import EvolutionRuntime
from app.evolution.service import EVOLUTION_VERSION
from app.ledger.models import ActivityEventRow
from app.main import create_app
from tests.identity_support import authenticate
from tests.unit.test_evolution_backlog import SCORES, insert_ledger_event

TABLES = [
    Capability.__table__,
    SkillVersion.__table__,
    CapabilityGap.__table__,
    EvolutionOpportunity.__table__,
    ActivityEventRow.__table__,
    Task.__table__,
    Artifact.__table__,
    TaskRun.__table__,
]

GAP_BODY = {
    "requested_capability": "text.slugify",
    "request_text": "Bir baslik verildiginde url slug uret.",
    "required_inputs": ["text"],
    "required_outputs": ["slug"],
    "spec": {
        "capability_id": "text.slugify",
        "operation": "slugify",
        "version": "0.1.0",
        "summary": "turn a title into a url slug",
    },
}


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
    app.state.evolution = EvolutionRuntime(settings, engine=engine)
    test_client = TestClient(app)
    # M9: every /v1/evolution endpoint requires an owner session.
    authenticate(app, test_client, settings=settings)
    yield test_client
    test_client.close()


# ------------------------------------------------------------- capabilities


def test_registry_starts_empty(client: TestClient) -> None:
    response = client.get("/v1/evolution/capabilities")
    assert response.status_code == 200
    assert response.json() == {"capabilities": []}


def test_unknown_capability_is_404(client: TestClient) -> None:
    response = client.get("/v1/evolution/capabilities/text.slugify")
    assert response.status_code == 404
    assert response.json()["detail"]["error_class"] == "not_found"


# --------------------------------------------------------------------- gaps


def test_detect_records_a_gap_with_its_decision_trail(client: TestClient) -> None:
    response = client.post("/v1/evolution/gaps", json=GAP_BODY)
    assert response.status_code == 201
    body = response.json()
    assert body["resolution"] == "generation"
    assert body["status"] == "open"
    steps = [entry["step"] for entry in body["decision_trail"]]
    assert steps[:3] == ["request_received", "existing_capability", "composition"]
    composition = body["decision_trail"][2]["evidence"]
    assert composition["attempted"] is True and composition["satisfied"] is False

    listed = client.get("/v1/evolution/gaps", params={"status": "open"}).json()["gaps"]
    assert [gap["id"] for gap in listed] == [body["id"]]


@pytest.mark.parametrize(
    "override",
    [
        {"requested_capability": "Text.Slugify"},
        {"requested_capability": "x" * 200},
        {"request_text": ""},
        {"request_text": "x" * 5000},
        {"required_inputs": ["../etc"]},
        {"required_outputs": ["a" * 100]},
        {"spec": {"capability_id": "text.slugify", "operation": "arbitrary_python"}},
        {"unexpected_field": 1},
    ],
)
def test_bounded_and_hostile_gap_bodies_are_refused(client: TestClient, override) -> None:
    response = client.post("/v1/evolution/gaps", json={**GAP_BODY, **override})
    assert response.status_code == 422


def test_resolve_runs_the_pipeline_and_registers(client: TestClient) -> None:
    gap = client.post("/v1/evolution/gaps", json=GAP_BODY).json()
    response = client.post(f"/v1/evolution/gaps/{gap['id']}/resolve")
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "registered"
    names = [stage["name"] for stage in result["stages"]]
    assert names[-2:] == ["register_capability", "resume_task"]
    assert names.index("shadow") < names.index("canary") < names.index("register_capability")

    capability = client.get("/v1/evolution/capabilities/text.slugify").json()
    assert capability["status"] == "production"
    assert capability["dispatchable"] is True
    assert capability["manifest"]["entrypoint"] == "run"

    versions = client.get("/v1/evolution/skill-versions").json()["skill_versions"]
    assert [v["status"] for v in versions] == ["registered"]
    assert client.get("/v1/evolution/gaps").json()["gaps"][0]["status"] == "resolved"


def test_resolve_of_an_unknown_gap_is_404(client: TestClient) -> None:
    response = client.post(f"/v1/evolution/gaps/{uuid.uuid4()}/resolve")
    assert response.status_code == 404


def test_resolve_of_a_core_change_gap_is_refused(client: TestClient) -> None:
    gap = client.post(
        "/v1/evolution/gaps",
        json={
            **GAP_BODY,
            "requested_capability": "recovery.patch_supervisor",
            "request_text": "Supervisor icin yeni bir surum uret.",
            "target_component": "recovery-supervisor",
            "spec": None,
        },
    ).json()
    assert gap["resolution"] == "product_change_required"
    assert gap["status"] == "abandoned"

    result = client.post(f"/v1/evolution/gaps/{gap['id']}/resolve").json()
    assert result["status"] == "refused"
    assert result["stages"][-1]["detail"]["error_class"] == "product_change_required"
    assert client.get("/v1/evolution/skill-versions").json()["skill_versions"] == []


def test_second_request_for_a_registered_capability_needs_no_generation(
    client: TestClient,
) -> None:
    gap = client.post("/v1/evolution/gaps", json=GAP_BODY).json()
    client.post(f"/v1/evolution/gaps/{gap['id']}/resolve")

    again = client.post("/v1/evolution/gaps", json=GAP_BODY).json()
    assert again["resolution"] == "existing_capability"
    assert again["status"] == "resolved"
    versions = client.get("/v1/evolution/skill-versions").json()["skill_versions"]
    assert len(versions) == 1  # no second candidate was ever built


def test_health_reports_the_evolution_subsystem(client: TestClient) -> None:
    body = client.get("/v1/system/health").json()
    assert "evolution" in body["checks"]
    assert body["checks"]["evolution"]["skill_generator"] == "deterministic"


def test_audit_endpoint_answers_all_nine_questions(client: TestClient) -> None:
    gap = client.post("/v1/evolution/gaps", json=GAP_BODY).json()
    assert client.post(f"/v1/evolution/gaps/{gap['id']}/resolve").json()["status"] == (
        "registered"
    )

    response = client.get(f"/v1/evolution/gaps/{gap['id']}/audit")
    assert response.status_code == 200
    audit = response.json()
    assert audit["questions"] == [
        "why_needed",
        "what_triggered",
        "what_changed",
        "code_and_dependencies",
        "permissions_granted",
        "tests_that_ran",
        "who_reviewed",
        "why_promoted",
        "rollback_target",
    ]
    for question in audit["questions"]:
        assert audit["answers"][question], question
    assert audit["answers"]["who_reviewed"]["reviewer"] == "independent-skill-reviewer"
    assert audit["answers"]["permissions_granted"]["deny_by_default"] is True


def test_audit_of_an_unknown_gap_is_404(client: TestClient) -> None:
    assert client.get(f"/v1/evolution/gaps/{uuid.uuid4()}/audit").status_code == 404


def test_an_over_deep_request_is_refused_with_409(client: TestClient) -> None:
    response = client.post("/v1/evolution/gaps", json={**GAP_BODY, "depth": 5})
    assert response.status_code == 409
    assert response.json()["detail"]["error_class"] == "recursion_limit_exceeded"


def test_health_reports_the_component_catalog_and_budget(client: TestClient) -> None:
    check = client.get("/v1/system/health").json()["checks"]["evolution"]
    assert check["component_catalog"] >= 1
    assert check["resource_budget"]["timeout_s"] > 0
    assert check["resource_budget"]["max_depth"] >= 0
    assert check["sandbox_isolated_from_core"] is True


def test_health_reports_the_production_boundary(client: TestClient) -> None:
    """The boundary is visible in the health surface, not only in the code."""
    check = client.get("/v1/system/health").json()["checks"]["evolution"]
    assert check["engine_authority_scope"] == "lab"
    assert check["production_grants_held"] == 0
    assert len(check["root_policy_digest"]) == 64


# ------------------------------------------------------------- opportunities

OPPORTUNITY_ENDPOINTS = [
    ("get", "/v1/evolution/opportunities"),
    ("post", "/v1/evolution/opportunities"),
    ("get", "/v1/evolution/shadow-ready"),
    ("get", "/v1/evolution/policy"),
]


def evidence_backed_body(client: TestClient, **overrides) -> dict:
    session_scope = client.app.state.evolution.session
    event_id = insert_ledger_event(session_scope)
    body = {
        "title": "Araştırma işine yeniden deneme ekle",
        "statement": "Aynı hata üç kez tekrarladı.",
        "evidence_refs": [{"kind": "ledger_event", "ref": event_id}],
        "scores": SCORES,
        "source": "ledger_event",
        "source_ref": f"activity_events:{event_id}",
    }
    body.update(overrides)
    return body


def create_opportunity(client: TestClient, **overrides) -> dict:
    response = client.post(
        "/v1/evolution/opportunities", json=evidence_backed_body(client, **overrides)
    )
    assert response.status_code == 201, response.text
    return response.json()


def drive_to_shadow_ready(client: TestClient, opportunity_id: str) -> dict:
    body: dict = {}
    for status in (
        "researching",
        "design_ready",
        "building",
        "testing",
        "evaluating",
        "shadow_ready",
    ):
        response = client.post(
            f"/v1/evolution/opportunities/{opportunity_id}/advance",
            json={"target": status, "actor": "lab"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
    return body


@pytest.mark.parametrize(("method", "path"), OPPORTUNITY_ENDPOINTS)
def test_the_opportunity_surface_is_owner_gated(client: TestClient, method: str, path: str) -> None:
    kwargs: dict = {"headers": {"Authorization": "Bearer nope"}}
    if method == "post":
        kwargs["json"] = {}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_the_approve_route_requires_the_owner_session(client: TestClient) -> None:
    opportunity = create_opportunity(client)
    drive_to_shadow_ready(client, opportunity["opportunity_id"])
    path = f"/v1/evolution/opportunities/{opportunity['opportunity_id']}/approve"

    # No credential at all, and a garbage credential: the same 401.
    assert client.post(path, json={}, headers={"Authorization": ""}).status_code == 401
    assert client.post(path, json={}, headers={"Authorization": "Bearer nope"}).status_code == 401

    # With the real owner session it is accepted, and the approval is recorded
    # against the verified session rather than against anything in the body.
    approved = client.post(path, json={"note": "olur"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "owner_approved"
    assert approved.json()["approved_by"].startswith("owner_session:")


def test_an_opportunity_is_created_from_evidence(client: TestClient) -> None:
    opportunity = create_opportunity(client)
    assert opportunity["status"] == "idea"
    assert opportunity["scores"]["composite"] > 0
    assert opportunity["origin"][0]["kind"] == "ledger_event"
    assert opportunity["detail"]["evidence_verification"][0]["verified"] is True

    listed = client.get("/v1/evolution/opportunities").json()["opportunities"]
    assert [item["opportunity_id"] for item in listed] == [opportunity["opportunity_id"]]


@pytest.mark.parametrize(
    "override",
    [
        {"evidence_refs": []},
        {"evidence_refs": [{"kind": "vibes", "ref": "x"}]},
        {"scores": {**SCORES, "owner_relevance": 2.0}},
        {"scores": {k: v for k, v in SCORES.items() if k != "confidence"}},
        {"title": ""},
        {"title": "x" * 500},
        {"source": "made_up_source"},
        {"unexpected_field": 1},
    ],
)
def test_a_hostile_or_evidence_free_body_is_refused(client: TestClient, override: dict) -> None:
    response = client.post(
        "/v1/evolution/opportunities", json=evidence_backed_body(client, **override)
    )
    assert response.status_code == 422


def test_evidence_that_does_not_exist_is_refused_by_the_api(client: TestClient) -> None:
    body = evidence_backed_body(
        client, evidence_refs=[{"kind": "ledger_event", "ref": str(uuid.uuid4())}]
    )
    assert client.post("/v1/evolution/opportunities", json=body).status_code == 422


def test_the_lab_path_runs_to_shadow_ready_without_the_owner(client: TestClient) -> None:
    """The engine does all of this by itself — and stops at the wall."""
    opportunity = create_opportunity(client)
    final = drive_to_shadow_ready(client, opportunity["opportunity_id"])
    assert final["status"] == "shadow_ready"

    pending = client.get("/v1/evolution/shadow-ready").json()
    assert pending["count"] == 1
    assert pending["awaiting_approval"][0]["opportunity_id"] == (opportunity["opportunity_id"])
    assert "deployed" in pending["note"]


def test_advance_refuses_owner_approved_and_points_at_the_gate(
    client: TestClient,
) -> None:
    opportunity = create_opportunity(client)
    drive_to_shadow_ready(client, opportunity["opportunity_id"])
    response = client.post(
        f"/v1/evolution/opportunities/{opportunity['opportunity_id']}/advance",
        json={"target": "owner_approved", "actor": "owner"},
    )
    assert response.status_code == 403
    assert "approve" in response.json()["detail"]["message"]


def test_a_lab_actor_cannot_drive_a_production_side_transition(
    client: TestClient,
) -> None:
    opportunity = create_opportunity(client)
    drive_to_shadow_ready(client, opportunity["opportunity_id"])
    client.post(f"/v1/evolution/opportunities/{opportunity['opportunity_id']}/approve", json={})
    response = client.post(
        f"/v1/evolution/opportunities/{opportunity['opportunity_id']}/advance",
        json={"target": "qualifying", "actor": "lab"},
    )
    assert response.status_code == 403


def test_live_is_refused_while_no_release_evidence_exists(client: TestClient) -> None:
    opportunity = create_opportunity(client)
    opportunity_id = opportunity["opportunity_id"]
    drive_to_shadow_ready(client, opportunity_id)
    client.post(f"/v1/evolution/opportunities/{opportunity_id}/approve", json={})
    qualifying = client.post(
        f"/v1/evolution/opportunities/{opportunity_id}/advance",
        json={"target": "qualifying", "actor": "system"},
    )
    assert qualifying.status_code == 200
    live = client.post(
        f"/v1/evolution/opportunities/{opportunity_id}/advance",
        json={"target": "live", "actor": "system"},
    )
    assert live.status_code == 409
    assert live.json()["detail"]["error_class"] == "lifecycle_violation"


def test_an_illegal_transition_is_a_409(client: TestClient) -> None:
    opportunity = create_opportunity(client)
    response = client.post(
        f"/v1/evolution/opportunities/{opportunity['opportunity_id']}/advance",
        json={"target": "shadow_ready", "actor": "lab"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_class"] == "lifecycle_violation"


@pytest.mark.parametrize(
    "body",
    [
        {"target": "not_a_status", "actor": "lab"},
        {"target": "idea", "actor": "not_an_actor"},
        {"target": "idea"},
        {"target": "idea", "actor": "lab", "unexpected": 1},
    ],
)
def test_a_malformed_advance_body_is_refused(client: TestClient, body: dict) -> None:
    opportunity = create_opportunity(client)
    response = client.post(
        f"/v1/evolution/opportunities/{opportunity['opportunity_id']}/advance", json=body
    )
    assert response.status_code == 422


def test_an_unknown_opportunity_is_404(client: TestClient) -> None:
    assert client.get(f"/v1/evolution/opportunities/{uuid.uuid4()}").status_code == 404
    assert (
        client.post(
            f"/v1/evolution/opportunities/{uuid.uuid4()}/advance",
            json={"target": "researching", "actor": "lab"},
        ).status_code
        == 404
    )


# -------------------------------------------------------------------- policy


def test_policy_publishes_the_whole_contract(client: TestClient) -> None:
    policy = client.get("/v1/evolution/policy").json()
    assert policy["evolution_version"] == EVOLUTION_VERSION == 1

    lifecycle = policy["lifecycle"]
    assert lifecycle["statuses"][:10] == [
        "idea",
        "researching",
        "design_ready",
        "building",
        "testing",
        "evaluating",
        "shadow_ready",
        "owner_approved",
        "qualifying",
        "live",
    ]
    assert set(lifecycle["statuses"]) >= {
        "rejected",
        "superseded",
        "quarantined",
        "rolled_back",
    }
    assert lifecycle["owner_only_targets"] == ["owner_approved"]
    assert lifecycle["release_required_targets"] == ["live"]
    assert set(lifecycle["lab_forbidden_targets"]) == {
        "owner_approved",
        "qualifying",
        "live",
        "rolled_back",
    }
    assert lifecycle["transitions"]["live"] == ["rolled_back", "superseded"]

    assert policy["scoring"]["inputs"] == [
        "owner_relevance",
        "expected_utility",
        "recurrence",
        "confidence",
        "engineering_cost",
        "operational_risk",
    ]
    assert policy["scoring"]["weights"]["risk_veto_threshold"] == 0.7

    authority = policy["authority"]
    assert authority["engine_scope"] == "lab"
    assert set(authority["production_grants_never_granted"]) == {
        "deploy",
        "sign_release",
        "write_production_db",
        "read_production_secrets",
        "modify_policy_kernel",
    }
    assert not set(authority["grants"]) & set(authority["production_grants_never_granted"])

    assert {policy["policy_id"] for policy in policy["root_policies"]} == {
        "owner_identity",
        "approval_boundary",
        "deployment_authority",
        "audit_guarantees",
        "secret_boundaries",
        "sandbox_boundary",
        "rollback_guarantees",
        "security_policy_kernel",
    }
    assert policy["release_evidence_provider"] == "null"
    assert policy["actors"] == ["owner", "system", "lab"]
