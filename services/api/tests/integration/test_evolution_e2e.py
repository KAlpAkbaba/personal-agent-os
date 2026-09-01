"""M7 MANDATORY end-to-end demonstration against real PostgreSQL.

ACCEPTANCE_TESTS M7 "Mandatory end-to-end demonstration": with a deterministic
fixture capability absent beforehand, asking the running system to perform a
task requiring it must automatically produce, with NO owner intervention:

    CAPABILITY_MISSING
      -> composition check
      -> component-catalog check
      -> skill design (work order)
      -> implementation (isolated workspace + generate)
      -> tests (generated unit tests + eval set actually run)
      -> independent security/reviewer gate
      -> shadow
      -> canary
      -> promotion
      -> registry update
      -> original task resumes
      -> task succeeds

...and the same scenario with a DELIBERATELY DEFECTIVE implementation must be
rejected or rolled back with no owner intervention and production unaffected.

Everything is offline and deterministic: no model calls, no network, stdlib-only
generated code, bounded subprocesses. Budget well under ~150s.
"""

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.artifacts.models import Task
from app.config import Settings
from app.evolution.models import Capability, CapabilityGap, SkillVersion
from app.evolution.skills import SkillLayout
from app.main import create_app
from tests.integration.conftest import attach_owner

pytestmark = pytest.mark.integration

SUFFIX = uuid.uuid4().hex[:8]
HELPER_CAPABILITY = f"text.reverse_{SUFFIX}"
TARGET_CAPABILITY = f"text.slugify_{SUFFIX}"
COMPONENT_CAPABILITY = f"text.wordcount_{SUFFIX}"
BAD_CAPABILITY = f"text.checksum_{SUFFIX}"
RECOVERY_CAPABILITY = f"recovery.rollback_{SUFFIX}"
DEVICE_CAPABILITY = f"device.label_{SUFFIX}"
AUTHORIZED_ASSET = "owner_workstation"
ALL_CAPABILITIES = (
    HELPER_CAPABILITY,
    TARGET_CAPABILITY,
    COMPONENT_CAPABILITY,
    BAD_CAPABILITY,
    RECOVERY_CAPABILITY,
    DEVICE_CAPABILITY,
)

EXPECTED_STAGES = [
    "load_gap",
    "verify_composition_attempted",
    "work_order",
    "isolated_workspace",
    "generate",
    "supply_chain",
    "evaluate",
    "independent_review",
    "shadow",
    "canary",
    "publish",
    "register_capability",
    "resume_task",
]


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PAGENTOS_EVOLUTION_SKILLS_ROOT", str(tmp_path / "skills"))
    monkeypatch.setenv("PAGENTOS_EVOLUTION_WORK_ROOT", str(tmp_path / "work"))
    monkeypatch.setenv("PAGENTOS_EVOLUTION_GENERATOR", "deterministic")
    settings = Settings()
    app = create_app(settings)
    test_client = TestClient(app)
    # M9: /v1/evolution creates and promotes code - owner session required.
    attach_owner(app, test_client, settings)
    yield test_client
    runtime = app.state.evolution
    with runtime.session() as session:
        session.execute(
            delete(CapabilityGap).where(
                CapabilityGap.requested_capability.in_(ALL_CAPABILITIES)
            )
        )
        session.execute(
            delete(Capability).where(Capability.capability_id.in_(ALL_CAPABILITIES))
        )
        session.execute(
            delete(SkillVersion).where(SkillVersion.capability_id.in_(ALL_CAPABILITIES))
        )
        session.execute(delete(Task).where(Task.intent.like(f"%{SUFFIX}%")))
        session.commit()
    test_client.close()


def gap_body(capability_id: str, operation: str, outputs: list[str], **overrides) -> dict:
    body = {
        "requested_capability": capability_id,
        "request_text": f"M7 acceptance request {SUFFIX}: {operation}",
        "required_inputs": ["text"],
        "required_outputs": outputs,
        "spec": {
            "capability_id": capability_id,
            "operation": operation,
            "version": "0.1.0",
            "summary": f"generated {operation} capability for the M7 gate",
        },
    }
    body.update(overrides)
    return body


