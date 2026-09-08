"""Versioned capability manifest (ACCEPTANCE_TESTS M7 "Capability manifest",
ADR-0025 delta, SECURITY_MODEL M7).

Every field the owner enumerated is REQUIRED and validated here; the registry
delegates to this module, so there is exactly one schema authority:

    capability ID .......... id
    human-readable purpose . purpose
    version ................ version
    input/output schema .... inputs / outputs (flat names, EVOLUTION_ENGINE_SPEC
                             §2 + the composer) and input_schema / output_schema
                             (typed {name,type,required} records)
    dependencies ........... dependencies (pinned {name,version,source,digest})
    network permissions .... network_permissions   (deny-by-default: [])
    filesystem permissions . filesystem_permissions(deny-by-default: [])
    device permissions ..... device_permissions    (deny-by-default: [])
    secret requirements .... secret_requirements   (deny-by-default: [])
    external services ...... external_services
    expected side effects .. side_effects
    risk classification .... risk_class
    tests .................. tests
    evaluation metrics ..... health_metrics (declared names, gate-enforced) and
                             evaluation_metrics (recorded values at promotion)
    provenance ............. provenance
    builder identity ....... builder_identity
    creation reason/task ... creation_reason
    rollback version ....... rollback_version

DENY-BY-DEFAULT (SECURITY_MODEL M7): a manifest whose ``builder_identity.kind``
is ``generated`` may only carry a non-empty permission grant when the
independent reviewer explicitly approved that exact grant set — see
``require_permission_approval``. ``risk_class`` and ``side_effects`` must be
CONSISTENT with the grants, so a generated skill cannot quietly declare device
access while calling itself low-risk.

Every scalar that can reach generated source still goes through
``app.evolution.tokens``.
"""

from __future__ import annotations

import re
from typing import Any

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.tokens import (
    require_capability_id,
    require_identifier,
    require_owner_scope,
    require_slug,
    require_slug_list,
    require_summary,
    require_version,
)

# The single dispatchable entrypoint name a generated skill may declare.
SUPPORTED_ENTRYPOINT = "run"

CAPABILITY_STATUSES = ("proposed", "experimental", "production", "deprecated")

RISK_CLASSES = ("low", "moderate", "high", "critical")
RISK_ORDER = {name: index for index, name in enumerate(RISK_CLASSES)}

SIDE_EFFECTS = (
    "pure",
    "reads_filesystem",
    "writes_filesystem",
    "network_egress",
    "device_control",
    "reads_secrets",
)

IO_TYPES = ("string", "integer", "number", "boolean", "object", "array")

FILESYSTEM_MODES = ("read", "write")

# A host a generated capability may talk to, when (and only when) a grant exists.
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9-]{1,63}){1,4}$")
#: M24: ``localhost`` has no dot-label and would otherwise fail HOSTNAME_RE, but
#: it is one of the two loopback hosts ``app.genesis.interface`` accepts for a
#: ``base_url`` — an IPv4-literal loopback host (``127.0.0.1``) already matches
#: HOSTNAME_RE unchanged (digits are inside ``[a-z0-9]``), so only this one
#: single-label name needs an explicit allowance.
LOOPBACK_SINGLE_LABEL_HOSTS = frozenset({"localhost"})
# A filesystem grant is an absolute-ish path token without traversal.
FS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/][^\r\n\"']{0,200}$|^/[^\r\n\"']{0,200}$")

REQUIRED_FIELDS = (
    "id",
    "purpose",
    "version",
    "status",
    "inputs",
    "outputs",
    "input_schema",
    "output_schema",
    "dependencies",
    "network_permissions",
    "filesystem_permissions",
    "device_permissions",
    "secret_requirements",
    "external_services",
    "side_effects",
    "risk_class",
    "tests",
    "health_metrics",
    "evaluation_metrics",
    "provenance",
    "builder_identity",
    "creation_reason",
    "rollback_version",
    "owner_scope",
)

