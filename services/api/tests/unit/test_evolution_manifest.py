"""Unit tests: the full M7 capability manifest schema and deny-by-default
permissions (ACCEPTANCE_TESTS M7 "Capability manifest", SECURITY_MODEL M7)."""

import pytest

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.manifest import (
    PERMISSION_FIELDS,
    REQUIRED_FIELDS,
    default_manifest,
    granted_permissions,
    implied_risk_class,
    implied_side_effects,
    require_permission_approval,
    validate_dependencies,
    validate_manifest,
)

# Every field the owner enumerated, mapped to the manifest key that carries it.
OWNER_REQUIRED_FIELDS = {
    "capability ID": "id",
    "human-readable purpose": "purpose",
    "version": "version",
    "input schema": "input_schema",
    "output schema": "output_schema",
    "dependencies": "dependencies",
    "network permissions": "network_permissions",
    "filesystem permissions": "filesystem_permissions",
    "device permissions": "device_permissions",
    "secret requirements": "secret_requirements",
    "external services/providers": "external_services",
    "expected side effects": "side_effects",
    "risk classification": "risk_class",
    "tests": "tests",
    "evaluation metrics": "evaluation_metrics",
    "provenance": "provenance",
    "builder identity": "builder_identity",
    "creation reason/task": "creation_reason",
    "rollback version": "rollback_version",
}


def base(**overrides):
    return default_manifest(
        "text.slugify",
        "0.1.0",
        purpose="turn a title into a url slug",
        inputs=["text"],
        outputs=["slug"],
        health_metrics=["success_rate"],
        **overrides,
    )


PINNED_DEP = {
    "name": "text_metrics_kit",
    "version": "2.1.0",
    "source": "local-component-catalog",
    "digest": "sha256:" + "ab" * 32,
}


# ----------------------------------------------------------------- schema


@pytest.mark.parametrize("label,key", sorted(OWNER_REQUIRED_FIELDS.items()))
def test_every_owner_required_field_is_required(label, key) -> None:
    assert key in REQUIRED_FIELDS, label
    body = base()
    body.pop(key)
    with pytest.raises(EvolutionError) as excinfo:
        validate_manifest(body)
    assert key in excinfo.value.details["missing"]


def test_a_complete_manifest_validates_and_normalizes() -> None:
    normalized = validate_manifest(base(skill="text_slugify", entrypoint="run"))
    assert normalized["id"] == "text.slugify"
    assert normalized["purpose"]
    assert normalized["input_schema"] == [
        {"name": "text", "type": "string", "required": True}
    ]
    assert normalized["risk_class"] == "low"
    assert normalized["side_effects"] == ["pure"]
    assert normalized["rollback_version"] is None
    assert normalized["builder_identity"]["kind"] == "generated"


def test_io_schema_must_match_the_flat_io_lists() -> None:
    body = base()
    body["input_schema"] = [{"name": "other", "type": "string", "required": True}]
    with pytest.raises(EvolutionError):
        validate_manifest(body)
    body = base()
    body["output_schema"] = []
    with pytest.raises(EvolutionError):
        validate_manifest(body)


def test_dependency_records_must_be_pinned_shapes() -> None:
    assert validate_dependencies([PINNED_DEP])[0]["digest"] == PINNED_DEP["digest"]
    for bad in (
        [{**PINNED_DEP, "version": "^2.1.0"}],
        [{k: v for k, v in PINNED_DEP.items() if k != "digest"}],
        [{**PINNED_DEP, "surprise": 1}],
        [{**PINNED_DEP, "name": "../evil"}],
        "not-a-list",
    ):
        with pytest.raises(EvolutionError):
            validate_dependencies(bad)


def test_manifest_carries_pinned_dependencies_and_components() -> None:
    normalized = validate_manifest(base(dependencies=[PINNED_DEP], components=[PINNED_DEP]))
    assert normalized["dependencies"] == [PINNED_DEP]
    assert normalized["components"] == [PINNED_DEP]


# ------------------------------------------------------- deny-by-default


