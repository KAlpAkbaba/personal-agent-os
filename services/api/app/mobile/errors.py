"""Typed mobile/push error taxonomy.

Same discipline as `app/voice/errors.py`: every push adapter and the mobile
service raise :class:`MobileError` and nothing else, so routes branch on a
stable ``error_class`` instead of vendor exception types. The class names come
from the project-wide taxonomy (docs/API_AND_PROTOCOLS.md §8) plus the same
voice-style ``provider_auth_missing`` marker for a real adapter that exists but
is inert because the owner has not provisioned credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MobileErrorClass(StrEnum):
    """Mobile-relevant subset of the project error taxonomy."""

    #: A real push adapter exists but has no credentials -> the send path is inert.
    PROVIDER_AUTH_MISSING = "provider_auth_missing"
    #: Provider reachable-but-failing, or a transport error (retryable).
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    #: The provider told us this token is dead; the registration must be invalidated.
    PUSH_TOKEN_INVALID = "push_token_invalid"
    #: No provider is registered under the requested name.
    CAPABILITY_MISSING = "capability_missing"
    #: Caller-supplied arguments are invalid (bad token shape, bad transition, ...).
    VALIDATION_ERROR = "validation_error"
    #: The requested payload exceeds a declared bound (share/export size).
    PAYLOAD_TOO_LARGE = "payload_too_large"
    #: Provider exceeded its latency budget.
    TIMEOUT = "timeout"
    #: httpx (or another optional dependency) is not installed.
    OPTIONAL_DEPENDENCY_MISSING = "optional_dependency_missing"
    #: The owner session backing this registration is gone.
    SESSION_REVOKED = "session_revoked"
    #: Anything unexpected.
    INTERNAL_BUG = "internal_bug"


@dataclass(slots=True)
class MobileError(Exception):
    """A mobile-subsystem failure with a stable, typed classification."""

    error_class: MobileErrorClass
    message: str
    provider: str | None = None
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # NB: zero-arg super() is unsafe under @dataclass(slots=True) (the
        # decorator recreates the class, breaking the __class__ cell), so call
        # the base initializer explicitly. Same as VoiceError.
        Exception.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": str(self.error_class),
            "message": self.message,
            "provider": self.provider,
            "retryable": self.retryable,
            "details": self.details,
        }


#: A delivery failure the caller may retry against the same or another provider.
#: A dead token is NOT retryable — the registration is invalidated instead.
RETRYABLE: frozenset[MobileErrorClass] = frozenset(
    {
        MobileErrorClass.DEPENDENCY_UNAVAILABLE,
        MobileErrorClass.TIMEOUT,
    }
)


__all__ = ["RETRYABLE", "MobileError", "MobileErrorClass"]
