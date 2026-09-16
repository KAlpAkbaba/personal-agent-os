"""Capability-aware provider router with typed fall-back (routing §5).

Given a capability request (subsystem + language + optional feature flags), the
router walks an ordered list of candidate providers, returns the first success,
and on a *fall-backable* typed failure moves to the next candidate. If every
candidate fails it raises a single ``ALL_PROVIDERS_FAILED`` error carrying the
per-provider reasons. A non-fall-backable error (e.g. a caller ``VALIDATION_ERROR``)
surfaces immediately and is never retried against another provider.

This is where "provider failure falls back or reports gracefully" (ACCEPTANCE
M4) lives. It is provider-agnostic: fakes in tests, real adapters in production
plug into the same list.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.logging import get_logger
from app.voice.errors import FALLBACKABLE, VoiceError, VoiceErrorClass
from app.voice.providers import (
    ProviderCapabilities,
    STTProvider,
    STTResult,
    TTSProvider,
    TTSResult,
)

logger = get_logger("app.voice.router")


@dataclass(frozen=True, slots=True)
class CapabilityRequest:
    """What the caller needs. Providers that cannot satisfy hard requirements are
    skipped before any call is attempted (CAPABILITY_MISSING if none qualify)."""

    language: str = "tr-TR"
    require_streaming: bool = False
    require_pronunciation_dict: bool = False
    require_speed_control: bool = False
    require_voice_selection: bool = False

    def satisfied_by(self, caps: ProviderCapabilities) -> bool:
        if not caps.supports_language(self.language):
            return False
        if self.require_streaming and not caps.streaming:
            return False
        if self.require_pronunciation_dict and not caps.pronunciation_dict:
            return False
        if self.require_speed_control and not caps.speed_control:
            return False
        if self.require_voice_selection and not caps.voice_selection:
            return False
        return True


@dataclass(slots=True)
class ProviderAttempt:
    provider: str
    ok: bool
    error_class: str | None = None
    message: str | None = None


@dataclass(slots=True)
class RouteResult[R]:
    result: R
    provider: str
    attempts: list[ProviderAttempt] = field(default_factory=list)


class ProviderRouter[P]:
    """Ordered candidate list + capability filter + typed fall-back."""

    def __init__(self, providers: list[P], *, kind: str) -> None:
        if not providers:
            raise VoiceError(VoiceErrorClass.CAPABILITY_MISSING, f"no {kind} providers configured")
        self._providers = providers
        self._kind = kind

    def _eligible(self, request: CapabilityRequest) -> list[P]:
        eligible = [p for p in self._providers if request.satisfied_by(p.capabilities())]  # type: ignore[attr-defined]
        if not eligible:
            raise VoiceError(
                VoiceErrorClass.CAPABILITY_MISSING,
                f"no {self._kind} provider satisfies {request}",
                details={"candidates": [p.name for p in self._providers]},  # type: ignore[attr-defined]
            )
        return eligible

    def run[R](self, request: CapabilityRequest, call: Callable[[P], R]) -> RouteResult[R]:
        attempts: list[ProviderAttempt] = []
        for provider in self._eligible(request):
            name = provider.name  # type: ignore[attr-defined]
            try:
                result = call(provider)
            except VoiceError as exc:
                attempts.append(ProviderAttempt(name, False, str(exc.error_class), exc.message))
                logger.info(
                    "voice_provider_failed",
                    kind=self._kind,
                    provider=name,
                    error_class=str(exc.error_class),
                    fallbackable=exc.error_class in FALLBACKABLE,
                )
                if exc.error_class in FALLBACKABLE:
                    continue  # try the next candidate
                raise  # caller error / non-recoverable: surface immediately
            attempts.append(ProviderAttempt(name, True))
            logger.info("voice_provider_ok", kind=self._kind, provider=name, attempts=len(attempts))
            return RouteResult(result=result, provider=name, attempts=attempts)

        raise VoiceError(
            VoiceErrorClass.ALL_PROVIDERS_FAILED,
            f"all {self._kind} providers failed",
            details={
                "attempts": [
                    {"provider": a.provider, "error_class": a.error_class, "message": a.message}
                    for a in attempts
                ]
            },
        )


class TTSRouter(ProviderRouter[TTSProvider]):
    def __init__(self, providers: list[TTSProvider]) -> None:
        super().__init__(providers, kind="tts")

    def synthesize(
        self,
        text: str,
        *,
        request: CapabilityRequest | None = None,
        voice: str = "default",
        speed: float = 1.0,
        fmt: str = "wav",
    ) -> RouteResult[TTSResult]:
        req = request or CapabilityRequest()
        return self.run(req, lambda p: p.synthesize(text, voice=voice, speed=speed, fmt=fmt))


class STTRouter(ProviderRouter[STTProvider]):
    def __init__(self, providers: list[STTProvider]) -> None:
        super().__init__(providers, kind="stt")

    def transcribe(
        self,
        audio: bytes,
        *,
        request: CapabilityRequest | None = None,
        language: str = "tr-TR",
    ) -> RouteResult[STTResult]:
        req = request or CapabilityRequest(language=language)
        return self.run(req, lambda p: p.transcribe(audio, language=language))


__all__ = [
    "CapabilityRequest",
    "ProviderAttempt",
    "ProviderRouter",
    "RouteResult",
    "STTRouter",
    "TTSRouter",
]