def test_generated_permissions_default_to_empty() -> None:
    manifest = base()
    for field in PERMISSION_FIELDS:
        assert manifest[field] == []
    assert granted_permissions(manifest) == {}
    # ...and a manifest with no grants needs no approval.
    require_permission_approval(validate_manifest(manifest), {})


def test_declaring_a_grant_raises_the_risk_class_and_side_effects() -> None:
    manifest = validate_manifest(base(device_permissions=["serial_port"]))
    assert manifest["risk_class"] == "high"
    assert "device_control" in manifest["side_effects"]
    assert granted_permissions(manifest) == {"device_permissions": ["serial_port"]}

    network = validate_manifest(base(network_permissions=["api.example.com"]))
    assert network["risk_class"] == "moderate"
    assert "network_egress" in network["side_effects"]


def test_a_manifest_cannot_understate_its_blast_radius() -> None:
    body = base(device_permissions=["serial_port"])
    body["risk_class"] = "low"
    with pytest.raises(EvolutionError) as excinfo:
        validate_manifest(body)
    assert "understates" in excinfo.value.message

    body = base(network_permissions=["api.example.com"])
    body["side_effects"] = ["pure"]
    with pytest.raises(EvolutionError) as excinfo:
        validate_manifest(body)
    assert "omits effects" in excinfo.value.message


def test_permission_shapes_are_strict() -> None:
    for bad in (
        {"network_permissions": ["not a host!"]},
        {"network_permissions": ["http://api.example.com/path"]},
        {"filesystem_permissions": [{"path": "relative/path", "mode": "read"}]},
        {"filesystem_permissions": [{"path": "C:/data/../secrets", "mode": "read"}]},
        {"filesystem_permissions": [{"path": "C:/data", "mode": "execute"}]},
        {"device_permissions": ["../dev"]},
        {"secret_requirements": ["My Secret"]},
    ):
        with pytest.raises(EvolutionError):
            validate_manifest(base(**bad))


def test_generated_grants_need_an_exact_reviewer_approval() -> None:
    manifest = validate_manifest(base(device_permissions=["serial_port"]))
    grants = granted_permissions(manifest)

    with pytest.raises(EvolutionError) as excinfo:
        require_permission_approval(manifest, {})
    assert excinfo.value.error_class == EvolutionErrorClass.REGISTRATION_REFUSED

    with pytest.raises(EvolutionError):
        require_permission_approval(manifest, {"permissions_approved": True})
    with pytest.raises(EvolutionError):
        require_permission_approval(
            manifest,
            {"permissions_approved": True, "approved_permissions": {"network_permissions": []}},
        )
    require_permission_approval(
        manifest, {"permissions_approved": True, "approved_permissions": grants}
    )


def test_curated_manifests_are_not_subject_to_the_generated_gate() -> None:
    manifest = validate_manifest(
        base(device_permissions=["serial_port"], builder_identity={"kind": "curated"})
    )
    require_permission_approval(manifest, {})  # no exception


def test_risk_and_side_effect_helpers() -> None:
    assert implied_risk_class({}) == "low"
    assert implied_risk_class({"secret_requirements": ["x"]}) == "high"
    assert implied_risk_class({"network_permissions": ["a.example.com"]}) == "moderate"
    assert implied_side_effects({}) == {"pure"}
    assert "writes_filesystem" in implied_side_effects(
        {"filesystem_permissions": [{"path": "/tmp/x", "mode": "write"}]}
    )


def test_builder_identity_and_creation_reason_are_constrained() -> None:
    with pytest.raises(EvolutionError):
        validate_manifest(base(builder_identity={"kind": "mystery"}))
    with pytest.raises(EvolutionError):
        validate_manifest(base(builder_identity={"name": "x"}))
    with pytest.raises(EvolutionError):
        validate_manifest(base(creation_reason={"trigger": "because"}))
    with pytest.raises(EvolutionError):
        validate_manifest(base(creation_reason={"unknown_key": 1}))
    ok = validate_manifest(
        base(
            creation_reason={
                "trigger": "capability_gap",
                "gap_id": "abc",
                "depth": 0,
                "authorized_asset": "owner_laptop",
                "resolution_path": "component_adaptation",
            }
        )
    )
    assert ok["creation_reason"]["authorized_asset"] == "owner_laptop"
