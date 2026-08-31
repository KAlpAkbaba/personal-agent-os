"""M7 acceptance E2E: the whole self-extension story against real PostgreSQL.

Choreographed through the public REST surface (plus the runtime's task helper,
since there is no task-creation endpoint yet), on a temp skills/sandbox root:

1. a capability intentionally ABSENT from the registry is requested — the task
   fails with ``capability_missing`` and the gap detector records the gap;
2. composition is ATTEMPTED FIRST against the real registry (which already holds
   a previously generated capability) and reported insufficient, with evidence;
3. a skill is generated in an ISOLATED workspace under the sandbox root;
4. its generated tests and eval set RUN and pass, producing a §9 release score;
5. an INDEPENDENT review re-runs them and proves they are not vacuous;
6. only then is the capability registered — and it becomes dispatchable;
7. the ORIGINAL failing task is resumed and COMPLETES using the new capability;
8. a deliberately-bad candidate is then rejected and production is unaffected.

Everything is offline and deterministic: no model calls, no network, stdlib-only
generated code, subprocesses bounded. Budget well under ~120s.
"""

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

pytestmark = pytest.mark.integration

SUFFIX = uuid.uuid4().hex[:8]
HELPER_CAPABILITY = f"text.reverse_{SUFFIX}"
TARGET_CAPABILITY = f"text.slugify_{SUFFIX}"
BAD_CAPABILITY = f"text.wordcount_{SUFFIX}"
RECOVERY_CAPABILITY = f"recovery.rollback_{SUFFIX}"
ALL_CAPABILITIES = (
    HELPER_CAPABILITY,
    TARGET_CAPABILITY,
    BAD_CAPABILITY,
    RECOVERY_CAPABILITY,
)


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PAGENTOS_EVOLUTION_SKILLS_ROOT", str(tmp_path / "skills"))
    monkeypatch.setenv("PAGENTOS_EVOLUTION_WORK_ROOT", str(tmp_path / "work"))
    monkeypatch.setenv("PAGENTOS_EVOLUTION_GENERATOR", "deterministic")
    app = create_app(Settings())
    test_client = TestClient(app)
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