def detect(client: TestClient, body: dict, expect: int = 201) -> dict:
    response = client.post("/v1/evolution/gaps", json=body)
    assert response.status_code == expect, response.text
    return response.json()


def resolve(client: TestClient, gap_id: str) -> dict:
    response = client.post(f"/v1/evolution/gaps/{gap_id}/resolve")
    assert response.status_code == 200, response.text
    return response.json()


def test_m7_self_extension_story(client: TestClient) -> None:
    runtime = client.app.state.evolution
    skills_root = Path(runtime.skills_root)

    # --- 0. Seed the registry with one genuinely generated capability, so the
    # composition attempt later has a real catalog to search.
    helper_gap = detect(
        client, gap_body(HELPER_CAPABILITY, "reverse_text", ["reversed_text"])
    )
    helper = resolve(client, helper_gap["id"])
    assert helper["status"] == "registered", helper["summary"]

    # --- 1. CAPABILITY_MISSING: the capability we want is absent, so the task
    # that needs it is parked as FAILED_RECOVERABLE/capability_missing.
    assert client.get(f"/v1/evolution/capabilities/{TARGET_CAPABILITY}").status_code == 404
    task_id = runtime.resumer.record_capability_missing(
        f"Bu basligi url slug'a cevir ({SUFFIX})", TARGET_CAPABILITY
    )
    parked = runtime.resumer.get_task(task_id)
    assert parked["status"] == "FAILED_RECOVERABLE"
    assert parked["error_class"] == "capability_missing"

    # --- 2. Gap detection: the WHOLE resolution order, in order, with evidence.
    gap = detect(
        client,
        gap_body(
            TARGET_CAPABILITY,
            "slugify",
            ["slug"],
            task_id=str(task_id),
            resume_payload={"text": "Personal Agent OS 2026"},
        ),
    )
    assert gap["resolution"] == "generation"
    trail = {entry["step"]: entry for entry in gap["decision_trail"]}
    assert [entry["step"] for entry in gap["decision_trail"]] == [
        "request_received",
        "existing_capability",
        "composition",
        "configuration",
        "extension",
        "component_adaptation",
        "new_skill",
        "product_core_change",
    ]
    # 2a. composition check — attempted FIRST and reported insufficient
    composition = trail["composition"]
    assert composition["outcome"] == "insufficient"
    assert composition["evidence"]["attempted"] is True
    assert composition["evidence"]["missing_outputs"] == ["slug"]
    assert HELPER_CAPABILITY in [
        entry["capability_id"] for entry in composition["evidence"]["considered"]
    ]
    # 2b. component-catalog check — asked before generation, nothing matched
    component = trail["component_adaptation"]
    assert component["outcome"] == "insufficient"
    assert component["evidence"]["catalog"]
    assert composition["index"] < component["index"] < trail["new_skill"]["index"]

    # --- 3..11. Resolve: design -> implement -> tests -> review -> shadow ->
    # canary -> promotion -> registry update -> task resumes.
    result = resolve(client, gap["id"])
    assert result["status"] == "registered", result["summary"]
    stages = {stage["name"]: stage for stage in result["stages"]}
    assert [stage["name"] for stage in result["stages"]] == EXPECTED_STAGES

    # 3. skill design (the machine-readable work order)
    assert stages["work_order"]["detail"]["resolution_path"] == "generation_from_scratch"
    # 4. implementation inside an isolated workspace
    assert stages["isolated_workspace"]["status"] == "ok"
    assert stages["generate"]["detail"]["lifecycle_stage"] == "sandbox"
    # supply chain scanned before anything ran
    assert stages["supply_chain"]["detail"]["ok"] is True

    # 5. tests + evals really ran and were scored (§9)
    evaluation = stages["evaluate"]["detail"]
    assert evaluation["passed"] is True
    assert evaluation["tests"]["total"] >= 8 and evaluation["tests"]["failed"] == 0
    assert evaluation["evals"]["total"] == evaluation["evals"]["passed"] >= 3
    score = evaluation["score"]
    assert score["functional_success_rate"] == 1.0
    assert score["regression_count"] == 0
    assert score["security_findings"] == 0
    assert set(score["metrics"]) == {"success_rate", "p95_latency_ms", "case_count"}

    # 6. independent security/reviewer gate re-ran everything itself
    review = stages["independent_review"]["detail"]
    assert review["approved"] is True
    checks = {check["name"]: check["passed"] for check in review["checks"]}
    assert checks["tests_rerun_pass"] and checks["evals_rerun_pass"]
    assert checks["tests_detect_regression"] and checks["sandbox_confined"]
    assert checks["permissions_scoped"] and checks["supply_chain_clean"]
    assert review["deny_by_default"] is True

    # 7/8. shadow then canary, both with recorded evidence
    shadow = stages["shadow"]["detail"]
    assert shadow["stage"] == "shadow" and shadow["mismatches"] == 0
    assert shadow["detail"]["served"] is False
    canary = stages["canary"]["detail"]
    assert canary["stage"] == "canary" and canary["not_worse_than_incumbent"] is True
    assert canary["samples"] >= 1

    # 9/10. promotion + registry update
    capability = client.get(f"/v1/evolution/capabilities/{TARGET_CAPABILITY}").json()
    assert capability["status"] == "production"
    assert capability["dispatchable"] is True
    assert capability["manifest"]["risk_class"] == "low"
    assert capability["manifest"]["network_permissions"] == []
    published = Path(result["source_ref"])
    assert published.is_relative_to(skills_root)
    layout = SkillLayout(
        published, TARGET_CAPABILITY.replace(".", "_"), TARGET_CAPABILITY, "0.1.0"
    )
    assert layout.missing_paths() == []
    versions = client.get(
        "/v1/evolution/skill-versions", params={"capability_id": TARGET_CAPABILITY}
    ).json()["skill_versions"]
    assert [v["status"] for v in versions] == ["registered"]
    assert versions[0]["evaluation"]["lifecycle"]["stage"] == "active"
    assert [e["stage"] for e in versions[0]["evaluation"]["lifecycle"]["history"]] == [
        "sandbox",
        "validated",
        "shadow",
        "canary",
        "active",
    ]

    # 11. the ORIGINAL task resumed and SUCCEEDED — no owner intervention
    assert result["resumption"]["status"] == "COMPLETED"
    assert result["resumption"]["output"] == {"slug": "personal-agent-os-2026"}
    completed = runtime.resumer.get_task(task_id)
    assert completed["status"] == "COMPLETED"
    assert completed["error_class"] is None

    # 12. Auditability: all nine questions answered for this evolution.
    audit = client.get(f"/v1/evolution/gaps/{gap['id']}/audit").json()
    for question in audit["questions"]:
        assert audit["answers"][question], question
    assert audit["answers"]["what_changed"]["lifecycle_stage"] == "active"
    assert audit["answers"]["who_reviewed"]["approved"] is True
    assert audit["answers"]["permissions_granted"]["granted"] == {}


