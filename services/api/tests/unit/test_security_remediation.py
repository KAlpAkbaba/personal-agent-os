"""M8 unit tests: remediation bounded by stored asset constraints.

Acceptance bullet covered here: **remediation respects stored asset
constraints.**
"""

from __future__ import annotations

import uuid

import pytest

from app.security.errors import SecurityError, SecurityErrorClass
from app.security.remediation import (
    MODE_APPLY,
    MODE_DRY_RUN,
    MODE_PROPOSE,
    MODE_REVERT,
)
from tests.unit.test_security_support import copy_fixture, enroll_lab_host, make_stack


@pytest.fixture()
def scenario(tmp_path):
    """A writable copy of the fixture target, enrolled and assessed."""

    def _build(*, max_disruption: str = "low", allowed_testing=None):
        stack = make_stack()
        root = copy_fixture(tmp_path / max_disruption)
        enroll_lab_host(
            stack,
            config_root=root,
            max_disruption=max_disruption,
            allowed_testing=allowed_testing,
        )
        result = stack.assessments.run(target="10.20.30.40")
        by_check = {f["evidence"]["check_id"]: f for f in result["findings"]}
        return stack, root, by_check

    return _build


def _id(finding) -> uuid.UUID:
    return uuid.UUID(finding["id"])


# ------------------------------------ ACCEPTANCE: constraints are respected


def test_medium_disruption_fix_is_refused_under_max_disruption_low(scenario) -> None:
    """`max_disruption: low` forbids the medium-disruption SSH fix — and the
    file is left untouched."""
    stack, root, by_check = scenario(max_disruption="low")
    finding = by_check["ssh_root_login_permitted"]
    assert finding["remediation"]["disruption"] == "medium"
    before = (root / "sshd_config").read_text(encoding="utf-8")

    with pytest.raises(SecurityError) as excinfo:
        stack.remediation.remediate(_id(finding), mode=MODE_APPLY)

    assert excinfo.value.error_class is SecurityErrorClass.CONSTRAINT_VIOLATION
    assert excinfo.value.details["decision"]["reason"] == "disruption_exceeds_constraint"
    assert excinfo.value.details["decision"]["detail"]["max_disruption"] == "low"
    assert (root / "sshd_config").read_text(encoding="utf-8") == before
    assert stack.assessments.get_finding(_id(finding))["status"] == "open"


def test_the_same_fix_is_allowed_once_the_owner_raises_the_constraint(
    scenario,
) -> None:
    stack, root, by_check = scenario(max_disruption="medium")
    finding = by_check["ssh_root_login_permitted"]
    result = stack.remediation.remediate(_id(finding), mode=MODE_APPLY)
    assert result["applied"] is True
    assert "PermitRootLogin no" in (root / "sshd_config").read_text(encoding="utf-8")


def test_constraint_refusal_is_written_to_the_audit_trail(scenario) -> None:
    stack, _root, by_check = scenario(max_disruption="low")
    with pytest.raises(SecurityError):
        stack.remediation.remediate(
            _id(by_check["ssh_root_login_permitted"]), mode=MODE_APPLY
        )
    refusals = stack.registry.list_events(action="remediation_refused")
    assert len(refusals) == 1
    assert refusals[0]["allowed"] is False
    assert refusals[0]["reason"] == "disruption_exceeds_constraint"


def test_remediation_class_not_allowed_refuses_even_a_trivial_fix(scenario) -> None:
    """Disruption is not the only gate: the asset must allow the class."""
    stack, root, by_check = scenario(
        max_disruption="high", allowed_testing={"configuration_audit": True}
    )
    finding = by_check["debug_mode_enabled"]
    with pytest.raises(SecurityError) as excinfo:
        stack.remediation.remediate(_id(finding), mode=MODE_APPLY)
    assert excinfo.value.error_class is SecurityErrorClass.OUT_OF_SCOPE
    assert excinfo.value.details["decision"]["reason"] == "testing_class_not_allowed"
    assert "debug=true" in (root / "app.env").read_text(encoding="utf-8")


