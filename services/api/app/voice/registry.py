"""Provider registry: assemble the provider lists used by the router, the
benchmark harness and the REST surface.

Fakes are always present (deterministic/offline). Real adapters are constructed
from Settings; without keys they remain inert (PROVIDER_AUTH_MISSING on call),
so they can be *listed* with their capabilities but never fire in tests. The
benchmark uses two distinguishable fake candidates so it always compares >= 2
providers offline.
"""

from __future__ import annotations

from app.config import Settings
from app.voice.providers import (
    AzureSTTProvider,
    AzureTTSProvider,
    ElevenLabsTTSProvider,
    FakeSTTProvider,
    FakeTTSProvider,
    FasterWhisperSTTProvider,
    OpenAISTTProvider,
    OpenAITTSProvider,
    drop_last_word,
    lowercase_all,
)


def real_tts_providers(settings: Settings) -> list:
    return [
        ElevenLabsTTSProvider(settings.voice_elevenlabs_api_key or None),
        AzureTTSProvider(
            settings.voice_azure_speech_key or None, region=settings.voice_azure_speech_region
        ),
        OpenAITTSProvider(settings.voice_openai_api_key or None),
    ]


def real_stt_providers(settings: Settings) -> list:
    return [
        OpenAISTTProvider(settings.voice_openai_api_key or None),
        AzureSTTProvider(
            settings.voice_azure_speech_key or None, region=settings.voice_azure_speech_region
        ),
        FasterWhisperSTTProvider(),
    ]


def benchmark_tts_candidates() -> list:
    """Two deterministic, distinguishable fake TTS providers (>= 2 for the gate)."""
    return [
        FakeTTSProvider("fake-tts-a", seed=1, stability="high", latency_ms=11.0),
        FakeTTSProvider("fake-tts-b", seed=2, stability="medium", latency_ms=18.0),
    ]


def benchmark_stt_candidates() -> list:
    """Two fake STT providers with different error profiles so WER differs."""
    return [
        FakeSTTProvider("fake-stt-accurate", confidence=0.98),
        FakeSTTProvider("fake-stt-lossy", error_profile=drop_last_word, confidence=0.80),
        FakeSTTProvider("fake-stt-caselossy", error_profile=lowercase_all, confidence=0.90),
    ]


def all_provider_capabilities(settings: Settings) -> list[dict]:
    """Capability declarations for every known provider (fakes + reals) with an
    'activated' flag reflecting whether a real key is configured."""
    fakes = [
        FakeTTSProvider("fake-tts"),
        FakeSTTProvider("fake-stt"),
    ]
    out: list[dict] = []
    for p in fakes:
        caps = p.capabilities().to_dict()
        caps["activated"] = True
        out.append(caps)
    for p in real_tts_providers(settings) + real_stt_providers(settings):
        caps = p.capabilities().to_dict()
        caps["activated"] = _activated(p, caps, settings)
        out.append(caps)
    return out


def _activated(provider, caps: dict, settings: Settings) -> bool:  # noqa: ANN001
    """Whether this provider can actually be USED right now.

    B20 req 236. This was ``not caps["requires_api_key"] or _is_activated(...)`` and
    Python short-circuits ``or``: faster-whisper declares ``requires_api_key=False``, so
    the left half was True and `_is_activated` - whose whole faster-whisper branch exists
    to answer False - was never called. `GET /v1/voice/providers` therefore reported the
    local STT fallback as ACTIVATED on a machine where it is not installed and every call
    raises OPTIONAL_DEPENDENCY_MISSING. A diagnostic surface that lists a dead provider as
    live is worse than one that lists nothing: it is the surface the owner would consult
    to find out why transcription failed.

    A provider that needs no key is activated iff whatever it DOES need is there, and the
    provider is the thing that knows what that is.
    """
    if caps["requires_api_key"]:
        return _is_activated(provider, settings)
    available = getattr(provider, "available", None)
    if available is None:
        return True
    try:
        return bool(available())
    except Exception:  # noqa: BLE001 - an availability probe must not break the listing
        return False


def _is_activated(provider, settings: Settings) -> bool:  # noqa: ANN001
    name = provider.name
    if name == "elevenlabs":
        return bool(settings.voice_elevenlabs_api_key)
    if name in ("azure", "azure-stt"):
        return bool(settings.voice_azure_speech_key)
    if name in ("openai", "openai-whisper"):
        return bool(settings.voice_openai_api_key)
    if name == "faster-whisper":
        # Unreachable from `all_provider_capabilities` since B20 (it declares no key
        # requirement, so `_activated` asks the provider itself) and kept for any caller
        # that asks about key activation directly: a local dependency has no key, ever.
        return False
    return False


__all__ = [
    "all_provider_capabilities",
    "benchmark_stt_candidates",
    "benchmark_tts_candidates",
    "real_stt_providers",
    "real_tts_providers",
]
