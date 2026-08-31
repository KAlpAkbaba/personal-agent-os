"""Typed memory error taxonomy (mirrors app.voice.errors conventions).

Every memory-subsystem failure raises :class:`MemorySubsystemError` with a
stable ``error_class`` so callers (routes, backends, tests) branch on typed
values instead of message strings. The name avoids shadowing the builtin
``MemoryError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MemoryErrorClass(StrEnum):
    # Secrets guard: content matched a credential/token pattern and was refused.
    SECRET_REJECTED = "secret_rejected"
    # The referenced memory/entity does not exist (or is not addressable).
    NOT_FOUND = "not_found"
    # An explicit owner memory may only be changed by Actor.OWNER.
    EXPLICIT_PROTECTED = "explicit_protected"
    # Caller-supplied arguments are invalid.
    VALIDATION_ERROR = "validation_error"
    # The selected backend documents a seam but has no implementation (Mem0 stub).
    BACKEND_NOT_IMPLEMENTED = "backend_not_implemented"
    # Anything unexpected.
    INTERNAL_BUG = "internal_bug"


@dataclass(slots=True)
class MemorySubsystemError(Exception):
    """A memory-subsystem failure with a stable, typed classification."""

    error_class: MemoryErrorClass
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Zero-arg super() is unsafe under @dataclass(slots=True); call the
        # base initializer explicitly (same rationale as app.voice.errors).
        Exception.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": str(self.error_class),
            "message": self.message,
            "details": self.details,
        }


__all__ = ["MemoryErrorClass", "MemorySubsystemError"]