# -------------------------------------------------- propose is the default


def test_propose_is_the_default_and_mutates_nothing(scenario) -> None:
    stack, root, by_check = scenario(max_disruption="low")
    finding = by_check["debug_mode_enabled"]
    before = (root / "app.env").read_text(encoding="utf-8")

    proposal = stack.remediation.remediate(_id(finding))

    assert proposal["mode"] == MODE_PROPOSE
    assert proposal["changed"] is False
    assert proposal["appliable"] is True
    assert proposal["remediation"]["proposed_line"] == "debug=false"
    assert (root / "app.env").read_text(encoding="utf-8") == before
    # A question nobody acted on writes no audit row.
    assert stack.registry.list_events(action="remediation_authorized") == []
    assert stack.registry.list_events(action="remediation_refused") == []


def test_propose_reports_a_blocked_fix_as_not_appliable(scenario) -> None:
    stack, _root, by_check = scenario(max_disruption="low")
    proposal = stack.remediation.remediate(_id(by_check["ssh_root_login_permitted"]))
    assert proposal["appliable"] is False
    assert proposal["constraint_check"]["reason"] == "disruption_exceeds_constraint"


def test_proposal_only_finding_is_refused_for_apply(scenario) -> None:
    """Rotating a leaked credential has no safe automated form; the agent
    refuses rather than guessing."""
    stack, _root, by_check = scenario(max_disruption="high")
    finding = by_check["secret_in_config"]
    assert finding["remediation"]["apply_supported"] is False
    assert finding["remediation"]["manual_steps"]

    with pytest.raises(SecurityError) as excinfo:
        stack.remediation.remediate(_id(finding), mode=MODE_APPLY)
    assert excinfo.value.error_class is SecurityErrorClass.REMEDIATION_NOT_AUTOMATABLE
    assert excinfo.value.details["manual_steps"]


# ------------------------------------------------ dry run / apply / revert


def test_dry_run_computes_the_patch_without_writing(scenario) -> None:
    stack, root, by_check = scenario(max_disruption="low")
    before = (root / "app.env").read_text(encoding="utf-8")

    result = stack.remediation.remediate(
        _id(by_check["debug_mode_enabled"]), mode=MODE_DRY_RUN
    )

    assert result["applied"] is False
    assert result["changed"] is True
    assert result["current_line"] == "debug=true"
    assert result["proposed_line"] == "debug=false"
    assert (root / "app.env").read_text(encoding="utf-8") == before
    assert result["finding"]["status"] == "open"


def test_apply_writes_a_backup_and_is_reversible(scenario) -> None:
    stack, root, by_check = scenario(max_disruption="low")
    finding = by_check["debug_mode_enabled"]
    original = (root / "app.env").read_text(encoding="utf-8")

    applied = stack.remediation.remediate(_id(finding), mode=MODE_APPLY)
    assert applied["applied"] is True
    assert applied["reversible"] is True
    patched = (root / "app.env").read_text(encoding="utf-8")
    assert "debug=false" in patched
    assert "debug=true" not in patched
    assert (root / applied["backup_file"]).read_text(encoding="utf-8") == original
    assert stack.assessments.get_finding(_id(finding))["status"] == "remediated"
    assert stack.registry.list_events(action="remediation_authorized")

    reverted = stack.remediation.remediate(_id(finding), mode=MODE_REVERT)
    assert reverted["reverted"] is True
    assert (root / "app.env").read_text(encoding="utf-8") == original
    assert not (root / applied["backup_file"]).exists()
    assert stack.assessments.get_finding(_id(finding))["status"] == "open"


def test_apply_preserves_the_rest_of_the_file_verbatim(scenario) -> None:
    stack, root, by_check = scenario(max_disruption="low")
    before = (root / "app.env").read_text(encoding="utf-8").splitlines()
    stack.remediation.remediate(_id(by_check["debug_mode_enabled"]), mode=MODE_APPLY)
    after = (root / "app.env").read_text(encoding="utf-8").splitlines()
    assert len(before) == len(after)
    assert [b for b, a in zip(before, after, strict=True) if b != a] == ["debug=true"]


