"""Unit tests: release risk-tier derivation (M18 spec §5, ADR-0055 §5).

The headline guarantee: the tier is a pure function of the paths a candidate
actually touches. There is no constructor a caller can use to assert a tier
directly, and an empty path list is refused rather than defaulted to "safe".
"""

from __future__ import annotations

import pytest

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.risk import RiskTier, derive_risk_tier


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
    for path in (
        "scripts/cloud/release-cloud-core.ps1",
        "services/recovery-supervisor/supervisor.py",
        "windows-agent/service/main.cs",
    ):
        assessment = derive_risk_tier([path])
        assert assessment.tier == RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS, path


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
