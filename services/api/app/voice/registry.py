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
        AzureTTSProvider(settings.voice_azure_speech_key or None,
                         region=settings.voice_azure_speech_region),
        OpenAITTSProvider(settings.voice_openai_api_key or None),
    ]


def real_stt_providers(settings: Settings) -> list:
    return [
        OpenAISTTProvider(settings.voice_openai_api_key or None),
        AzureSTTProvider(settings.voice_azure_speech_key or None,
                         region=settings.voice_azure_speech_region),
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
        # Activated iff it does not require a key, or a key/region is configured.
        activated = not caps["requires_api_key"] or _is_activated(p, settings)
        caps["activated"] = activated
        out.append(caps)
    return out


def _is_activated(provider, settings: Settings) -> bool:  # noqa: ANN001
    name = provider.name
    if name == "elevenlabs":
        return bool(settings.voice_elevenlabs_api_key)
    if name in ("azure", "azure-stt"):
        return bool(settings.voice_azure_speech_key)
    if name in ("openai", "openai-whisper"):
        return bool(settings.voice_openai_api_key)
    if name == "faster-whisper":
        return False  # optional local dep, not installed by default
    return False


__all__ = [
    "all_provider_capabilities",
    "benchmark_stt_candidates",
    "benchmark_tts_candidates",
    "real_stt_providers",
    "real_tts_providers",
]
