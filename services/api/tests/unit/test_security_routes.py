"""M8 unit tests: the /v1/security REST surface, against an injected SQLite
runtime and an in-memory object store (everything offline)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.object_store import InMemoryObjectStore
from app.security.runtime import SecurityRuntime
from tests.identity_support import authenticate
from tests.unit.test_security_support import (
    ARTIFACT_TABLES,
    FIXTURE_ROOT,
    copy_fixture,
    make_engine,
)


def enroll_body(**overrides):
    body = {
        "asset_ref": "lab-web-01",
        "name": "Lab web host 01",
        "kind": "host",
        "locator": "10.20.30.40",
        "environment": "lab",
        "allowed_testing": {"configuration_audit": True, "remediation": True},
        "allowed_permissions": {"network_permissions": ["lab.example.test"]},
        "constraints": {"max_disruption": "low", "config_roots": [str(FIXTURE_ROOT)]},
        "evidence": {
            "authorization": "owner_or_company_authorized",
            "recorded_by": "owner",
        },
        "valid_until": (datetime.now(UTC) + timedelta(days=90)).isoformat(),
    }
    body.update(overrides)
    return body


@pytest.fixture()
def client(tmp_path) -> TestClient:
    engine = make_engine()
    for table in ARTIFACT_TABLES:
        table.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.state.security = SecurityRuntime(
        settings, engine=engine, store=InMemoryObjectStore()
    )
    test_client = TestClient(app)
    # M9: every /v1/security endpoint requires an owner session.
    authenticate(app, test_client, settings=settings)
    yield test_client
    test_client.close()


@pytest.fixture()
def enrolled(client: TestClient) -> TestClient:
    assert client.post("/v1/security/assets", json=enroll_body()).status_code == 201
    return client


# --------------------------------------------------------------- assets


def test_registry_starts_empty(client: TestClient) -> None:
    response = client.get("/v1/security/assets")
    assert response.status_code == 200
    assert response.json()["assets"] == []


def test_enroll_returns_201_and_the_stored_row(client: TestClient) -> None:
    response = client.post("/v1/security/assets", json=enroll_body())
    assert response.status_code == 201
    body = response.json()
    assert body["asset_ref"] == "lab-web-01"
    assert body["status"] == "active"
    assert body["constraints"]["max_disruption"] == "low"


def test_enroll_without_evidence_is_422(client: TestClient) -> None:
    payload = enroll_body()
    payload.pop("evidence")
    assert client.post("/v1/security/assets", json=payload).status_code == 422


def test_enroll_with_empty_evidence_is_422(client: TestClient) -> None:
    response = client.post("/v1/security/assets", json=enroll_body(evidence={}))
    assert response.status_code == 422
    assert response.json()["detail"]["error_class"] == "validation_error"


def test_duplicate_enrollment_is_409(enrolled: TestClient) -> None:
    response = enrolled.post("/v1/security/assets", json=enroll_body())
    assert response.status_code == 409
    assert response.json()["detail"]["error_class"] == "already_enrolled"


def test_unknown_asset_is_404(client: TestClient) -> None:
    response = client.get("/v1/security/assets/nope")
    assert response.status_code == 404
    assert response.json()["detail"]["error_class"] == "not_found"


def test_extra_fields_are_rejected(client: TestClient) -> None:
    assert (
        client.post("/v1/security/assets", json=enroll_body(surprise="x")).status_code == 422
    )


def test_patch_changes_scope_and_cannot_repoint_the_locator(enrolled: TestClient) -> None:
    response = enrolled.patch(
        "/v1/security/assets/lab-web-01",
        json={"allowed_testing": {"configuration_audit": True}, "reason": "narrowed"},
    )
    assert response.status_code == 200
    assert response.json()["allowed_testing"] == {"configuration_audit": True}

    # `locator` is not part of the schema at all.
    rejected = enrolled.patch("/v1/security/assets/lab-web-01", json={"locator": "8.8.8.8"})
    assert rejected.status_code == 422
    assert enrolled.get("/v1/security/assets/lab-web-01").json()["locator"] == "10.20.30.40"


def test_suspend_and_revoke(enrolled: TestClient) -> None:
    assert (
        enrolled.post("/v1/security/assets/lab-web-01/suspend", json={}).json()["status"]
        == "suspended"
    )
    assert (
        enrolled.post(
            "/v1/security/assets/lab-web-01/revoke", json={"reason": "decommissioned"}
        ).json()["status"]
        == "revoked"
    )


# ---------------------------------------------------------- assessments


def test_in_scope_assessment_returns_201_with_findings(enrolled: TestClient) -> None:
    response = enrolled.post("/v1/security/assessments", json={"target": "10.20.30.40"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["findings_total"] > 0
    assert body["scope_decision"]["allowed"] is True


def test_out_of_scope_assessment_is_403_and_adds_no_asset(enrolled: TestClient) -> None:
    response = enrolled.post("/v1/security/assessments", json={"target": "203.0.113.9"})
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["error_class"] == "out_of_scope"
    assert detail["details"]["decision"]["reason"] == "no_authorized_asset"

    assert [a["asset_ref"] for a in enrolled.get("/v1/security/assets").json()["assets"]] == [
        "lab-web-01"
    ]
    audit = enrolled.get("/v1/security/audit", params={"action": "assessment_refused"}).json()
    assert audit["events"][0]["requested_target"] == "203.0.113.9"


def test_spoofed_hostname_is_403(enrolled: TestClient) -> None:
    response = enrolled.post(
        "/v1/security/assessments", json={"target": "10.20.30.40.evil.com"}
    )
    assert response.status_code == 403


def test_repeated_runs_take_no_further_approval(enrolled: TestClient) -> None:
    for _ in range(3):
        assert (
            enrolled.post(
                "/v1/security/assessments", json={"target": "10.20.30.40"}
            ).status_code
            == 201
        )
    grants = enrolled.get("/v1/security/audit", params={"action": "enrolled"}).json()
    changes = enrolled.get("/v1/security/audit", params={"action": "scope_changed"}).json()
    assert len(grants["events"]) == 1
    assert changes["events"] == []


def test_assessment_listing_and_detail(enrolled: TestClient) -> None:
    created = enrolled.post(
        "/v1/security/assessments", json={"target": "10.20.30.40"}
    ).json()
    listed = enrolled.get("/v1/security/assessments").json()["assessments"]
    assert [a["id"] for a in listed] == [created["id"]]
    detail = enrolled.get(f"/v1/security/assessments/{created['id']}").json()
    assert len(detail["findings"]) == created["result"]["findings_total"]
    assert enrolled.get("/v1/security/assessments/not-a-uuid").status_code == 422


def test_assessment_body_is_bounded(enrolled: TestClient) -> None:
    assert (
        enrolled.post("/v1/security/assessments", json={"target": "x" * 600}).status_code
        == 422
    )
    assert (
        enrolled.post(
            "/v1/security/assessments", json={"target": "10.20.30.40", "nope": 1}
        ).status_code
        == 422
    )


# -------------------------------------------------------------- artifact


def test_assessment_publishes_a_security_artifact(enrolled: TestClient) -> None:
    created = enrolled.post(
        "/v1/security/assessments", json={"target": "10.20.30.40"}
    ).json()
    response = enrolled.post(f"/v1/security/assessments/{created['id']}/artifact")
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "security_report"
    assert {r["format"] for r in body["renders"]} == {"pdf", "html"}
    assert body["executive_summary"]


# -------------------------------------------------------------- findings


def test_findings_listing_and_filters(enrolled: TestClient) -> None:
    enrolled.post("/v1/security/assessments", json={"target": "10.20.30.40"})
    findings = enrolled.get("/v1/security/findings").json()["findings"]
    assert findings
    criticals = enrolled.get(
        "/v1/security/findings", params={"severity": "critical"}
    ).json()["findings"]
    assert criticals and all(f["severity"] == "critical" for f in criticals)
    assert (
        enrolled.get("/v1/security/findings", params={"severity": "apocalyptic"}).status_code
        == 422
    )


def test_remediate_defaults_to_propose(enrolled: TestClient) -> None:
    enrolled.post("/v1/security/assessments", json={"target": "10.20.30.40"})
    findings = enrolled.get("/v1/security/findings").json()["findings"]
    target = next(f for f in findings if f["evidence"]["check_id"] == "debug_mode_enabled")

    response = enrolled.post(f"/v1/security/findings/{target['id']}/remediate", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "propose"
    assert body["changed"] is False


def test_constraint_violating_remediation_is_409(enrolled: TestClient) -> None:
    enrolled.post("/v1/security/assessments", json={"target": "10.20.30.40"})
    findings = enrolled.get("/v1/security/findings").json()["findings"]
    ssh = next(f for f in findings if f["evidence"]["check_id"] == "ssh_root_login_permitted")

    response = enrolled.post(
        f"/v1/security/findings/{ssh['id']}/remediate", json={"mode": "apply"}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_class"] == "constraint_violation"


def test_dry_run_against_a_writable_copy(client: TestClient, tmp_path) -> None:
    root = copy_fixture(tmp_path)
    payload = enroll_body(
        constraints={"max_disruption": "low", "config_roots": [str(root)]}
    )
    client.post("/v1/security/assets", json=payload)
    client.post("/v1/security/assessments", json={"target": "10.20.30.40"})
    findings = client.get("/v1/security/findings").json()["findings"]
    debug = next(f for f in findings if f["evidence"]["check_id"] == "debug_mode_enabled")

    response = client.post(
        f"/v1/security/findings/{debug['id']}/remediate", json={"mode": "dry_run"}
    )
    assert response.status_code == 200
    assert response.json()["proposed_line"] == "debug=false"
    assert "debug=true" in (root / "app.env").read_text(encoding="utf-8")


def test_unknown_remediation_mode_is_422(enrolled: TestClient) -> None:
    enrolled.post("/v1/security/assessments", json={"target": "10.20.30.40"})
    finding = enrolled.get("/v1/security/findings").json()["findings"][0]
    assert (
        enrolled.post(
            f"/v1/security/findings/{finding['id']}/remediate", json={"mode": "yolo"}
        ).status_code
        == 422
    )


# ----------------------------------------------------- audit + scope check


def test_audit_records_the_whole_authorization_story(enrolled: TestClient) -> None:
    enrolled.post("/v1/security/assessments", json={"target": "10.20.30.40"})
    enrolled.post("/v1/security/assessments", json={"target": "203.0.113.9"})

    events = enrolled.get("/v1/security/audit").json()["events"]
    actions = [e["action"] for e in events]
    assert set(actions) == {"enrolled", "assessment_authorized", "assessment_refused"}

    refused = enrolled.get("/v1/security/audit", params={"allowed": False}).json()["events"]
    assert all(e["allowed"] is False for e in refused)


def test_scope_check_previews_without_running_or_auditing(enrolled: TestClient) -> None:
    allowed = enrolled.post(
        "/v1/security/scope/check", json={"target": "10.20.30.40"}
    ).json()
    assert allowed["allowed"] is True
    assert allowed["matched_by"] == "exact_address"

    refused = enrolled.post(
        "/v1/security/scope/check", json={"target": "10.20.30.40.evil.com"}
    ).json()
    assert refused["allowed"] is False
    assert refused["reason"] == "no_authorized_asset"
    assert refused["message"]  # Turkish explanation for the owner

    assert enrolled.get("/v1/security/assessments").json()["assessments"] == []
    assert (
        enrolled.get("/v1/security/audit", params={"action": "assessment_refused"}).json()[
            "events"
        ]
        == []
    )


def test_health_reports_the_security_posture(enrolled: TestClient) -> None:
    with enrolled as ready:
        check = ready.get("/v1/system/health").json()["checks"]["security"]
    assert check["status"] == "ok"
    assert check["scope_authority"] == "authorized_asset_registry"
    assert check["fail_safe"] is True
    assert check["network_scanning"] is False
    assert check["checks_available"] >= 10
