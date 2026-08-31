"""Typed security error taxonomy (mirrors app.evolution.errors conventions)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SecurityErrorClass(StrEnum):
    # Caller-supplied enrollment/assessment arguments are invalid or unbounded.
    VALIDATION_ERROR = "validation_error"
    # The referenced asset/assessment/finding does not exist.
    NOT_FOUND = "not_found"
    # An asset_ref is already enrolled.
    ALREADY_ENROLLED = "already_enrolled"
    # scope.py refused: target/action is outside the owner's stored scope.
    # This is THE constitution §8 refusal; details carry the machine reason.
    OUT_OF_SCOPE = "out_of_scope"
    # A remediation was requested that the asset's stored constraints forbid
    # (e.g. max_disruption: low against a medium-disruption fix).
    CONSTRAINT_VIOLATION = "constraint_violation"
    # The finding's remediation is proposal-only; no safe automated apply exists.
    REMEDIATION_NOT_AUTOMATABLE = "remediation_not_automatable"
    # A collector/remediation path escaped the asset's recorded config roots.
    COLLECTOR_ROOT_VIOLATION = "collector_root_violation"
    # The recorded collection target is missing/unreadable on this machine.
    TARGET_UNAVAILABLE = "target_unavailable"
    # Anything unexpected.
    INTERNAL_BUG = "internal_bug"


@dataclass(slots=True)
class SecurityError(Exception):
    """A security-subsystem failure with a stable, typed classification."""

    error_class: SecurityErrorClass
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


__all__ = ["SecurityError", "SecurityErrorClass"]
