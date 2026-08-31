"""Boundary clarification (ACCEPTANCE_TESTS M7 "Boundaries", SECURITY_MODEL M7).

    "the recovery/security root restriction applies to **self-modification
     authority**, not to owner-authorized operational capabilities: the owner
     policy subsystem must remain able to grant powerful tools to explicitly
     authorized devices/assets without Evolution weakening or rewriting the
     security root."

Both directions are proven here:

- an OWNER-AUTHORIZED OPERATIONAL capability — one whose manifest declares
  device/network permissions for an asset the owner policy subsystem authorized
  — is NOT refused by the core/recovery guard, gets its grants reviewer-approved
  and reaches production;
- a SELF-MODIFICATION attempt aimed at the recovery/security roots is still
  refused with ``product_change_required``, and the sandbox still cannot be
  pointed at those trees.
"""

import uuid

import pytest

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.gaps import (
    PROTECTED_COMPONENTS,
    STEP_PRODUCT_CHANGE,
    CapabilityRequest,
    implies_product_change,
)
from app.evolution.manifest import granted_permissions
from app.evolution.resources import ResourceBudget
from app.evolution.sandbox import REPO_ROOT, SandboxPolicy
from tests.unit.test_evolution_pipeline import Stack, make_stack


@pytest.fixture()
def stack(tmp_path) -> Stack:
    return make_stack(tmp_path)

# A real operational grant: an owner-enrolled device this capability drives.
AUTHORIZED_ASSET = "owner_workstation"

OPERATIONAL_GAP = {
    "requested_capability": "device.slug_label",
    "request_text": "Yetkili cihaz icin etiket slug'i uret ve cihaza yaz.",
    "required_inputs": ["text"],
    "required_outputs": ["slug"],
    "intent": "operational_capability",
    "authorized_asset": AUTHORIZED_ASSET,
    "spec": {
        "capability_id": "device.slug_label",
        "operation": "slugify",
        "version": "0.1.0",
        "summary": "label slug for an owner-authorized device",
        "device_permissions": ["serial_port"],
        "network_permissions": ["device.local"],
        "authorized_asset": AUTHORIZED_ASSET,
    },
}

SELF_MODIFICATION_GAP = {
    "requested_capability": "recovery.rewrite_supervisor",
    "request_text": "Kurtarma supervisor'ini kendi kendine guncelle.",
    "required_inputs": ["text"],
    "required_outputs": ["slug"],
    "intent": "self_modification",
    "target_component": "recovery-supervisor",
    "spec": None,
}


def request_for(**overrides) -> CapabilityRequest:
    body = {
        "requested_capability": "device.slug_label",
        "request_text": "Yetkili cihaz icin etiket uret.",
        "required_inputs": ["text"],
        "required_outputs": ["slug"],
    }
    body.update(overrides)
    return CapabilityRequest.parse(body)


# ------------------------------------ operational grants are NOT refused


def test_owner_authorized_operational_grant_is_not_refused(stack: Stack) -> None:
    gap_id = stack.open_gap(**OPERATIONAL_GAP)
    gap = stack.gaps.get(gap_id)
    assert gap["resolution"] == "generation"
    assert gap["status"] == "open"
    trail = {entry["step"]: entry for entry in gap["decision_trail"]}
    assert trail[STEP_PRODUCT_CHANGE]["outcome"] == "not_applicable"


def test_declaring_device_permissions_never_triggers_the_core_guard() -> None:
    """Permissions are an OPERATIONAL concern; they are gated deny-by-default,
    not treated as evidence of self-modification."""
    refused, evidence = implies_product_change(
        request_for(authorized_asset=AUTHORIZED_ASSET)
    )
    assert refused is False
    assert evidence["owner_authorized_operational"] is True

    # ...and even without an asset reference, plain permissions are irrelevant
    # to this rule: the request below declares none of the protected signals.
    refused, evidence = implies_product_change(request_for())
    assert refused is False
    assert evidence["reasons"] == []


def test_an_owner_authorized_request_is_not_second_guessed_by_the_text_scan() -> None:
    """A request the owner policy subsystem authorized is not refused merely for
    MENTIONING a protected component in prose — but its structured signals still
    count, so it cannot name one as its target."""
    mentioning = request_for(
        request_text="Bu cihaz backup-restore isini de tetikleyecek.",
        authorized_asset=AUTHORIZED_ASSET,
    )
    assert implies_product_change(mentioning)[0] is False

    targeting = request_for(
        authorized_asset=AUTHORIZED_ASSET, target_component="recovery-supervisor"
    )
    refused, evidence = implies_product_change(targeting)
    assert refused is True
    assert "target_component=recovery-supervisor" in evidence["reasons"]


def test_an_unauthorized_request_is_still_text_scanned() -> None:
    refused, evidence = implies_product_change(
        request_for(request_text="patch the recovery-supervisor rollback logic")
    )
    assert refused is True
    assert any("request_text_mentions" in reason for reason in evidence["reasons"])


