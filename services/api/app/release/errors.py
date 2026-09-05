"""Typed release-execution error taxonomy (mirrors app.selfhealing.errors)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ReleaseErrorClass(StrEnum):
    # Caller-supplied candidate/evidence is invalid or incomplete.
    VALIDATION_ERROR = "validation_error"
    # Preflight refused the release before any production mutation.
    PREFLIGHT_REFUSED = "preflight_refused"
    # The deployment backend reported failure.
    DEPLOY_FAILED = "deploy_failed"
    # Post-deployment verification (health/version/provenance) failed.
    VERIFICATION_FAILED = "verification_failed"
    # A rollback itself could not be completed.
    ROLLBACK_FAILED = "rollback_failed"
    # Anything unexpected.
    INTERNAL_BUG = "internal_bug"


@dataclass(slots=True)
class ReleaseError(Exception):
    """A release-execution failure with a stable, typed classification."""

    error_class: ReleaseErrorClass
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": str(self.error_class),
            "message": self.message,
            "details": self.details,
        }


__all__ = ["ReleaseError", "ReleaseErrorClass"]
