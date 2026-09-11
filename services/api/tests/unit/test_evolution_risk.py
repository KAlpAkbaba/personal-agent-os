"""Unit tests: release risk-tier derivation (M18 spec §5, ADR-0055 §5).

The headline guarantee: the tier is a pure function of the paths a candidate
actually touches. There is no constructor a caller can use to assert a tier
directly, and an empty path list is refused rather than defaulted to "safe".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.risk import RISK_RULES, RiskTier, derive_risk_tier

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_an_empty_path_list_is_refused_not_defaulted_to_safe() -> None:
    for empty in ([], None):
        with pytest.raises(EvolutionError) as excinfo:
            derive_risk_tier(empty)
        assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR


def test_tier_1_ui_and_docs_only() -> None:
    assessment = derive_risk_tier(["apps/web/app/page.tsx", "docs/CHANGELOG_DEV.md"])
    assert assessment.tier == RiskTier.UI_ADDITIVE
    assert not assessment.requires_second_confirmation


def test_tier_2_is_the_default_for_an_unclassified_path() -> None:
    """Under-classifying an unknown path as tier 1 would defeat the point."""
    assessment = derive_risk_tier(["services/api/lab/candidates/thing/src/thing.py"])
    assert assessment.tier == RiskTier.INTERNAL_LOGIC


def test_tier_3_production_backend_logic() -> None:
    assessment = derive_risk_tier(["services/api/app/goals/service.py"])
    assert assessment.tier == RiskTier.PRODUCTION_BEHAVIOR
    assert assessment.requires_second_confirmation


def test_tier_4_migration() -> None:
    assessment = derive_risk_tier(
        ["services/api/alembic/versions/20260906_0019_thing.py"]
    )
    assert assessment.tier == RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS
    assert assessment.requires_second_confirmation
    assert any("migration" in r for r in assessment.reasons)


def test_tier_4_deployment_scripts_and_recovery_supervisor() -> None:
    # Real paths only. This list used to carry "windows-agent/service/main.cs", a path that
    # does not exist, and the test passed while every real file of the privileged Windows
    # service classified as tier 2.
    for path in (
        "scripts/cloud/release-cloud-core.ps1",
        "services/recovery-supervisor/supervisor.py",
        "devices/windows-agent/src/PagentOS.DeviceService/AgentWorker.cs",
        "infra/systemd/pagentos-bluegreen-reconcile.service",
        "infra/docker/docker-compose.prod.yml",
        "infra/docker/edge/nginx.conf",
        "scripts/install-device-service.ps1",
        "scripts/uninstall-device-service.ps1",
        "scripts/lib/AgentUpdate.ps1",
        "scripts/lib/ServiceInstall.ps1",
        "scripts/lib/InstallAcl.ps1",
        "packages/schemas/device-protocol.schema.json",
    ):
        assessment = derive_risk_tier([path])
        assert assessment.tier == RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS, path
        assert (REPO_ROOT / path).is_file(), f"{path} is not a real file: pick one that is"


def test_the_dev_compose_is_not_production_mechanics() -> None:
    # The production rule is specific: the laptop's compose file is ordinary internal logic.
    assert derive_risk_tier(["infra/docker/docker-compose.dev.yml"]).tier == (
        RiskTier.INTERNAL_LOGIC
    )


def test_tier_5_the_security_surface() -> None:
    # The rule said ^app/security/ - nothing lives there - so this module fell to tier 3.
    assessment = derive_risk_tier(["services/api/app/security/remediation.py"])
    assert assessment.tier == RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY
    assert any("security policy" in r for r in assessment.reasons)


def test_tier_5_the_risk_law_itself() -> None:
    # Before 2026-09-11 this file was tier 2: a candidate could reclassify its own paths in
    # one unremarkable step and be promoted under the new table in the next.
    assessment = derive_risk_tier(["services/api/app/evolution/risk.py"])
    assert assessment.tier == RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY
    assert any("risk classification law" in r for r in assessment.reasons)


def _tracked_files() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    files = completed.stdout.splitlines()
    assert len(files) > 1000, "git ls-files returned too little to judge the rules by"
    return files


def test_every_rule_names_a_path_that_exists() -> None:
    # Three rules in this table matched no file in the repository - ^app/security/,
    # ^windows-agent/ and the fake path in the test above - and every test stayed green,
    # because each test asserted a path invented to satisfy its rule. A rule nothing matches
    # is a rule that never fires; this reads the real tree instead.
    files = _tracked_files()
    dead = [rule.pattern.pattern for rule in RISK_RULES if not any(
        rule.pattern.search(path) for path in files
    )]
    assert dead == [], f"risk rules that match no tracked file: {dead}"


def test_tier_5_identity_module() -> None:
    assessment = derive_risk_tier(["services/api/app/identity/service.py"])
    assert assessment.tier == RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY
    assert assessment.requires_second_confirmation


def test_tier_5_the_authority_kernel_itself() -> None:
    assessment = derive_risk_tier(["services/api/app/evolution/authority.py"])
    assert assessment.tier == RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY
    assert any("authority kernel" in r for r in assessment.reasons)


def test_tier_5_anything_naming_a_secret_or_credential() -> None:
    for path in ("scripts/lib/SecretStore.ps1", "config/credential_rotation.json"):
        assessment = derive_risk_tier([path])
        assert assessment.tier == RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY, path


def test_the_overall_tier_is_the_maximum_across_all_touched_paths() -> None:
    """A change that touches both a docs file and the authority kernel is
    tier 5, never averaged down by the docs file."""
    assessment = derive_risk_tier(
        ["docs/CHANGELOG_DEV.md", "services/api/app/evolution/authority.py"]
    )
    assert assessment.tier == RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY


def test_windows_backslash_paths_are_normalised() -> None:
    assessment = derive_risk_tier(["services\\api\\app\\identity\\service.py"])
    assert assessment.tier == RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY


def test_second_confirmation_floor_is_tier_3() -> None:
    assert derive_risk_tier(["docs/x.md"]).requires_second_confirmation is False
    assert derive_risk_tier(["services/api/lab/x/x.py"]).requires_second_confirmation is False
    tier3 = derive_risk_tier(["services/api/app/selfhealing/service.py"])
    assert tier3.requires_second_confirmation


def test_the_assessment_reports_matched_paths_per_tier() -> None:
    assessment = derive_risk_tier(
        ["docs/a.md", "services/api/app/evolution/authority.py", "apps/web/x.tsx"]
    )
    matched = assessment.matched_paths
    assert "docs/a.md" in matched[int(RiskTier.UI_ADDITIVE)]
    assert "apps/web/x.tsx" in matched[int(RiskTier.UI_ADDITIVE)]
    assert "services/api/app/evolution/authority.py" in matched[
        int(RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY)
    ]


def test_to_dict_is_json_shaped_and_carries_turkish_and_english_labels() -> None:
    assessment = derive_risk_tier(["services/api/app/identity/x.py"])
    payload = assessment.to_dict()
    assert payload["tier"] == 5
    assert payload["label_tr"]
    assert payload["label_en"]
    assert payload["requires_second_confirmation"] is True