def test_revert_without_a_backup_is_typed_not_found(scenario) -> None:
    stack, _root, by_check = scenario(max_disruption="low")
    with pytest.raises(SecurityError) as excinfo:
        stack.remediation.remediate(
            _id(by_check["debug_mode_enabled"]), mode=MODE_REVERT
        )
    assert excinfo.value.error_class is SecurityErrorClass.NOT_FOUND


# --------------------------------------------- the patch is re-derived


def test_patch_is_recomputed_from_disk_not_replayed(scenario) -> None:
    """If the owner already fixed it, applying reports 'already resolved'
    instead of clobbering their edit."""
    stack, root, by_check = scenario(max_disruption="low")
    finding = by_check["debug_mode_enabled"]
    (root / "app.env").write_text(
        (root / "app.env").read_text(encoding="utf-8").replace("debug=true", "debug=false"),
        encoding="utf-8",
    )

    result = stack.remediation.remediate(_id(finding), mode=MODE_APPLY)
    assert result["status"] == "already_resolved"
    assert result["changed"] is False
    assert stack.assessments.get_finding(_id(finding))["status"] == "remediated"


def test_patch_follows_a_line_that_moved(scenario) -> None:
    stack, root, by_check = scenario(max_disruption="low")
    finding = by_check["debug_mode_enabled"]
    env = root / "app.env"
    env.write_text("# inserted\n# lines\n" + env.read_text(encoding="utf-8"), encoding="utf-8")

    result = stack.remediation.remediate(_id(finding), mode=MODE_APPLY)
    assert result["applied"] is True
    assert "debug=false" in env.read_text(encoding="utf-8")


# --------------------------------------------------- path containment


def test_remediation_path_cannot_escape_the_authorized_root(scenario) -> None:
    stack, _root, by_check = scenario(max_disruption="low")
    finding = by_check["debug_mode_enabled"]
    with stack.session() as session:
        from app.security.models import SecurityFinding

        row = session.get(SecurityFinding, _id(finding))
        row.remediation_json = {**row.remediation_json, "file": "../../../etc/passwd"}
        session.commit()

    with pytest.raises(SecurityError) as excinfo:
        stack.remediation.remediate(_id(finding), mode=MODE_APPLY)
    assert excinfo.value.error_class is SecurityErrorClass.COLLECTOR_ROOT_VIOLATION


def test_absolute_remediation_path_is_refused(scenario, tmp_path) -> None:
    stack, _root, by_check = scenario(max_disruption="low")
    finding = by_check["debug_mode_enabled"]
    with stack.session() as session:
        from app.security.models import SecurityFinding

        row = session.get(SecurityFinding, _id(finding))
        row.remediation_json = {**row.remediation_json, "file": str(tmp_path / "elsewhere.conf")}
        session.commit()

    with pytest.raises(SecurityError) as excinfo:
        stack.remediation.remediate(_id(finding), mode=MODE_APPLY)
    assert excinfo.value.error_class is SecurityErrorClass.COLLECTOR_ROOT_VIOLATION


# ------------------------------------------------------------- validation


def test_unknown_mode_is_refused(scenario) -> None:
    stack, _root, by_check = scenario(max_disruption="low")
    with pytest.raises(SecurityError) as excinfo:
        stack.remediation.remediate(_id(by_check["debug_mode_enabled"]), mode="yolo")
    assert excinfo.value.error_class is SecurityErrorClass.VALIDATION_ERROR


def test_unknown_finding_is_typed_not_found(scenario) -> None:
    stack, _root, _by_check = scenario(max_disruption="low")
    with pytest.raises(SecurityError) as excinfo:
        stack.remediation.remediate(uuid.uuid4(), mode=MODE_PROPOSE)
    assert excinfo.value.error_class is SecurityErrorClass.NOT_FOUND