def test_component_adaptation_path_end_to_end(client: TestClient) -> None:
    """Resolution step 4: a vetted, pinned local component is adapted instead of
    writing a capability from scratch — and still passes every gate."""
    gap = detect(client, gap_body(COMPONENT_CAPABILITY, "word_count", ["count"]))
    component = next(
        e for e in gap["decision_trail"] if e["step"] == "component_adaptation"
    )
    assert component["outcome"] == "satisfied"
    assert component["evidence"]["component"]["name"] == "text_metrics_kit"
    assert component["evidence"]["install"] == "offline-local-catalog"

    result = resolve(client, gap["id"])
    assert result["status"] == "registered", result["summary"]
    assert result["stages"][2]["detail"]["resolution_path"] == "component_adaptation"

    capability = client.get(f"/v1/evolution/capabilities/{COMPONENT_CAPABILITY}").json()
    dependencies = capability["manifest"]["dependencies"]
    assert [d["name"] for d in dependencies] == ["text_metrics_kit"]
    assert dependencies[0]["source"] == "local-component-catalog"
    assert dependencies[0]["digest"].startswith("sha256:")
    assert dependencies[0]["version"] == "2.1.0"

    dispatched = client.app.state.evolution.dispatcher.dispatch(
        COMPONENT_CAPABILITY, {"text": "bir iki uc"}
    )
    assert dispatched.output == {"count": 3}


