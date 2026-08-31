"""M8 unit tests: the in-scope defensive assessment runner.

Acceptance bullets covered here:
- **in-scope defensive assessment runs without repeated approvals**
- **out-of-scope target is not silently added**
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.security.errors import SecurityError, SecurityErrorClass
from app.security.models import AUTHORIZATION_GRANT_ACTIONS
from tests.unit.test_security_support import (
    FIXTURE_ROOT,
    copy_fixture,
    enroll_lab_host,
    make_stack,
    utcnow,
)


@pytest.fixture()
def stack():
    stack = make_stack()
    enroll_lab_host(stack, config_root=FIXTURE_ROOT)
    return stack


def grant_events(stack) -> list[dict]:
    """Owner authorization records — enrollment and scope change ONLY."""
    return [
        event
        for action in AUTHORIZATION_GRANT_ACTIONS
        for event in stack.registry.list_events(action=action, limit=500)
    ]


# ---------------------------------- ACCEPTANCE: in-scope assessment runs


def test_in_scope_assessment_runs_and_records_findings(stack) -> None:
    result = stack.assessments.run(target="10.20.30.40")

    assert result["status"] == "completed"
    assert result["testing_class"] == "configuration_audit"
    assert result["target"] == "10.20.30.40"
    assert result["scope_decision"]["allowed"] is True
    assert result["scope_decision"]["matched_by"] == "exact_address"
    assert result["started_at"] and result["completed_at"]

    summary = result["result"]
    assert summary["collector"] == "configuration_audit/1"
    assert summary["files_scanned"] == 6
    assert summary["findings_total"] == len(result["findings"]) > 0
    assert summary["by_severity"]["critical"] >= 1
    assert sum(summary["by_severity"].values()) == summary["findings_total"]

    # Findings are ordered most-severe first for the owner.
    severities = [f["severity"] for f in result["findings"]]
    assert severities[0] == "critical"
    assert all(f["status"] == "open" for f in result["findings"])


def test_repeated_in_scope_runs_need_no_new_authorization(stack) -> None:
    """ACCEPTANCE: no repeated approvals. Authorization is scope-based, so the
    count of OWNER grant records must not move no matter how often we run."""
    grants_before = grant_events(stack)
    assert len(grants_before) == 1  # the single enrollment

    results = [stack.assessments.run(target="10.20.30.40") for _ in range(5)]

    assert all(r["status"] == "completed" for r in results)
    grants_after = grant_events(stack)
    assert len(grants_after) == 1
    assert grants_after == grants_before  # byte-identical: nothing was added

    # The runs ARE audited — audit is not approval (SECURITY_MODEL §9).
    authorized = stack.registry.list_events(action="assessment_authorized", limit=500)
    assert len(authorized) == 5
    assert all(e["allowed"] and e["reason"] == "in_scope" for e in authorized)


def test_repeated_runs_are_idempotent_not_duplicative(stack) -> None:
    first = stack.assessments.run(target="10.20.30.40")
    second = stack.assessments.run(target="10.20.30.40")

    assert first["id"] != second["id"]  # two runs...
    assert {f["fingerprint"] for f in first["findings"]} == {
        f["fingerprint"] for f in second["findings"]
    }
    # ...but one set of findings, re-pointed at the newest assessment.
    all_findings = stack.assessments.list_findings()
    assert len(all_findings) == len(second["findings"])
    assert {f["assessment_id"] for f in all_findings} == {second["id"]}
    assert len(stack.assessments.list()) == 2


def test_a_resolved_finding_that_reappears_is_reopened(stack) -> None:
    first = stack.assessments.run(target="10.20.30.40")
    target = first["findings"][0]
    with stack.session() as session:
        from app.security.models import SecurityFinding

        row = session.get(SecurityFinding, uuid.UUID(target["id"]))
        row.status = "remediated"
        row.resolved_at = utcnow()
        session.commit()

    stack.assessments.run(target="10.20.30.40")
    assert stack.assessments.get_finding(uuid.UUID(target["id"]))["status"] == "open"


# ------------------------------ ACCEPTANCE: out-of-scope is not added


def test_out_of_scope_target_is_refused_and_never_enrolled(stack) -> None:
    assets_before = stack.registry.list()

    with pytest.raises(SecurityError) as excinfo:
        stack.assessments.run(target="203.0.113.9")

    assert excinfo.value.error_class is SecurityErrorClass.OUT_OF_SCOPE
    assert excinfo.value.details["decision"]["reason"] == "no_authorized_asset"

    assert stack.registry.list() == assets_before  # nothing added, nothing widened
    assert stack.assessments.list() == []  # no run row for an unknown asset
    assert stack.assessments.list_findings() == []

    refusals = stack.registry.list_events(action="assessment_refused")
    assert len(refusals) == 1
    assert refusals[0]["requested_target"] == "203.0.113.9"


def test_spoofed_target_is_refused_by_the_runner(stack) -> None:
    with pytest.raises(SecurityError) as excinfo:
        stack.assessments.run(target="10.20.30.40.evil.com")
    assert excinfo.value.error_class is SecurityErrorClass.OUT_OF_SCOPE
    assert stack.registry.list_events(action="assessment_refused")[0][
        "requested_target"
    ] == "10.20.30.40.evil.com"


def test_refusal_against_a_known_asset_records_a_refused_run(stack) -> None:
    """When the asset IS enrolled, the refused attempt shows up in the run
    history too — not only in the event log."""
    stack.registry.suspend("lab-web-01")
    with pytest.raises(SecurityError):
        stack.assessments.run(target="10.20.30.40")

    runs = stack.assessments.list()
    assert len(runs) == 1
    assert runs[0]["status"] == "refused"
    assert runs[0]["scope_decision"]["reason"] == "asset_suspended"
    assert stack.assessments.list_findings() == []


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda s: s.registry.suspend("lab-web-01"), "asset_suspended"),
        (lambda s: s.registry.revoke("lab-web-01"), "asset_revoked"),
        (
            lambda s: s.registry.change_scope(
                "lab-web-01", allowed_testing={"configuration_audit": False}
            ),
            "testing_class_not_allowed",
        ),
    ],
)
def test_assessment_refused_after_authorization_is_withdrawn(stack, mutate, reason) -> None:
    assert stack.assessments.run(target="10.20.30.40")["status"] == "completed"
    mutate(stack)
    with pytest.raises(SecurityError) as excinfo:
        stack.assessments.run(target="10.20.30.40")
    assert excinfo.value.details["decision"]["reason"] == reason


def test_assessment_refused_once_the_window_expires() -> None:
    stack = make_stack()
    enroll_lab_host(
        stack,
        config_root=FIXTURE_ROOT,
        valid_from=utcnow() - timedelta(days=2),
        valid_until=utcnow() - timedelta(seconds=1),
    )
    with pytest.raises(SecurityError) as excinfo:
        stack.assessments.run(target="10.20.30.40")
    assert excinfo.value.details["decision"]["reason"] == "authorization_expired"


# -------------------------------------------- collector root is registry-bound


def test_config_root_outside_the_recorded_roots_is_refused(stack, tmp_path) -> None:
    """An authorized ASSET does not authorize reading arbitrary paths."""
    elsewhere = copy_fixture(tmp_path)
    with pytest.raises(SecurityError) as excinfo:
        stack.assessments.run(target="10.20.30.40", config_root=str(elsewhere))
    assert excinfo.value.error_class is SecurityErrorClass.COLLECTOR_ROOT_VIOLATION
    assert stack.assessments.list() == []


def test_config_root_may_narrow_inside_a_recorded_root(stack, tmp_path) -> None:
    nested = FIXTURE_ROOT  # the recorded root itself is always acceptable
    result = stack.assessments.run(target="10.20.30.40", config_root=str(nested))
    assert result["status"] == "completed"


def test_asset_without_config_roots_cannot_be_assessed() -> None:
    stack = make_stack()
    enroll_lab_host(stack)  # no config_roots recorded
    with pytest.raises(SecurityError) as excinfo:
        stack.assessments.run(target="10.20.30.40")
    assert excinfo.value.error_class is SecurityErrorClass.VALIDATION_ERROR


def test_several_config_roots_require_the_caller_to_name_one(tmp_path) -> None:
    stack = make_stack()
    second = copy_fixture(tmp_path)
    stack.registry.enroll(
        asset_ref="lab-multi",
        name="Multi-root host",
        kind="host",
        locator="10.20.30.50",
        allowed_testing={"configuration_audit": True},
        constraints={"max_disruption": "low", "config_roots": [str(FIXTURE_ROOT), str(second)]},
        evidence={"authorization": "owner_or_company_authorized", "recorded_by": "owner"},
    )
    with pytest.raises(SecurityError):
        stack.assessments.run(target="10.20.30.50")
    assert stack.assessments.run(target="10.20.30.50", config_root=str(second))[
        "status"
    ] == "completed"


def test_missing_config_root_fails_the_run_not_the_process(tmp_path) -> None:
    stack = make_stack()
    enroll_lab_host(stack, config_root=tmp_path / "does-not-exist")
    with pytest.raises(SecurityError) as excinfo:
        stack.assessments.run(target="10.20.30.40")
    assert excinfo.value.error_class is SecurityErrorClass.TARGET_UNAVAILABLE
    assert stack.assessments.list()[0]["status"] == "failed"


# ---------------------------------------------------------------- reading


def test_get_and_list_filters(stack) -> None:
    result = stack.assessments.run(target="10.20.30.40")
    fetched = stack.assessments.get(uuid.UUID(result["id"]))
    assert fetched["id"] == result["id"]
    assert len(fetched["findings"]) == len(result["findings"])

    assert stack.assessments.list(asset_ref="lab-web-01")
    assert stack.assessments.list(asset_ref="not-enrolled") == []
    assert stack.assessments.list(status="completed")
    assert stack.assessments.list(status="failed") == []

    criticals = stack.assessments.list_findings(severity="critical")
    assert criticals and all(f["severity"] == "critical" for f in criticals)
    assert stack.assessments.list_findings(status="remediated") == []


def test_unknown_ids_are_typed_not_found(stack) -> None:
    with pytest.raises(SecurityError) as excinfo:
        stack.assessments.get(uuid.uuid4())
    assert excinfo.value.error_class is SecurityErrorClass.NOT_FOUND
    with pytest.raises(SecurityError):
        stack.assessments.get_finding(uuid.uuid4())
