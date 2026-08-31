"""Unit tests: voice provider Protocols, deterministic fakes, and real adapters
that build correct requests under mocked HTTP and stay inert without keys."""

import httpx
import pytest

from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import (
    AzureSTTProvider,
    AzureTTSProvider,
    ElevenLabsTTSProvider,
    FakeSTTProvider,
    FakeTTSProvider,
    FasterWhisperSTTProvider,
    OpenAISTTProvider,
    OpenAITTSProvider,
    STTProvider,
    TTSProvider,
    is_wav,
    synthesize_wav,
    wav_duration_ms,
)

TR = "merhaba nasılsın bugün hava güzel"


# ----------------------------------------------------------- capability + fakes


def test_fakes_satisfy_protocols() -> None:
    assert isinstance(FakeTTSProvider(), TTSProvider)
    assert isinstance(FakeSTTProvider(), STTProvider)


def test_capability_declaration_shape() -> None:
    caps = FakeTTSProvider().capabilities()
    d = caps.to_dict()
    for field in ("languages", "streaming", "long_form_stability", "pronunciation_dict",
                  "voice_selection", "speed_control", "cost_metadata", "output_formats",
                  "latency_class"):
        assert field in d
    assert caps.supports_language("tr-TR")
    assert caps.supports_language("tr")


def test_fake_tts_emits_valid_wav_magic_deterministically() -> None:
    a = FakeTTSProvider().synthesize(TR)
    b = FakeTTSProvider().synthesize(TR)
    assert a.audio[:4] == b"RIFF" and a.audio[8:12] == b"WAVE"
    assert is_wav(a.audio)
    assert a.audio == b.audio  # stable bytes
    assert a.duration_ms > 0


def test_fake_tts_length_scales_with_text() -> None:
    short = FakeTTSProvider().synthesize("kısa")
    long = FakeTTSProvider().synthesize("çok daha uzun bir cümle daha fazla ses üretir")
    assert wav_duration_ms(long.audio) > wav_duration_ms(short.audio)


def test_fake_tts_rejects_empty_and_bad_format() -> None:
    with pytest.raises(VoiceError) as e1:
        FakeTTSProvider().synthesize("   ")
    assert e1.value.error_class == VoiceErrorClass.VALIDATION_ERROR
    with pytest.raises(VoiceError):
        FakeTTSProvider().synthesize(TR, fmt="mp3")


def test_fake_stt_roundtrips_synthesized_audio() -> None:
    audio = synthesize_wav(TR)
    res = FakeSTTProvider().transcribe(audio)
    assert res.text == TR
    assert 0.0 <= res.confidence <= 1.0
    assert res.word_timings and res.word_timings[0].word == "merhaba"


def test_fake_stt_error_profile_changes_output() -> None:
    from app.voice.providers import drop_last_word

    audio = synthesize_wav(TR)
    lossy = FakeSTTProvider("lossy", error_profile=drop_last_word).transcribe(audio)
    assert lossy.text != TR
    assert lossy.text == " ".join(TR.split()[:-1])


def test_fake_stt_rejects_non_wav() -> None:
    with pytest.raises(VoiceError):
        FakeSTTProvider().transcribe(b"not audio")


# --------------------------------------------------- real adapters: build_request


def test_elevenlabs_build_request_targets_vendor() -> None:
    p = ElevenLabsTTSProvider("SECRET_KEY", default_voice="Rachel")
    req = p.build_request("selam", voice="default", speed=1.0, fmt="mp3")
    assert req.method == "POST"
    assert req.url.endswith("/text-to-speech/Rachel")
    assert req.headers["xi-api-key"] == "SECRET_KEY"
    assert req.json_body["text"] == "selam"


def test_azure_build_request_is_ssml_with_turkish_locale() -> None:
    p = AzureTTSProvider("K", region="westeurope")
    req = p.build_request("merhaba", voice="default", speed=1.2, fmt="mp3")
    assert "westeurope.tts.speech.microsoft.com" in req.url
    assert req.headers["Ocp-Apim-Subscription-Key"] == "K"
    assert b"xml:lang='tr-TR'" in req.data
    assert b"tr-TR-EmelNeural" in req.data


def test_openai_tts_build_request() -> None:
    p = OpenAITTSProvider("K")
    req = p.build_request("selam", voice="alloy", speed=1.0, fmt="mp3")
    assert req.url == "https://api.openai.com/v1/audio/speech"
    assert req.headers["Authorization"] == "Bearer K"
    assert req.json_body["input"] == "selam"


def test_openai_stt_and_azure_stt_build_request() -> None:
    o = OpenAISTTProvider("K").build_request(synthesize_wav("x"), language="tr-TR")
    assert o.query["language"] == "tr"
    assert o.headers["Authorization"] == "Bearer K"
    a = AzureSTTProvider("K", region="westeurope").build_request(
        synthesize_wav("x"), language="tr-TR"
    )
    assert "stt.speech.microsoft.com" in a.url
    assert a.query["language"] == "tr-TR"


# ----------------------------------------- real adapters: inert without a key


@pytest.mark.parametrize("provider", [
    ElevenLabsTTSProvider(None),
    AzureTTSProvider(None),
    OpenAITTSProvider(None),
    OpenAISTTProvider(None),
    AzureSTTProvider(None),
])
def test_real_adapter_is_inert_without_key(provider) -> None:
    """No key -> PROVIDER_AUTH_MISSING and NO network call is attempted."""
    with pytest.raises(VoiceError) as exc:
        if hasattr(provider, "synthesize"):
            provider.synthesize(TR)
        else:
            provider.transcribe(synthesize_wav(TR))
    assert exc.value.error_class == VoiceErrorClass.PROVIDER_AUTH_MISSING


def test_faster_whisper_optional_dependency_guarded() -> None:
    """Optional local dep absent -> typed OPTIONAL_DEPENDENCY_MISSING, not ImportError."""
    p = FasterWhisperSTTProvider()
    caps = p.capabilities()
    assert caps.requires_api_key is False
    with pytest.raises(VoiceError) as exc:
        p.transcribe(synthesize_wav(TR))
    assert exc.value.error_class == VoiceErrorClass.OPTIONAL_DEPENDENCY_MISSING


# --------------------------- real adapters: mocked HTTP (no real network)


def test_openai_tts_with_mocked_http_no_real_call() -> None:
    """The real call path works against a mocked transport: proves the request is
    well-formed AND that a real call never leaves the test process."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, content=b"FAKE_AUDIO_BYTES")

    transport = httpx.MockTransport(handler)
    p = OpenAITTSProvider("SECRET")

    # Patch httpx.Client to use the mock transport for this adapter's _send.
    import app.voice.providers as providers_mod

    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    orig = providers_mod.httpx if hasattr(providers_mod, "httpx") else None
    # _send imports httpx lazily; monkeypatch the module attribute it will import.
    import httpx as httpx_mod

    saved = httpx_mod.Client
    httpx_mod.Client = client_factory  # type: ignore[assignment]
    try:
        res = p.synthesize("merhaba", fmt="mp3")
    finally:
        httpx_mod.Client = saved  # type: ignore[assignment]
    _ = orig
    assert res.audio == b"FAKE_AUDIO_BYTES"
    assert res.provider == "openai"
    assert captured["url"] == "https://api.openai.com/v1/audio/speech"
    assert captured["auth"] == "Bearer SECRET"
