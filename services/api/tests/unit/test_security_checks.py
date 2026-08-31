"""M8 unit tests: the deterministic configuration-audit checks.

These prove the collector is what the milestone claims — offline, read-only,
bounded, reproducible — and that it neither invents findings nor misses the
ones the fixture target deliberately plants.
"""

from __future__ import annotations

import pytest

from app.security.checks import (
    CHECKS,
    CHECKS_BY_ID,
    MAX_FILE_BYTES,
    collect_files,
    fingerprint_for,
    run_checks,
)
from app.security.redaction import contains_secret
from tests.unit.test_security_support import FIXTURE_ROOT, copy_fixture

EXPECTED_CHECK_IDS = {
    "secret_in_config",
    "ssh_root_login_permitted",
    "ssh_password_authentication_enabled",
    "debug_mode_enabled",
    "tls_disabled",
    "permissive_cors_origin",
    "backup_encryption_disabled",
    "weak_password_policy",
    "directory_listing_enabled",
    "obsolete_tls_version_allowed",
}


@pytest.fixture()
def findings():
    return run_checks(collect_files(FIXTURE_ROOT))


def test_check_table_is_complete_and_unique() -> None:
    ids = [c.check_id for c in CHECKS]
    assert set(ids) == EXPECTED_CHECK_IDS
    assert len(ids) == len(set(ids))
    for check in CHECKS:
        assert check.title_tr and check.title_en and check.rationale_tr
        assert check.remediation_tr or check.manual_steps
        # An automatable fix must actually carry a substitution.
        assert check.apply_supported == bool(check.fixes)


def test_every_check_fires_against_the_fixture(findings) -> None:
    assert {f.check_id for f in findings} == EXPECTED_CHECK_IDS


def test_findings_are_deterministic_across_runs() -> None:
    first = run_checks(collect_files(FIXTURE_ROOT))
    second = run_checks(collect_files(FIXTURE_ROOT))
    assert [f.fingerprint for f in first] == [f.fingerprint for f in second]
    assert [f.evidence for f in first] == [f.evidence for f in second]


def test_fingerprints_are_stable_across_absolute_paths(tmp_path, findings) -> None:
    """The same tree assessed from a different directory yields the same
    fingerprints, because they are derived from the RELATIVE path."""
    copied = run_checks(collect_files(copy_fixture(tmp_path)))
    assert [f.fingerprint for f in copied] == [f.fingerprint for f in findings]


def test_fingerprint_survives_an_inserted_comment(tmp_path, findings) -> None:
    """Line numbers must not be part of the fingerprint, or every unrelated
    edit would mint 'new' findings."""
    root = copy_fixture(tmp_path)
    sshd = root / "sshd_config"
    sshd.write_text("# a new comment line\n" + sshd.read_text(encoding="utf-8"), encoding="utf-8")

    before = {f.fingerprint for f in findings if f.check_id == "ssh_root_login_permitted"}
    after = {
        f.fingerprint
        for f in run_checks(collect_files(root))
        if f.check_id == "ssh_root_login_permitted"
    }
    assert before == after


def test_fingerprint_inputs_are_the_documented_triple() -> None:
    assert fingerprint_for("a", "b", "c") == fingerprint_for("a", "b", "c")
    assert fingerprint_for("a", "b", "c") != fingerprint_for("a", "b", "d")
    assert len(fingerprint_for("a", "b", "c")) == 64


def test_clean_file_produces_no_findings(findings) -> None:
    """The runner must not invent problems where there are none."""
    assert [f for f in findings if f.evidence["file"] == "clean.ini"] == []


def test_password_policy_keys_are_not_reported_as_secrets(findings) -> None:
    """`password_rotation_days=90` and `min_password_length: 8` are policy
    knobs, not credentials — a naive keyword match would leak false criticals."""
    secret_lines = {
        (f.evidence["file"], f.evidence["line"])
        for f in findings
        if f.check_id == "secret_in_config"
    }
    assert ("app.env", 15) not in secret_lines  # password_rotation_days
    assert all(file != "service.yaml" for file, _ in secret_lines)


def test_weak_password_policy_predicate_only_fires_below_the_threshold(
    tmp_path,
) -> None:
    root = copy_fixture(tmp_path)
    service = root / "service.yaml"
    service.write_text(
        service.read_text(encoding="utf-8").replace(
            "min_password_length: 8", "min_password_length: 16"
        ),
        encoding="utf-8",
    )
    ids = {f.check_id for f in run_checks(collect_files(root))}
    assert "weak_password_policy" not in ids


def test_severities_match_the_check_table(findings) -> None:
    for finding in findings:
        assert finding.severity == CHECKS_BY_ID[finding.check_id].severity


def test_remediation_proposal_shape(findings) -> None:
    for finding in findings:
        remediation = finding.remediation
        assert remediation["check_id"] == finding.check_id
        assert remediation["disruption"] in ("none", "low", "medium", "high")
        assert isinstance(remediation["apply_supported"], bool)
        assert remediation["reversible"] is True
        if remediation["apply_supported"]:
            assert remediation["proposed_line"]
            assert remediation["proposed_line"] != remediation["current_line"]
        else:
            assert remediation["manual_steps"]


def test_disruptive_fixes_are_marked_medium(findings) -> None:
    """The SSH fixes can lock the owner out; they must not claim to be low."""
    disruption = {
        f.check_id: f.remediation["disruption"]
        for f in findings
        if f.check_id.startswith("ssh_")
    }
    assert disruption == {
        "ssh_root_login_permitted": "medium",
        "ssh_password_authentication_enabled": "medium",
    }


# ------------------------------------------------------- collector bounds


def test_collector_skips_binaries_and_unknown_suffixes(tmp_path) -> None:
    root = copy_fixture(tmp_path)
    (root / "blob.conf").write_bytes(b"PermitRootLogin yes\x00\x00binary")
    (root / "notes.md").write_text("password=leaked-should-not-be-scanned", encoding="utf-8")
    names = {f.relative_path for f in collect_files(root)}
    assert "blob.conf" not in names
    assert "notes.md" not in names


def test_collector_skips_oversized_files(tmp_path) -> None:
    root = copy_fixture(tmp_path)
    (root / "huge.conf").write_text("x" * (MAX_FILE_BYTES + 10), encoding="utf-8")
    assert "huge.conf" not in {f.relative_path for f in collect_files(root)}


def test_collector_reads_nested_directories(tmp_path) -> None:
    root = copy_fixture(tmp_path)
    nested = root / "conf.d"
    nested.mkdir()
    (nested / "extra.conf").write_text("autoindex on;\n", encoding="utf-8")
    findings = run_checks(collect_files(root))
    assert any(f.evidence["file"] == "conf.d/extra.conf" for f in findings)


def test_no_finding_carries_a_secret(findings) -> None:
    assert contains_secret([f.evidence for f in findings]) is None
    assert contains_secret([f.remediation for f in findings]) is None
    assert contains_secret([f.title for f in findings]) is None
