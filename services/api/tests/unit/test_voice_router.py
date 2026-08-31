"""Unit tests: capability-aware provider router with typed fall-back."""

import pytest

from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import FailingTTSProvider, FakeTTSProvider
from app.voice.registry import benchmark_stt_candidates
from app.voice.router import CapabilityRequest, STTRouter, TTSRouter

TR = "merhaba dünya"


def test_first_provider_success() -> None:
    router = TTSRouter([FakeTTSProvider("a"), FakeTTSProvider("b")])
    out = router.synthesize(TR)
    assert out.provider == "a"
    assert out.result.audio[:4] == b"RIFF"
    assert [a.ok for a in out.attempts] == [True]


def test_fallback_to_next_on_failure() -> None:
    router = TTSRouter([FailingTTSProvider("bad"), FakeTTSProvider("good")])
    out = router.synthesize(TR)
    assert out.provider == "good"
    assert out.attempts[0].provider == "bad" and out.attempts[0].ok is False
    assert out.attempts[1].provider == "good" and out.attempts[1].ok is True


def test_all_fail_raises_typed_error_with_reasons() -> None:
    router = TTSRouter([FailingTTSProvider("bad1"), FailingTTSProvider("bad2")])
    with pytest.raises(VoiceError) as exc:
        router.synthesize(TR)
    assert exc.value.error_class == VoiceErrorClass.ALL_PROVIDERS_FAILED
    attempts = exc.value.details["attempts"]
    assert {a["provider"] for a in attempts} == {"bad1", "bad2"}


def test_auth_missing_is_fallbackable() -> None:
    router = TTSRouter([
        FailingTTSProvider("noauth", error_class=VoiceErrorClass.PROVIDER_AUTH_MISSING),
        FakeTTSProvider("good"),
    ])
    out = router.synthesize(TR)
    assert out.provider == "good"


def test_validation_error_surfaces_immediately_no_fallback() -> None:
    """A caller (non-fallbackable) error must not be retried on the next provider."""
    router = TTSRouter([FakeTTSProvider("a"), FakeTTSProvider("b")])
    with pytest.raises(VoiceError) as exc:
        router.synthesize("   ")  # empty -> VALIDATION_ERROR from provider 'a'
    assert exc.value.error_class == VoiceErrorClass.VALIDATION_ERROR
    assert exc.value.provider == "a"


def test_capability_filter_skips_ineligible_then_errors() -> None:
    # Only Turkish-less providers -> CAPABILITY_MISSING for a Turkish request.
    router = STTRouter(benchmark_stt_candidates())
    with pytest.raises(VoiceError) as exc:
        router.transcribe(b"RIFF....WAVE", request=CapabilityRequest(language="ja-JP"))
    assert exc.value.error_class == VoiceErrorClass.CAPABILITY_MISSING


def test_capability_filter_requires_streaming() -> None:
    req = CapabilityRequest(require_pronunciation_dict=True)
    # FakeTTSProvider declares pronunciation_dict=True, so it is eligible.
    router = TTSRouter([FakeTTSProvider("a")])
    assert router.synthesize(TR, request=req).provider == "a"