#: M24 (ADR-0087, spec §4): the six additive keys a genesis HTTP adapter's
#: manifest carries. Two of the spec's six (``input_schema``/``output_schema``)
#: are the EXISTING required typed-io fields above — ``HttpAdapterGenerator``
#: populates them from its own bounded JSON-schema subset
#: (``app.genesis.interface.ObjectSchema.to_manifest_schema``), so no schema
#: change was needed for those two. The four genuinely NEW optional keys are
#: below. Every existing manifest and test is unchanged: nothing here is
#: required, and ``validate_manifest`` only shape-checks a key when present.
AUTHORITY_CLASSES = ("read_only", "mutating_authorized_asset", "mutating_unauthorized")
SIDE_EFFECT_CLASSES = ("none", "read", "mutate_external")
ROLLBACK_SEMANTICS_NONE_APPLICABLE = "not_applicable"
ROLLBACK_SEMANTICS_NONE_IRREVERSIBLE = "none_irreversible"
ROLLBACK_SEMANTICS_COMPENSATING_RE = re.compile(r"^compensating_operation:[a-z][a-z0-9_]{0,31}$")

OPTIONAL_FIELDS = (
    "skill",
    "entrypoint",
    "source_ref",
    "configurable_for",
    "extension_points",
    "resource_budget",
    "components",
    # M24 additive keys (spec §4):
    "authority_class",
    "side_effect_class",
    "evidence_contract",
    "rollback_semantics",
)

PERMISSION_FIELDS = (
    "network_permissions",
    "filesystem_permissions",
    "device_permissions",
    "secret_requirements",
)

MAX_LIST_ITEMS = 32


def _fail(message: str, **details: Any) -> EvolutionError:
    return EvolutionError(EvolutionErrorClass.VALIDATION_ERROR, message, details=details)


# ------------------------------------------------------------------ pieces


def _require_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(f"manifest.{field} must be an object")
    return value


def _require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list | tuple):
        raise _fail(f"manifest.{field} must be a list")
    if len(value) > MAX_LIST_ITEMS:
        raise _fail(f"manifest.{field} may hold at most {MAX_LIST_ITEMS} entries")
    return list(value)


