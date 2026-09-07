"""Typed voice error taxonomy (MODEL_AND_PROVIDER_ROUTING.md §5, VOICE_SPEC §2/§7).

Every voice provider adapter and the provider router raise :class:`VoiceError`
and nothing else, so callers (routes, benchmark, realtime loop) can branch on a
stable ``error_class`` instead of vendor-specific exception types. The class
names come from the project-wide taxonomy (docs/API_AND_PROTOCOLS.md §8); this
module uses the voice-relevant subset plus one voice-specific value
(``provider_auth_missing``) that marks a real adapter that is present but inert
because no API key is configured (an owner action, never exercised in tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class VoiceErrorClass(StrEnum):
    """Voice-relevant subset of the project error taxonomy."""

    # A real adapter exists but has no API key -> the real call path is inert.
    PROVIDER_AUTH_MISSING = "provider_auth_missing"
    # Provider reachable-but-failing, or transport error (retryable / fall back).
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    # No configured provider satisfies the requested capability.
    CAPABILITY_MISSING = "capability_missing"
    # The router tried every candidate and all of them failed.
    ALL_PROVIDERS_FAILED = "all_providers_failed"
    # Caller-supplied arguments are invalid (empty text, bad format, ...).
    VALIDATION_ERROR = "validation_error"
    # Provider exceeded its latency budget.
    TIMEOUT = "timeout"
    # Optional local dependency (e.g. faster-whisper) is not installed.
    OPTIONAL_DEPENDENCY_MISSING = "optional_dependency_missing"
    # Anything unexpected.
    INTERNAL_BUG = "internal_bug"
    # docs/DECISIONS.md ADR-0077: the call ran correctly and found nothing it could hand
    # the owner (a research whose report row has no body). Not a bug and not a
    # dependency: an honest empty result, spoken as such and never as a success.
    EMPTY_RESULT = "empty_result"


@dataclass(slots=True)
class VoiceError(Exception):
    """A voice-subsystem failure with a stable, typed classification."""

    error_class: VoiceErrorClass
    message: str
    provider: str | None = None
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # NB: zero-arg super() is unsafe under @dataclass(slots=True) (the
        # decorator recreates the class, breaking the __class__ cell), so call
        # the base initializer explicitly.
        Exception.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": str(self.error_class),
            "message": self.message,
            "provider": self.provider,
            "retryable": self.retryable,
            "details": self.details,
        }


# Which classes are safe to fall through to the next provider on. A missing key
# or a transient dependency failure is a fall-back candidate; a validation error
# is the caller's fault and must surface immediately.
FALLBACKABLE: frozenset[VoiceErrorClass] = frozenset(
    {
        VoiceErrorClass.PROVIDER_AUTH_MISSING,
        VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
        VoiceErrorClass.TIMEOUT,
        VoiceErrorClass.OPTIONAL_DEPENDENCY_MISSING,
    }
)


__all__ = ["FALLBACKABLE", "VoiceError", "VoiceErrorClass"]
