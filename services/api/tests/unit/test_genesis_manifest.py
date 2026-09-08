"""M24 additive manifest keys (M24_CAPABILITY_GENESIS_SPEC.md §4, ADR-0087):
``authority_class``, ``side_effect_class``, ``evidence_contract``,
``rollback_semantics`` (``input_schema``/``output_schema`` are the EXISTING
required typed-io fields, populated by ``HttpAdapterGenerator`` from its own
bounded subset — see ``app.evolution.manifest``'s own module docstring for
why only four keys are genuinely new columns). Every existing manifest and
test must be unchanged; the dispatcher refuses a mutate_external +
mutating_unauthorized capability.
"""

from __future__ import annotations

import uuid

import pytest

from app.evolution.errors import EvolutionError
from app.evolution.manifest import default_manifest, validate_manifest
from app.evolution.task_resumption import CapabilityDispatcher
from tests.fixtures.genesis import counterbox_app
from tests.unit.genesis_stack import make_stack


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


def test_existing_manifest_without_any_m24_key_still_validates():
    """The M7 acceptance manifest (no M24 keys at all) is unchanged."""
    manifest = base()
    for key in ("authority_class", "side_effect_class", "evidence_contract", "rollback_semantics"):
        assert key not in manifest
    normalized = validate_manifest(manifest)
    for key in ("authority_class", "side_effect_class", "evidence_contract", "rollback_semantics"):
        assert key not in normalized


@pytest.mark.parametrize(
    "value", ["read_only", "mutating_authorized_asset", "mutating_unauthorized"]
)
def test_authority_class_accepts_the_three_declared_values(value):
    normalized = validate_manifest(base(authority_class=value))
    assert normalized["authority_class"] == value


def test_authority_class_rejects_anything_else():
    with pytest.raises(EvolutionError):
        validate_manifest(base(authority_class="omnipotent"))


@pytest.mark.parametrize("value", ["none", "read", "mutate_external"])
def test_side_effect_class_accepts_the_three_declared_values(value):
    normalized = validate_manifest(base(side_effect_class=value))
    assert normalized["side_effect_class"] == value


def test_side_effect_class_rejects_anything_else():
    with pytest.raises(EvolutionError):
        validate_manifest(base(side_effect_class="everything"))


def test_evidence_contract_shape():
    normalized = validate_manifest(
        base(
            evidence_contract={
                "read_back": "read",
                "postcondition": "the counter read back equals the reported value",
            }
        )
    )
    assert normalized["evidence_contract"]["read_back"] == "read"


def test_evidence_contract_rejects_extra_or_missing_keys():
    with pytest.raises(EvolutionError):
        validate_manifest(base(evidence_contract={"read_back": "read"}))
    with pytest.raises(EvolutionError):
        validate_manifest(
            base(evidence_contract={"read_back": "read", "postcondition": "x", "extra": "y"})
        )


@pytest.mark.parametrize(
    "value", ["not_applicable", "none_irreversible", "compensating_operation:reset"]
)
def test_rollback_semantics_accepts_declared_shapes(value):
    normalized = validate_manifest(base(rollback_semantics=value))
    assert normalized["rollback_semantics"] == value


def test_rollback_semantics_rejects_a_malformed_compensating_operation():
    with pytest.raises(EvolutionError):
        validate_manifest(base(rollback_semantics="compensating_operation:"))
    with pytest.raises(EvolutionError):
        validate_manifest(base(rollback_semantics="just_rollback_it"))


def test_localhost_is_a_valid_network_permission_host():
    """M24: ``localhost`` (no dot-label) is a valid loopback host for
    app.genesis.interface, so it must be a valid network_permissions entry
    too, or a genesis manifest against ``http://localhost:<port>`` could
    never validate."""
    normalized = validate_manifest(base(network_permissions=["localhost"]))
    assert normalized["network_permissions"] == ["localhost"]


# --------------------------------------------------- registry.resolve exposes them


def test_registry_resolve_exposes_the_six_keys(tmp_path):
    """A real run (through GenesisService, exactly as test_genesis_end_to_end
    proves works end to end) registers a manifest whose row `registry.resolve`
    returns carries all six spec §4 keys."""
    stack = make_stack(tmp_path)
    with counterbox_app.serve() as server:
        result = stack.service.request(
            interface_name="counterbox", interface_url=server.spec_url, operation_id="read"
        )
        assert result["state"] == "verified", result
        resolved = stack.registry.resolve("counterbox.read")
        assert resolved["manifest"]["authority_class"] == "read_only"
        assert resolved["manifest"]["side_effect_class"] == "read"
        assert resolved["manifest"]["evidence_contract"]["read_back"] == "read"
        assert resolved["manifest"]["rollback_semantics"] == "not_applicable"
        assert resolved["manifest"]["input_schema"] == []
        assert resolved["manifest"]["output_schema"] == [
            {"name": "value", "type": "integer", "required": True}
        ]


# ------------------------------------------------------- dispatcher refusal


def _fake_registry_resolving(manifest, root):
    class _Fake:
        def resolve(self, capability_id):
            return {
                "capability_id": capability_id,
                "version": "0.1.0",
                "skill_version": {"id": str(uuid.uuid4()), "source_ref": str(root)},
            }

    return _Fake()


def test_dispatcher_refuses_mutate_external_mutating_unauthorized(tmp_path):
    from app.evolution.sandbox import SandboxPolicy
    from app.evolution.skills import dump_manifest_yaml

    root = tmp_path / "skill"
    (root / "src").mkdir(parents=True)
    manifest = base(
        authority_class="mutating_unauthorized",
        side_effect_class="mutate_external",
        skill="whatever",
        entrypoint="run",
    )
    (root / "manifest.yaml").write_text(dump_manifest_yaml(manifest), encoding="utf-8")
    (root / "src" / "whatever.py").write_text("def run(p):\n    return {}\n", encoding="utf-8")

    dispatcher = CapabilityDispatcher(
        _fake_registry_resolving(manifest, root), sandbox=SandboxPolicy(tmp_path)
    )
    with pytest.raises(EvolutionError) as excinfo:
        dispatcher.dispatch("text.slugify", {})
    assert str(excinfo.value.error_class) == "permission_denied"


def test_dispatcher_allows_mutate_external_when_authorized(tmp_path):
    """The SAME shape, but authority_class=mutating_authorized_asset — the
    hard rule is specific to the unauthorized combination only."""
    from app.evolution.sandbox import SandboxPolicy
    from app.evolution.skills import dump_manifest_yaml

    root = tmp_path / "skill2"
    (root / "src").mkdir(parents=True)
    manifest = base(
        authority_class="mutating_authorized_asset",
        side_effect_class="mutate_external",
        skill="whatever",
        entrypoint="run",
    )
    (root / "manifest.yaml").write_text(dump_manifest_yaml(manifest), encoding="utf-8")
    (root / "src" / "whatever.py").write_text(
        "import json\n"
        "import sys\n\n"
        "def run(p):\n"
        "    return {'ok': True}\n\n"
        "if __name__ == '__main__':\n"
        "    sys.stdout.write(json.dumps(run({})))\n",
        encoding="utf-8",
    )
    dispatcher = CapabilityDispatcher(
        _fake_registry_resolving(manifest, root), sandbox=SandboxPolicy(tmp_path)
    )
    result = dispatcher.dispatch("text.slugify", {})
    assert result.output == {"ok": True}
