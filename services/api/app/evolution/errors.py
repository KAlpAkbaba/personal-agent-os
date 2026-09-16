"""Typed evolution error taxonomy (mirrors app.selfhealing.errors conventions)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EvolutionErrorClass(StrEnum):
    # Caller-supplied arguments/manifest/spec are invalid.
    VALIDATION_ERROR = "validation_error"
    # The referenced capability/gap/skill version does not exist.
    NOT_FOUND = "not_found"
    # Dispatch asked for a capability that is not registered/production.
    CAPABILITY_MISSING = "capability_missing"
    # Code generation was requested before composition was genuinely attempted.
    GENERATION_REFUSED = "generation_refused"
    # The requirement implies a product/core/recovery change — never auto-generated.
    PRODUCT_CHANGE_REQUIRED = "product_change_required"
    # A path escaped the configured evolution sandbox root (§13).
    SANDBOX_VIOLATION = "sandbox_violation"
    # A generator seam exists but is not configured (Claude Agent SDK).
    GENERATOR_NOT_CONFIGURED = "generator_not_configured"
    # The generator refused a hostile/unsupported skill spec.
    GENERATION_FAILED = "generation_failed"
    # Generated tests/evals did not meet the release-score gates (§9).
    EVALUATION_FAILED = "evaluation_failed"
    # A dependency scan rule fired (unpinned/unknown source/install script...).
    SUPPLY_CHAIN_REJECTED = "supply_chain_rejected"
    # A pinned dependency/component is not actually available locally.
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    # A configured resource budget was exceeded (timeout/disk/output/cpu/memory).
    RESOURCE_BUDGET_EXCEEDED = "resource_budget_exceeded"
    # The self-extension recursion depth limit was reached.
    RECURSION_LIMIT_EXCEEDED = "recursion_limit_exceeded"
    # A candidate asked for a permission the manifest does not grant.
    PERMISSION_DENIED = "permission_denied"
    # An illegal lifecycle transition (e.g. active without canary evidence).
    LIFECYCLE_VIOLATION = "lifecycle_violation"
    # A candidate performed worse than the incumbent in shadow/canary/benchmark.
    NOT_SUPERIOR = "not_superior"
    # The INDEPENDENT reviewer refused the candidate (§6).
    REVIEW_REJECTED = "review_rejected"
    # Registration attempted without a passing, evaluated skill version (§2).
    REGISTRATION_REFUSED = "registration_refused"
    # Executing a registered capability failed at runtime.
    DISPATCH_FAILED = "dispatch_failed"
    # M24 (ADR-0087): a response conformed to nothing (off-schema, or the
    # evidence_contract's read-back did not satisfy the declared postcondition).
    POSTCONDITION_FAILED = "postcondition_failed"
    # M24: a genesis run's own bound was exceeded (>1 active run per capability,
    # >10 minutes end to end, >3 runs/hour per interface).
    RATE_LIMITED = "rate_limited"
    # B36 (req 579/680): the security gate on a generated adapter refused it.
    SECURITY_REFUSED = "security_refused"
    # Anything unexpected.
    INTERNAL_BUG = "internal_bug"


@dataclass(slots=True)
class EvolutionError(Exception):
    """An evolution-engine failure with a stable, typed classification."""

    error_class: EvolutionErrorClass
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Zero-arg super() is unsafe under @dataclass(slots=True).
        Exception.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": str(self.error_class),
            "message": self.message,
            "details": self.details,
        }


__all__ = ["EvolutionError", "EvolutionErrorClass"]