def detect(client: TestClient, body: dict) -> dict:
    response = client.post("/v1/evolution/gaps", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def resolve(client: TestClient, gap_id: str) -> dict:
    response = client.post(f"/v1/evolution/gaps/{gap_id}/resolve")
    assert response.status_code == 200, response.text
    return response.json()


def test_m7_self_extension_story(client: TestClient, tmp_path) -> None:
    runtime = client.app.state.evolution
    skills_root = Path(runtime.skills_root)

    # --- 0. Seed the registry with one genuinely generated capability, so the
    # composition attempt later has a real catalog to search.
    helper_gap = detect(
        client, gap_body(HELPER_CAPABILITY, "reverse_text", ["reversed_text"])
    )
    helper = resolve(client, helper_gap["id"])
    assert helper["status"] == "registered", helper["summary"]

    # --- 1. The capability we actually want is ABSENT from the registry.
    assert client.get(f"/v1/evolution/capabilities/{TARGET_CAPABILITY}").status_code == 404
    task_id = runtime.resumer.record_capability_missing(
        f"Bu basligi url slug'a cevir ({SUFFIX})", TARGET_CAPABILITY
    )
    assert runtime.resumer.get_task(task_id)["status"] == "FAILED_RECOVERABLE"
    assert runtime.resumer.get_task(task_id)["error_class"] == "capability_missing"

    # --- 2. Gap detection: composition attempted FIRST, reported insufficient.
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
    assert trail["existing_capability"]["outcome"] == "insufficient"
    composition = trail["composition"]
    assert composition["outcome"] == "insufficient"
    assert composition["evidence"]["attempted"] is True
    assert composition["evidence"]["missing_outputs"] == ["slug"]
    assert HELPER_CAPABILITY in [
        entry["capability_id"] for entry in composition["evidence"]["considered"]
    ]
    assert composition["index"] < trail["new_skill"]["index"]

    # --- 3..7. Resolve: generate -> run tests+evals -> independent review ->
    # register -> resume the original task.
    result = resolve(client, gap["id"])
    assert result["status"] == "registered", result["summary"]
    stages = {stage["name"]: stage for stage in result["stages"]}
    order = [stage["name"] for stage in result["stages"]]
    assert order == [
        "load_gap",
        "verify_composition_attempted",
        "work_order",
        "isolated_workspace",
        "generate",
        "evaluate",
        "independent_review",
        "publish",
        "register_capability",
        "resume_task",
    ]

    # 4. tests and evals really ran and were scored (§9).
    evaluation = stages["evaluate"]["detail"]
    assert evaluation["passed"] is True
    assert evaluation["tests"]["total"] >= 8 and evaluation["tests"]["failed"] == 0
    assert evaluation["evals"]["total"] == evaluation["evals"]["passed"] >= 3
    score = evaluation["score"]
    assert score["functional_success_rate"] == 1.0
    assert score["regression_count"] == 0
    assert score["security_findings"] == 0
    assert set(score["metrics"]) == {"success_rate", "p95_latency_ms", "case_count"}

    # 5. independent review re-ran everything and proved the tests bite.
    review = stages["independent_review"]["detail"]
    assert review["approved"] is True
    checks = {check["name"]: check["passed"] for check in review["checks"]}
    assert checks["tests_rerun_pass"] and checks["evals_rerun_pass"]
    assert checks["tests_detect_regression"] and checks["sandbox_confined"]

    # 6. registered, dispatchable, and on disk in the §10 layout.
    capability = client.get(f"/v1/evolution/capabilities/{TARGET_CAPABILITY}").json()
    assert capability["status"] == "production"
    assert capability["dispatchable"] is True
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
    assert versions[0]["manifest_digest"]

    # 7. the ORIGINAL task resumed and completed with the new capability.
    assert result["resumption"]["status"] == "COMPLETED"
    assert result["resumption"]["output"] == {"slug": "personal-agent-os-2026"}
    completed = runtime.resumer.get_task(task_id)
    assert completed["status"] == "COMPLETED"
    assert completed["error_class"] is None
    assert client.get("/v1/evolution/gaps").status_code == 200

    # --- 8. A deliberately-bad candidate is rejected; production is unaffected.
    production_before = client.get(
        f"/v1/evolution/capabilities/{TARGET_CAPABILITY}"
    ).json()
    bad_body = gap_body(BAD_CAPABILITY, "word_count", ["count"])
    bad_body["spec"]["cases"] = [
        {"input": "one two three", "expected": 3},
        {"input": "one two", "expected": 99},  # cannot be satisfied
    ]
    bad_gap = detect(client, bad_body)
    assert bad_gap["resolution"] == "generation"
    bad = resolve(client, bad_gap["id"])

    assert bad["status"] == "rejected"
    bad_order = [stage["name"] for stage in bad["stages"]]
    assert "register_capability" not in bad_order and "publish" not in bad_order
    assert client.get(f"/v1/evolution/capabilities/{BAD_CAPABILITY}").status_code == 404
    rejected = client.get(
        "/v1/evolution/skill-versions", params={"capability_id": BAD_CAPABILITY}
    ).json()["skill_versions"]
    assert [v["status"] for v in rejected] == ["rejected"]
    assert "release gates failed" in rejected[0]["rejected_reason"]
    assert not (skills_root / BAD_CAPABILITY.replace(".", "_")).exists()

    # Production is exactly what it was, and still dispatches.
    assert client.get(f"/v1/evolution/capabilities/{TARGET_CAPABILITY}").json() == (
        production_before
    )
    dispatched = runtime.dispatcher.dispatch(TARGET_CAPABILITY, {"text": "Merhaba Dunya"})
    assert dispatched.output == {"slug": "merhaba-dunya"}


def test_core_recovery_request_is_refused_end_to_end(client: TestClient) -> None:
    """A gap that would require touching the recovery root is refused and never
    reaches a generator (constitution §6, EVOLUTION_ENGINE_SPEC §12)."""
    gap = detect(
        client,
        {
            "requested_capability": RECOVERY_CAPABILITY,
            "request_text": f"Yeni bir kurtarma yolu uret ({SUFFIX}).",
            "required_inputs": ["text"],
            "required_outputs": ["slug"],
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
