"""M8 integration: the whole authorized-security story against the real stack.

Requires the compose stack (postgres + minio). Everything remains OFFLINE with
respect to the *target*: the only thing assessed is the local, tracked fixture
configuration bundle copied into tmp_path. There is no network scanning, no
probing and no third-party tooling — the collector opens files and matches
regexes.

The full story, in one test:

    enroll asset -> run assessment -> findings recorded -> security artifact
    with renders -> remediation within constraints -> audit trail complete and
    secret-free

plus the negative half: an out-of-scope attempt is refused, appears in the
audit as refused, and creates no asset.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.artifacts import service as artifact_service
from app.artifacts.models import ARTIFACT_STATE_READY
from app.artifacts.renderers import content_hash
from app.config import Settings
from app.object_store import S3ObjectStore
from app.security.artifacts import publish_assessment_artifact
from app.security.errors import SecurityError, SecurityErrorClass
from app.security.models import (
    ARTIFACT_KIND_SECURITY_REPORT,
    AUTHORIZATION_GRANT_ACTIONS,
    AuthorizedAsset,
    SecurityAssessment,
    SecurityFinding,
)
from app.security.provider import RegistryAuthorizationProvider
from app.security.redaction import contains_secret
from app.security.runtime import SecurityRuntime

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = API_ROOT / "tests" / "fixtures" / "security_target"

PLANTED_SECRETS = (
    "Fak3-Placeholder-Password-Value",
    "AKIA" + "IOSFODNN7EXAMPLE",
    "placeholder-api-key-0000000000000000",
    "Fak3-Placeholder-Db-Pass",
)


@pytest.fixture(autouse=True)
def _ensure_bucket(settings: Settings) -> None:
    S3ObjectStore.from_settings(settings).ensure_bucket()


@pytest.fixture()
def runtime(settings: Settings) -> SecurityRuntime:
    return SecurityRuntime(settings)


@pytest.fixture()
def target_root(tmp_path) -> Path:
    """A writable copy so remediation can actually change a file."""
    root = tmp_path / "security_target"
    shutil.copytree(FIXTURE_ROOT, root)
    return root


@pytest.fixture()
def asset_ref() -> str:
    # Unique per run: the registry enforces a UNIQUE asset_ref and this suite
    # runs against a persistent database.
    return f"lab-e2e-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def locator() -> str:
    return f"10.90.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"


def _enroll(runtime: SecurityRuntime, asset_ref: str, locator: str, root: Path) -> dict:
    return runtime.registry.enroll(
        asset_ref=asset_ref,
        name="M8 end-to-end lab host",
        kind="host",
        locator=locator,
        environment="lab",
        allowed_testing={"configuration_audit": True, "remediation": True},
        allowed_permissions={"network_permissions": ["lab.example.test"]},
        constraints={"max_disruption": "low", "config_roots": [str(root)]},
        evidence={
            "authorization": "owner_or_company_authorized",
            "recorded_by": "owner",
            "reference": "OWNER-LAB-M8",
        },
        valid_until=datetime.now(UTC) + timedelta(days=30),
    )


def _events(runtime: SecurityRuntime, asset_ref: str) -> list[dict]:
    return runtime.registry.list_events(asset_ref=asset_ref, limit=500)


async def test_authorized_security_story_end_to_end(
    runtime: SecurityRuntime, target_root: Path, asset_ref: str, locator: str
) -> None:
    # --- 1. enroll the authorized test asset -----------------------------
    asset = _enroll(runtime, asset_ref, locator, target_root)
    assert asset["status"] == "active"
    assert asset["locator"] == locator

    grants = [
        e for e in _events(runtime, asset_ref) if e["action"] in AUTHORIZATION_GRANT_ACTIONS
    ]
    assert len(grants) == 1

    # --- 2. in-scope assessment runs, repeatedly, with no new approval ----
    runs = [runtime.assessments.run(target=locator) for _ in range(3)]
    assert [r["status"] for r in runs] == ["completed"] * 3
    assessment = runs[-1]
    assert assessment["scope_decision"]["allowed"] is True
    assert assessment["scope_decision"]["matched_by"] == "exact_address"

    grants_after = [
        e for e in _events(runtime, asset_ref) if e["action"] in AUTHORIZATION_GRANT_ACTIONS
    ]
    assert grants_after == grants  # scope-based authorization: nothing re-approved

    # --- 3. findings are recorded durably (and idempotently) -------------
    findings = runtime.assessments.list_findings(asset_ref=asset_ref)
    assert len(findings) == assessment["result"]["findings_total"] > 0
    assert {f["severity"] for f in findings} >= {"critical", "high", "medium", "low"}

    fresh = SecurityRuntime(runtime.settings)  # independent engine: real persistence
    with fresh.session() as session:
        stored = (
            session.query(SecurityFinding)
            .join(AuthorizedAsset, SecurityFinding.asset_id == AuthorizedAsset.id)
            .filter(AuthorizedAsset.asset_ref == asset_ref)
            .all()
        )
        assert len(stored) == len(findings)

    # --- 4. results become a security artifact with renders in MinIO -----
    with runtime.session() as session:
        published = publish_assessment_artifact(
            session, runtime.store, assessment_id=uuid.UUID(assessment["id"])
        )
    assert published["kind"] == ARTIFACT_KIND_SECURITY_REPORT
    assert published["state"] == ARTIFACT_STATE_READY
    assert {r["format"] for r in published["renders"]} == {"pdf", "html"}

    for render in published["renders"]:
        data = fresh.store.get(render["object_key"])
        assert content_hash(data) == render["content_hash"]
        assert render["size_bytes"] == len(data)
    pdf = next(r for r in published["renders"] if r["format"] == "pdf")
    assert fresh.store.get(pdf["object_key"])[:4] == b"%PDF"

    with fresh.session() as session:
        artifact = artifact_service.get_artifact(session, uuid.UUID(published["artifact_id"]))
        assert artifact.state == ARTIFACT_STATE_READY
        assert artifact.executive_summary == published["executive_summary"]
        body = artifact_service.get_current_version(session, artifact.id).canonical_body
        assert body.startswith("# Güvenlik Değerlendirme Raporu")
        assert "## Bulgular (Findings)" in body
        assessment_row = session.get(SecurityAssessment, uuid.UUID(assessment["id"]))
        assert str(assessment_row.artifact_id) == published["artifact_id"]

    # --- 5. remediation inside the stored constraints --------------------
    by_check = {f["evidence"]["check_id"]: f for f in findings}

    #   a) medium-disruption fix is REFUSED under max_disruption: low
    ssh = by_check["ssh_root_login_permitted"]
    sshd_before = (target_root / "sshd_config").read_text(encoding="utf-8")
    with pytest.raises(SecurityError) as excinfo:
        runtime.remediation.remediate(uuid.UUID(ssh["id"]), mode="apply")
    assert excinfo.value.error_class is SecurityErrorClass.CONSTRAINT_VIOLATION
    assert (target_root / "sshd_config").read_text(encoding="utf-8") == sshd_before

    #   b) low-disruption fix is applied, reversibly
    debug = by_check["debug_mode_enabled"]
    env_before = (target_root / "app.env").read_text(encoding="utf-8")
    applied = runtime.remediation.remediate(uuid.UUID(debug["id"]), mode="apply")
    assert applied["applied"] is True
    assert "debug=false" in (target_root / "app.env").read_text(encoding="utf-8")
    assert runtime.assessments.get_finding(uuid.UUID(debug["id"]))["status"] == "remediated"

    reverted = runtime.remediation.remediate(uuid.UUID(debug["id"]), mode="revert")
    assert reverted["reverted"] is True
    assert (target_root / "app.env").read_text(encoding="utf-8") == env_before

    # --- 6. the evolution permission model can now verify this asset -----
    provider = RegistryAuthorizationProvider(fresh.session)
    authorization = provider.verify(asset_ref)
    assert authorization is not None
    approved, unauthorized = authorization.covers(
        {"network_permissions": ["lab.example.test"]}
    )
    assert approved is True and unauthorized == []
    denied, missing = authorization.covers({"device_permissions": ["camera"]})
    assert denied is False and missing == ["device_permissions:camera"]

    # --- 7. the audit trail is complete AND secret-free ------------------
    events = _events(runtime, asset_ref)
    actions = [e["action"] for e in events]
    assert actions.count("enrolled") == 1
    assert actions.count("assessment_authorized") == 3
    assert actions.count("remediation_refused") == 1  # the blocked SSH fix
    assert actions.count("remediation_authorized") == 2  # apply + revert

    surfaces = {
        "audit": events,
        "assessment": assessment,
        "findings": findings,
        "asset": runtime.registry.get(asset_ref),
        "artifact_body": body,
        "executive_summary": published["executive_summary"],
    }
    for name, payload in surfaces.items():
        blob = json.dumps(payload, ensure_ascii=False, default=str)
        for secret in PLANTED_SECRETS:
            assert secret not in blob, f"{secret!r} leaked into {name}"
        assert contains_secret(payload) is None, f"secret pattern survived in {name}"

    html = next(r for r in published["renders"] if r["format"] == "html")
    html_bytes = fresh.store.get(html["object_key"])
    for secret in PLANTED_SECRETS:
        assert secret.encode() not in html_bytes


async def test_out_of_scope_attempt_is_refused_and_audited(
    runtime: SecurityRuntime, target_root: Path, asset_ref: str, locator: str
) -> None:
    """The negative half: refused, audited as refused, no asset created."""
    _enroll(runtime, asset_ref, locator, target_root)
    assets_before = {a["asset_ref"] for a in runtime.registry.list(limit=500)}

    # A hostname with the authorized address embedded in it — the spoof that
    # a string-prefix scope check would wave through.
    spoof = f"{locator}.evil.example"
    with pytest.raises(SecurityError) as excinfo:
        runtime.assessments.run(target=spoof)
    assert excinfo.value.error_class is SecurityErrorClass.OUT_OF_SCOPE
    assert excinfo.value.details["decision"]["reason"] == "no_authorized_asset"
    assert excinfo.value.details["decision"]["target_kind"] == "hostname"

    # Nothing was silently added to scope.
    assert {a["asset_ref"] for a in runtime.registry.list(limit=500)} == assets_before
    assert runtime.assessments.list(asset_ref=asset_ref) == []

    # ...and the attempt is visible in the audit as REFUSED.
    refusals = [
        e
        for e in runtime.registry.list_events(action="assessment_refused", limit=500)
        if e["requested_target"] == spoof
    ]
    assert len(refusals) == 1
    assert refusals[0]["allowed"] is False
    assert refusals[0]["asset_id"] is None
    assert refusals[0]["detail"]["enrollment_required"] is True
    assert contains_secret(refusals[0]) is None
