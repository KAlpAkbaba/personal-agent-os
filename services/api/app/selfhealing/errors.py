"""Typed self-healing error taxonomy (mirrors app.memory.errors conventions)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SelfHealingErrorClass(StrEnum):
    # Caller-supplied arguments/report payload are invalid.
    VALIDATION_ERROR = "validation_error"
    # The referenced incident/release does not exist.
    NOT_FOUND = "not_found"
    # A coding backend exists as a seam but is not configured (Claude CLI).
    BACKEND_NOT_CONFIGURED = "backend_not_configured"
    # The backend could not derive a patch from the incident evidence.
    PATCH_DERIVATION_FAILED = "patch_derivation_failed"
    # The independent reviewer refused the candidate (builder can never promote).
    REVIEW_REJECTED = "review_rejected"
    # The bug could not be reproduced in isolation (nothing to fix).
    REPRODUCTION_FAILED = "reproduction_failed"
    # A pipeline stage failed (deploy unhealthy, regression still failing, ...).
    PIPELINE_STAGE_FAILED = "pipeline_stage_failed"
    # Anything unexpected.
    INTERNAL_BUG = "internal_bug"


@dataclass(slots=True)
class SelfHealingError(Exception):
    """A self-healing subsystem failure with a stable, typed classification."""

    error_class: SelfHealingErrorClass
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Zero-arg super() is unsafe under @dataclass(slots=True); call the
        # base initializer explicitly (same rationale as app.memory.errors).
        Exception.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": str(self.error_class),
            "message": self.message,
            "details": self.details,
        }


__all__ = ["SelfHealingError", "SelfHealingErrorClass"]