def test_defective_implementation_is_rejected_with_no_owner_intervention(
    client: TestClient,
) -> None:
    """The same scenario with a deliberately defective implementation: rejected
    automatically, production unaffected, the system still usable."""
    good_gap = detect(client, gap_body(TARGET_CAPABILITY, "slugify", ["slug"]))
    assert resolve(client, good_gap["id"])["status"] == "registered"
    production_before = client.get(
        f"/v1/evolution/capabilities/{TARGET_CAPABILITY}"
    ).json()

    bad_body = gap_body(BAD_CAPABILITY, "char_checksum", ["checksum"])
    bad_body["spec"]["cases"] = [
        {"input": "abc", "expected": 96354},
        {"input": "abd", "expected": 999999},  # cannot ever be satisfied
    ]
    bad_gap = detect(client, bad_body)
    assert bad_gap["resolution"] == "generation"

    bad = resolve(client, bad_gap["id"])
    assert bad["status"] == "rejected"
    bad_stages = [stage["name"] for stage in bad["stages"]]
    assert "register_capability" not in bad_stages
    assert "publish" not in bad_stages
    assert "shadow" not in bad_stages  # never even reached rollout

    # Production is exactly what it was, and still dispatches.
    assert client.get(f"/v1/evolution/capabilities/{BAD_CAPABILITY}").status_code == 404
    assert (
        client.get(f"/v1/evolution/capabilities/{TARGET_CAPABILITY}").json()
        == production_before
    )
    dispatched = client.app.state.evolution.dispatcher.dispatch(
        TARGET_CAPABILITY, {"text": "Merhaba Dunya"}
    )
    assert dispatched.output == {"slug": "merhaba-dunya"}

    rejected = client.get(
        "/v1/evolution/skill-versions", params={"capability_id": BAD_CAPABILITY}
    ).json()["skill_versions"]
    assert [v["status"] for v in rejected] == ["rejected"]
    assert "release gates failed" in rejected[0]["rejected_reason"]
    # The gap is retryable, not lost — and no owner was asked anything.
    assert client.get(
        "/v1/evolution/gaps", params={"status": "open"}
    ).json()["gaps"][0]["id"] == bad_gap["id"]


def test_improvement_and_rollback_end_to_end(client: TestClient) -> None:
    """Telemetry -> improved candidate -> benchmark -> promote -> rollback."""
    runtime = client.app.state.evolution
    gap = detect(client, gap_body(TARGET_CAPABILITY, "slugify", ["slug"]))
    assert resolve(client, gap["id"])["status"] == "registered"
    incumbent = runtime.registry.resolve(TARGET_CAPABILITY)
    old_source_ref = incumbent["skill_version"]["source_ref"]

    # Production telemetry: Turkish titles keep coming back wrong.
    version_id = uuid.UUID(incumbent["skill_version"]["id"])
    for index in range(6):
        runtime.registry.record_telemetry(
            version_id, {"ok": index % 3 == 0, "latency_ms": 1.1}
        )
    proposal = runtime.improvement_detector.detect(TARGET_CAPABILITY)
    assert proposal is not None
    assert proposal.weakness == "recurring_failures"

    improved = runtime.improver.improve(
        proposal,
        {
            "capability_id": TARGET_CAPABILITY,
            "operation": "slugify_tr",
            "summary": "turkish-aware url slug",
            "cases": [
                {"input": "Türkçe Başlık", "expected": "turkce-baslik"},
                {"input": "IŞIK ve GÖLGE", "expected": "isik-ve-golge"},
                {"input": "Personal Agent OS", "expected": "personal-agent-os"},
            ],
        },
    )
    assert improved.status == "promoted", improved.summary
    assert improved.benchmark["superior"] is True
    assert improved.benchmark["incumbent"]["success_rate"] < 1.0
    assert improved.benchmark["candidate"]["success_rate"] == 1.0

    now = runtime.registry.resolve(TARGET_CAPABILITY)
    assert now["version"] == "0.2.0"
    assert now["manifest"]["rollback_version"] == "0.1.0"
    assert runtime.dispatcher.dispatch(TARGET_CAPABILITY, {"text": "Türkçe Başlık"}).output == (
        {"slug": "turkce-baslik"}
    )

    # The old version is still rollback-capable, and rollback restores service.
    restored = runtime.improver.rollback(TARGET_CAPABILITY, "0.1.0")
    assert restored["version"] == "0.1.0"
    serving = runtime.registry.resolve(TARGET_CAPABILITY)
    assert serving["skill_version"]["source_ref"] == old_source_ref
    assert runtime.dispatcher.dispatch(TARGET_CAPABILITY, {"text": "Agent OS"}).output == (
        {"slug": "agent-os"}
    )