def _require_text(value: Any, field: str, max_length: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise _fail(f"manifest.{field} must be a string of 1..{max_length} chars")
    if not value.isprintable():
        raise _fail(f"manifest.{field} must be printable single-line text")
    return value


def validate_io_schema(entries: Any, *, field: str, names: list[str]) -> list[dict[str, Any]]:
    """Typed I/O schema; its names must match the flat ``inputs``/``outputs``."""
    parsed: list[dict[str, Any]] = []
    for index, entry in enumerate(_require_list(entries, field)):
        entry = _require_dict(entry, f"{field}[{index}]")
        unknown = set(entry) - {"name", "type", "required"}
        if unknown:
            raise _fail(f"manifest.{field}[{index}] has unknown keys: {sorted(unknown)}")
        kind = entry.get("type")
        if kind not in IO_TYPES:
            raise _fail(f"manifest.{field}[{index}].type must be one of {IO_TYPES}")
        required = entry.get("required", True)
        if not isinstance(required, bool):
            raise _fail(f"manifest.{field}[{index}].required must be a boolean")
        parsed.append(
            {
                "name": require_slug(entry.get("name"), field=f"{field}[{index}].name"),
                "type": kind,
                "required": required,
            }
        )
    if [entry["name"] for entry in parsed] != list(names):
        raise _fail(
            f"manifest.{field} names must match the flat io list exactly",
            expected=list(names),
        )
    return parsed


def validate_dependencies(entries: Any) -> list[dict[str, Any]]:
    """Pinned dependency records — the supply-chain contract (§ Isolation).

    Shape validation only; the *policy* (allowed sources, pinning, install
    scripts) lives in app.evolution.supply_chain so the scan is a separate,
    independently testable gate.
    """
    parsed: list[dict[str, Any]] = []
    for index, entry in enumerate(_require_list(entries, "dependencies")):
        entry = _require_dict(entry, f"dependencies[{index}]")
        unknown = set(entry) - {"name", "version", "source", "digest", "install_script"}
        if unknown:
            raise _fail(f"manifest.dependencies[{index}] has unknown keys: {sorted(unknown)}")
        record = {
            "name": require_slug(entry.get("name"), field=f"dependencies[{index}].name"),
            "version": require_version(
                entry.get("version"), field=f"dependencies[{index}].version"
            ),
            "source": _require_text(entry.get("source"), f"dependencies[{index}].source"),
            "digest": _require_text(entry.get("digest"), f"dependencies[{index}].digest", 128),
        }
        if entry.get("install_script"):
            record["install_script"] = _require_text(
                entry["install_script"], f"dependencies[{index}].install_script"
            )
        parsed.append(record)
    return parsed


def validate_permissions(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate the four grant blocks. Empty everywhere == deny-by-default."""
    network = [
        _require_text(host, "network_permissions[]", 253)
        for host in _require_list(manifest.get("network_permissions"), "network_permissions")
    ]
    for host in network:
        if host not in LOOPBACK_SINGLE_LABEL_HOSTS and not HOSTNAME_RE.match(host):
            raise _fail("manifest.network_permissions entries must be hostnames")
    filesystem: list[dict[str, str]] = []
    for index, entry in enumerate(
        _require_list(manifest.get("filesystem_permissions"), "filesystem_permissions")
    ):
        entry = _require_dict(entry, f"filesystem_permissions[{index}]")
        unknown = set(entry) - {"path", "mode"}
        if unknown:
            raise _fail(
                f"manifest.filesystem_permissions[{index}] has unknown keys: {sorted(unknown)}"
            )
        path = _require_text(entry.get("path"), f"filesystem_permissions[{index}].path")
        if not FS_PATH_RE.match(path) or ".." in path:
            raise _fail(
                "manifest.filesystem_permissions paths must be absolute and traversal-free"
            )
        if entry.get("mode") not in FILESYSTEM_MODES:
            raise _fail(
                f"manifest.filesystem_permissions[{index}].mode must be read or write"
            )
        filesystem.append({"path": path, "mode": entry["mode"]})
    device = require_slug_list(
        manifest.get("device_permissions"), field="device_permissions", max_items=MAX_LIST_ITEMS
    )
    secrets = require_slug_list(
        manifest.get("secret_requirements"),
        field="secret_requirements",
        max_items=MAX_LIST_ITEMS,
    )
    return {
        "network_permissions": network,
        "filesystem_permissions": filesystem,
        "device_permissions": device,
        "secret_requirements": secrets,
    }


def granted_permissions(manifest: dict[str, Any]) -> dict[str, list[Any]]:
    """The non-empty grants of a manifest (the audit's "what was granted")."""
    return {
        field: list(manifest.get(field) or [])
        for field in PERMISSION_FIELDS
        if manifest.get(field)
    }


def implied_risk_class(manifest: dict[str, Any]) -> str:
    """Deterministic floor for ``risk_class`` given the declared grants."""
    if manifest.get("device_permissions") or manifest.get("secret_requirements"):
        return "high"
    if manifest.get("network_permissions") or any(
        entry.get("mode") == "write"
        for entry in manifest.get("filesystem_permissions") or []
    ):
        return "moderate"
    if manifest.get("filesystem_permissions") or manifest.get("external_services"):
        return "moderate"
    return "low"


def implied_side_effects(manifest: dict[str, Any]) -> set[str]:
    effects: set[str] = set()
    for entry in manifest.get("filesystem_permissions") or []:
        effects.add("writes_filesystem" if entry.get("mode") == "write" else "reads_filesystem")
    if manifest.get("network_permissions") or manifest.get("external_services"):
        effects.add("network_egress")
    if manifest.get("device_permissions"):
        effects.add("device_control")
    if manifest.get("secret_requirements"):
        effects.add("reads_secrets")
    return effects or {"pure"}


# ---------------------------------------------------------------- validation


def validate_manifest(manifest: Any) -> dict[str, Any]:
    """Validate the FULL owner-required schema; return a normalized copy."""
    manifest = _require_dict(manifest, "<root>")
    missing = [key for key in REQUIRED_FIELDS if key not in manifest]
    if missing:
        raise _fail(
            f"capability manifest is missing required keys: {missing}",
            missing=missing,
            required=list(REQUIRED_FIELDS),
        )
    unknown = [k for k in manifest if k not in REQUIRED_FIELDS and k not in OPTIONAL_FIELDS]
    if unknown:
        raise _fail(
            f"capability manifest carries unknown keys: {sorted(unknown)}",
            unknown=sorted(unknown),
        )
    if manifest["status"] not in CAPABILITY_STATUSES:
        raise _fail(
            f"invalid capability status: {manifest['status']!r}",
            allowed=list(CAPABILITY_STATUSES),
        )
    if manifest["risk_class"] not in RISK_CLASSES:
        raise _fail(f"manifest.risk_class must be one of {RISK_CLASSES}")

    inputs = require_slug_list(manifest["inputs"], field="manifest.inputs")
    outputs = require_slug_list(manifest["outputs"], field="manifest.outputs")

    normalized: dict[str, Any] = {
        "id": require_capability_id(manifest["id"], field="manifest.id"),
        "purpose": require_summary(manifest["purpose"], field="manifest.purpose"),
        "version": require_version(manifest["version"], field="manifest.version"),
        "status": manifest["status"],
        "inputs": inputs,
        "outputs": outputs,
        "input_schema": validate_io_schema(
            manifest["input_schema"], field="input_schema", names=inputs
        ),
        "output_schema": validate_io_schema(
            manifest["output_schema"], field="output_schema", names=outputs
        ),
        "dependencies": validate_dependencies(manifest["dependencies"]),
        "external_services": require_slug_list(
            manifest["external_services"], field="manifest.external_services"
        ),
        "side_effects": require_slug_list(
            manifest["side_effects"], field="manifest.side_effects"
        ),
        "risk_class": manifest["risk_class"],
        "tests": _require_dict(manifest["tests"], "tests"),
        "health_metrics": require_slug_list(
            manifest["health_metrics"], field="manifest.health_metrics"
        ),
        "evaluation_metrics": _require_dict(manifest["evaluation_metrics"], "evaluation_metrics"),
        "provenance": _require_dict(manifest["provenance"], "provenance"),
        "builder_identity": _require_dict(manifest["builder_identity"], "builder_identity"),
        "creation_reason": _require_dict(manifest["creation_reason"], "creation_reason"),
        "owner_scope": require_owner_scope(manifest["owner_scope"]),
    }
    normalized.update(validate_permissions(manifest))

    for effect in normalized["side_effects"]:
        if effect not in SIDE_EFFECTS:
            raise _fail(f"manifest.side_effects entries must be one of {SIDE_EFFECTS}")

    rollback = manifest["rollback_version"]
    if rollback is not None:
        rollback = require_version(rollback, field="manifest.rollback_version")
    normalized["rollback_version"] = rollback

    # Builder identity: who/what produced this manifest.
    builder = normalized["builder_identity"]
    if set(builder) - {"kind", "name", "version"} or "kind" not in builder:
        raise _fail("manifest.builder_identity needs kind (+ optional name/version)")
    if builder["kind"] not in ("generated", "curated", "core"):
        raise _fail("manifest.builder_identity.kind must be generated|curated|core")

    # Creation reason: which gap/task caused this to exist.
    reason = normalized["creation_reason"]
    if set(reason) - {
        "gap_id",
        "task_id",
        "trigger",
        "request_digest",
        "depth",
        "authorized_asset",
        "resolution_path",
    }:
        raise _fail("manifest.creation_reason has unknown keys")
    if reason.get("trigger") not in ("capability_gap", "improvement", "seed", None):
        raise _fail("manifest.creation_reason.trigger is not a known trigger")

    # Consistency: a manifest cannot understate its own blast radius.
    floor = implied_risk_class(normalized)
    if RISK_ORDER[normalized["risk_class"]] < RISK_ORDER[floor]:
        raise _fail(
            "manifest.risk_class understates the declared permissions",
            declared=normalized["risk_class"],
            minimum=floor,
        )
    implied = implied_side_effects(normalized)
    if not implied <= set(normalized["side_effects"]):
        raise _fail(
            "manifest.side_effects omits effects implied by the declared permissions",
            missing=sorted(implied - set(normalized["side_effects"])),
        )

    # Optional engine keys.
    if "skill" in manifest:
        normalized["skill"] = require_identifier(manifest["skill"], field="manifest.skill")
    if "entrypoint" in manifest:
        if manifest["entrypoint"] != SUPPORTED_ENTRYPOINT:
            raise _fail(f"manifest.entrypoint must be {SUPPORTED_ENTRYPOINT!r}")
        normalized["entrypoint"] = SUPPORTED_ENTRYPOINT
    if "source_ref" in manifest:
        normalized["source_ref"] = _require_text(manifest["source_ref"], "source_ref", 512)
    for list_key in ("configurable_for", "extension_points"):
        if list_key in manifest:
            normalized[list_key] = require_slug_list(
                manifest[list_key], field=f"manifest.{list_key}"
            )
    if "resource_budget" in manifest:
        normalized["resource_budget"] = _require_dict(
            manifest["resource_budget"], "resource_budget"
        )
    if "components" in manifest:
        normalized["components"] = validate_dependencies(manifest["components"])
    # M24 additive keys (spec §4). Shape-checked only when present; nothing
    # here is consistency-checked against risk_class/side_effects the way the
    # existing permission grants are — the dispatcher enforces the ONE hard
    # rule that matters (a mutate_external + mutating_unauthorized capability
    # never dispatches), in app.evolution.task_resumption.
    if "authority_class" in manifest:
        if manifest["authority_class"] not in AUTHORITY_CLASSES:
            raise _fail(f"manifest.authority_class must be one of {AUTHORITY_CLASSES}")
        normalized["authority_class"] = manifest["authority_class"]
    if "side_effect_class" in manifest:
        if manifest["side_effect_class"] not in SIDE_EFFECT_CLASSES:
            raise _fail(f"manifest.side_effect_class must be one of {SIDE_EFFECT_CLASSES}")
        normalized["side_effect_class"] = manifest["side_effect_class"]
    if "evidence_contract" in manifest:
        contract = _require_dict(manifest["evidence_contract"], "evidence_contract")
        unknown_contract = set(contract) - {"read_back", "postcondition"}
        if unknown_contract or "read_back" not in contract or "postcondition" not in contract:
            raise _fail(
                "manifest.evidence_contract must be an object with exactly "
                "read_back and postcondition"
            )
        normalized["evidence_contract"] = {
            "read_back": require_slug(contract["read_back"], field="evidence_contract.read_back"),
            "postcondition": _require_text(
                contract["postcondition"], "evidence_contract.postcondition", 256
            ),
        }
    if "rollback_semantics" in manifest:
        value = manifest["rollback_semantics"]
        valid = value in (
            ROLLBACK_SEMANTICS_NONE_APPLICABLE,
            ROLLBACK_SEMANTICS_NONE_IRREVERSIBLE,
        ) or (isinstance(value, str) and ROLLBACK_SEMANTICS_COMPENSATING_RE.match(value))
        if not valid:
            raise _fail(
                "manifest.rollback_semantics must be not_applicable, none_irreversible or "
                "compensating_operation:<id>"
            )
        normalized["rollback_semantics"] = value
    return normalized


def require_permission_approval(manifest: dict[str, Any], review: Any) -> None:
    """DENY-BY-DEFAULT gate for generated skills (SECURITY_MODEL M7).

    A generated manifest with any non-empty grant is refused unless the
    INDEPENDENT review recorded ``permissions_approved: true`` together with the
    exact grant set it approved. Approving "some permissions" is not enough —
    the approved set must equal the requested set.
    """
    if (manifest.get("builder_identity") or {}).get("kind") != "generated":
        return
    grants = granted_permissions(manifest)
    if not grants:
        return
    review = review if isinstance(review, dict) else {}
    if review.get("permissions_approved") is not True:
        raise EvolutionError(
            EvolutionErrorClass.REGISTRATION_REFUSED,
            "generated skills are deny-by-default: the independent reviewer did not "
            "approve the requested permission grants",
            details={"requested": grants},
        )
    approved = review.get("approved_permissions")
    if approved != grants:
        raise EvolutionError(
            EvolutionErrorClass.REGISTRATION_REFUSED,
            "the reviewer-approved permission set does not match the manifest grants",
            details={"requested": grants, "approved": approved},
        )


# ------------------------------------------------------------------ builder


def default_manifest(
    capability_id: str,
    version: str,
    *,
    purpose: str,
    inputs: list[str],
    outputs: list[str],
    status: str = "experimental",
    builder_kind: str = "generated",
    builder_name: str = "deterministic",
    health_metrics: list[str] | None = None,
    input_types: dict[str, str] | None = None,
    output_types: dict[str, str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """A complete, deny-by-default manifest skeleton.

    Everything that grants power starts EMPTY; callers must opt in explicitly
    and get reviewer approval for it.
    """
    input_types = input_types or {}
    output_types = output_types or {}
    manifest: dict[str, Any] = {
        "id": capability_id,
        "purpose": purpose,
        "version": version,
        "status": status,
        "inputs": list(inputs),
        "outputs": list(outputs),
        "input_schema": [
            {"name": name, "type": input_types.get(name, "string"), "required": True}
            for name in inputs
        ],
        "output_schema": [
            {"name": name, "type": output_types.get(name, "string"), "required": True}
            for name in outputs
        ],
        "dependencies": [],
        "network_permissions": [],
        "filesystem_permissions": [],
        "device_permissions": [],
        "secret_requirements": [],
        "external_services": [],
        "side_effects": ["pure"],
        "risk_class": "low",
        "tests": {},
        "health_metrics": list(health_metrics or []),
        "evaluation_metrics": {},
        "provenance": {},
        "builder_identity": {"kind": builder_kind, "name": builder_name},
        "creation_reason": {},
        "rollback_version": None,
        "owner_scope": "normal",
    }
    manifest.update(overrides)
    # Keep the declared blast radius honest even when a caller opted into grants.
    floor = implied_risk_class(manifest)
    if RISK_ORDER[manifest["risk_class"]] < RISK_ORDER[floor]:
        manifest["risk_class"] = floor
    implied = implied_side_effects(manifest)
    declared = set(manifest["side_effects"]) | implied
    if len(declared) > 1:
        declared.discard("pure")
    manifest["side_effects"] = sorted(declared)
    return manifest


__all__ = [
    "AUTHORITY_CLASSES",
    "CAPABILITY_STATUSES",
    "FILESYSTEM_MODES",
    "IO_TYPES",
    "LOOPBACK_SINGLE_LABEL_HOSTS",
    "MAX_LIST_ITEMS",
    "OPTIONAL_FIELDS",
    "PERMISSION_FIELDS",
    "REQUIRED_FIELDS",
    "RISK_CLASSES",
    "ROLLBACK_SEMANTICS_NONE_APPLICABLE",
    "ROLLBACK_SEMANTICS_NONE_IRREVERSIBLE",
    "SIDE_EFFECTS",
    "SIDE_EFFECT_CLASSES",
    "SUPPORTED_ENTRYPOINT",
    "default_manifest",
    "granted_permissions",
    "implied_risk_class",
    "implied_side_effects",
    "require_permission_approval",
    "validate_dependencies",
    "validate_io_schema",
    "validate_manifest",
    "validate_permissions",
]