def test_owner_authorized_grants_reach_production_through_the_normal_gates(
    stack: Stack,
) -> None:
    result = stack.pipeline().run(stack.open_gap(**OPERATIONAL_GAP))
    assert result.status == "registered", result.summary

    resolved = stack.registry.resolve("device.slug_label")
    assert resolved is not None
    manifest = resolved["manifest"]
    assert manifest["device_permissions"] == ["serial_port"]
    assert manifest["network_permissions"] == ["device.local"]
    assert manifest["risk_class"] == "high"  # the manifest states the blast radius
    assert "device_control" in manifest["side_effects"]
    assert manifest["creation_reason"]["authorized_asset"] == AUTHORIZED_ASSET

    review = stack.registry.get_skill_version(uuid.UUID(result.skill_version_id))["review"]
    assert review["permissions_approved"] is True
    assert review["approved_permissions"] == granted_permissions(manifest)


def test_a_grant_without_owner_authorization_is_denied(stack: Stack) -> None:
    """Deny-by-default: the same capability WITHOUT the owner policy reference
    cannot award itself device access."""
    spec = {k: v for k, v in OPERATIONAL_GAP["spec"].items() if k != "authorized_asset"}
    result = stack.pipeline().run(
        stack.open_gap(
            **{
                **OPERATIONAL_GAP,
                "authorized_asset": None,
                "spec": spec,
            }
        )
    )
    assert result.status == "rejected"
    assert "independent review" in result.summary
    assert stack.registry.resolve("device.slug_label") is None
    review = stack.registry.get_skill_version(uuid.UUID(result.skill_version_id))["review"]
    assert review["permissions_approved"] is False
    failed = {check["name"] for check in review["checks"] if not check["passed"]}
    assert "permission_grants_reviewed" in failed


# --------------------------------- self-modification is STILL refused


def test_self_modification_of_the_recovery_root_is_still_refused(stack: Stack) -> None:
    gap_id = stack.open_gap(**SELF_MODIFICATION_GAP)
    gap = stack.gaps.get(gap_id)
    assert gap["resolution"] == "product_change_required"
    assert gap["status"] == "abandoned"

    result = stack.pipeline().run(gap_id)
    assert result.status == "refused"
    assert result.stages[-1].detail["error_class"] == "product_change_required"
    assert stack.registry.list_skill_versions() == []


def test_self_modification_intent_alone_is_enough_to_refuse() -> None:
    refused, evidence = implies_product_change(request_for(intent="self_modification"))
    assert refused is True
    assert "intent=self_modification" in evidence["reasons"]


@pytest.mark.parametrize("component", sorted(PROTECTED_COMPONENTS))
def test_every_protected_component_is_still_off_limits_as_a_target(component) -> None:
    refused, _ = implies_product_change(
        request_for(target_component=component, authorized_asset=AUTHORIZED_ASSET)
    )
    assert refused is True


def test_the_sandbox_still_cannot_be_aimed_at_the_recovery_root() -> None:
    """An operational grant never widens where generated code may be written."""
    for target in (
        REPO_ROOT / "services" / "recovery-supervisor",
        REPO_ROOT / "services" / "api" / "app",
    ):
        with pytest.raises(EvolutionError) as excinfo:
            SandboxPolicy(target)
        assert excinfo.value.error_class == EvolutionErrorClass.SANDBOX_VIOLATION


def test_an_intent_outside_the_vocabulary_is_refused() -> None:
    with pytest.raises(EvolutionError):
        request_for(intent="whatever_i_want")


# ------------------------------------------ recursion boundary (item 4)


def test_a_nested_evolution_stops_at_the_configured_depth(stack: Stack) -> None:
    """An evolution triggered BY an evolution carries depth+1; the chain is cut
    at the configured limit, so agent -> agent -> agent cannot run away."""
    budget = ResourceBudget(max_depth=1)

    root = stack.open_gap()  # depth 0
    assert stack.gaps.get(root)["resolution"] == "generation"

    nested = stack.open_gap(
        requested_capability="text.reverse_text",
        request_text="Evrimin tetikledigi ikinci yetenek.",
        required_inputs=["text"],
        required_outputs=["reversed_text"],
        depth=1,
        origin_gap_id=str(root),
        spec={
            "capability_id": "text.reverse_text",
            "operation": "reverse_text",
            "version": "0.1.0",
            "summary": "reverse a text",
        },
    )
    assert stack.gaps.get(nested)["resolution"] == "generation"

    # depth 2 never even becomes a gap: detection refuses first.
    with pytest.raises(EvolutionError) as excinfo:
        stack.open_gap(
            requested_capability="text.char_checksum",
            request_text="Ucuncu seviye evrim.",
            required_inputs=["text"],
            required_outputs=["checksum"],
            depth=2,
            origin_gap_id=str(nested),
            spec=None,
        )
    assert excinfo.value.error_class == EvolutionErrorClass.RECURSION_LIMIT_EXCEEDED
    assert len(stack.gaps.list()) == 2

    # And the pipeline refuses a too-deep request even if a gap row existed.
    deep_request = request_for(depth=budget.max_depth + 1)
    with pytest.raises(EvolutionError):
        stack.detector.detect(deep_request)