def _operational_grant_body() -> dict:
    body = gap_body(DEVICE_CAPABILITY, "slugify", ["slug"])
    body["intent"] = "operational_capability"
    body["authorized_asset"] = AUTHORIZED_ASSET
    body["spec"].update(
        {
            "device_permissions": ["serial_port"],
            "network_permissions": ["device.local"],
            "authorized_asset": AUTHORIZED_ASSET,
        }
    )
    return body


def test_an_unverified_asset_reference_cannot_award_itself_permissions(
    client: TestClient,
) -> None:
    """Deny-by-default over HTTP (M7 security review #1): with no configured
    authorization source, asserting an asset reference grants nothing."""
    gap = detect(client, _operational_grant_body())
    assert gap["resolution"] == "generation"
    result = resolve(client, gap["id"])
    assert result["status"] == "rejected", result["summary"]
    assert client.get(f"/v1/evolution/capabilities/{DEVICE_CAPABILITY}").status_code == 404


def test_owner_authorized_operational_grant_is_not_blocked(
    client: TestClient, monkeypatch
) -> None:
    """The boundary: the recovery/security-root rule constrains self-modification
    only — an owner-authorized operational capability with device/network grants
    reaches production (once a VERIFIED authorization source exists), while
    self-modification of the recovery root does not."""
    monkeypatch.setenv(
        "PAGENTOS_EVOLUTION_AUTHORIZATIONS",
        json.dumps(
            {
                AUTHORIZED_ASSET: {
                    "device_permissions": ["serial_port"],
                    "network_permissions": ["device.local"],
                }
            }
        ),
    )
    gap = detect(client, _operational_grant_body())
    assert gap["resolution"] == "generation"
    result = resolve(client, gap["id"])
    assert result["status"] == "registered", result["summary"]

    manifest = client.get(f"/v1/evolution/capabilities/{DEVICE_CAPABILITY}").json()[
        "manifest"
    ]
    assert manifest["device_permissions"] == ["serial_port"]
    assert manifest["risk_class"] == "high"
    assert manifest["creation_reason"]["authorized_asset"] == AUTHORIZED_ASSET
    audit = client.get(f"/v1/evolution/gaps/{gap['id']}/audit").json()
    assert audit["answers"]["permissions_granted"]["reviewer_approved"] is True
    assert audit["answers"]["permissions_granted"]["authorized_asset"] == AUTHORIZED_ASSET


def test_core_recovery_self_modification_is_refused_end_to_end(client: TestClient) -> None:
    gap = detect(
        client,
        {
            "requested_capability": RECOVERY_CAPABILITY,
            "request_text": f"Kurtarma yolunu kendi kendine degistir ({SUFFIX}).",
            "required_inputs": ["text"],
            "required_outputs": ["slug"],
            "intent": "self_modification",
            "target_component": "recovery-supervisor",
        },
    )
    assert gap["resolution"] == "product_change_required"
    assert gap["status"] == "abandoned"
    trail = {entry["step"]: entry for entry in gap["decision_trail"]}
    assert trail["product_core_change"]["outcome"] == "refused"

    result = resolve(client, gap["id"])
    assert result["status"] == "refused"
    assert result["stages"][-1]["detail"]["error_class"] == "product_change_required"
    assert (
        client.get(
            "/v1/evolution/skill-versions",
            params={"capability_id": RECOVERY_CAPABILITY},
        ).json()["skill_versions"]
        == []
    )
