"""Voice provider seams (constitution Voice rule + provider independence).

STT, realtime dialogue and long-form TTS are *separate* subsystems and may each
use a different vendor. This module defines one Protocol per subsystem, a shared
capability declaration, deterministic offline **fakes**, and **real adapter
skeletons** for ElevenLabs / Azure Speech / OpenAI (+ Faster-Whisper local STT).

Testing discipline (deterministic + offline):

- The fakes never touch the network. ``FakeTTSProvider`` emits a valid RIFF/WAVE
  byte stream whose length is proportional to the input text and whose bytes are
  stable for the same input; ``FakeSTTProvider`` returns transcripts recovered
  from that synthetic audio, optionally perturbed by a fixed error profile so
  two fakes disagree and the benchmark can rank them.
- The real adapters split into a pure ``build_request()`` (unit-testable request
  construction, no I/O) and a lazily-imported ``_send()`` that uses httpx. With
  no API key configured the real call path raises ``PROVIDER_AUTH_MISSING`` and
  performs no I/O, so the vendor path exists but is inert in tests. Wiring a real
  key is an owner action (VOICE_SPEC §7/§8) and MUST NOT run in the test suite.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from app.voice.errors import VoiceError, VoiceErrorClass

# --------------------------------------------------------------- audio formats
# VOICE_SPEC §11. Realtime prefers WebRTC/Opus/PCM; narration cache prefers
# Opus/AAC. The deterministic fakes emit PCM WAV because it is header-verifiable
# with zero dependencies.
AUDIO_FORMATS = ("wav", "pcm16", "opus", "mp3", "aac")

_WAV_SAMPLE_RATE = 16000
_WAV_CHANNELS = 1
_WAV_BITS = 16


def synthesize_wav(text: str, *, seed: int = 0, sample_rate: int = _WAV_SAMPLE_RATE) -> bytes:
    """Deterministic synthetic WAV: valid RIFF/WAVE header + a low sine tone.

    Length is proportional to ``len(text)`` so downstream duration metadata is
    meaningful, and the payload also carries the source text (obfuscated) so the
    fake recognizer can recover it. Same ``(text, seed)`` -> identical bytes.
    """
    text_bytes = text.encode("utf-8")
    # ~40 ms of audio per character, floored so empty text still has a frame.
    n_samples = max(sample_rate // 25, (len(text_bytes) + 1) * (sample_rate // 25))
    key = (seed & 0xFF) or 0x5A
    frames = bytearray()
    freq = 110.0 + (seed % 7) * 20.0
    for i in range(n_samples):
        # Base tone plus a per-sample stamp of the (obfuscated) source text so
        # the fake STT can reconstruct it; both are deterministic.
        tone = math.sin(2.0 * math.pi * freq * (i / sample_rate))
        stamp = text_bytes[i % len(text_bytes)] ^ key if text_bytes else 0
        sample = int(tone * 8000) + (stamp - 128) * 4
        sample = max(-32768, min(32767, sample))
        frames += struct.pack("<h", sample)
    return _wrap_wav(bytes(frames), sample_rate)


def _wrap_wav(pcm: bytes, sample_rate: int) -> bytes:
    byte_rate = sample_rate * _WAV_CHANNELS * (_WAV_BITS // 8)
    block_align = _WAV_CHANNELS * (_WAV_BITS // 8)
    header = b"RIFF"
    header += struct.pack("<I", 36 + len(pcm))
    header += b"WAVE"
    header += b"fmt "
    header += struct.pack("<IHHIIHH", 16, 1, _WAV_CHANNELS, sample_rate, byte_rate,
                          block_align, _WAV_BITS)
    header += b"data"
    header += struct.pack("<I", len(pcm))
    return header + pcm


def wav_duration_ms(audio: bytes) -> int:
    """Duration of a PCM16 mono WAV in milliseconds (0 if it is not parseable)."""
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return 0
    data_size = struct.unpack("<I", audio[40:44])[0]
    n_samples = data_size // (_WAV_BITS // 8)
    return int(round(n_samples / _WAV_SAMPLE_RATE * 1000))


def is_wav(audio: bytes) -> bool:
    return len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"


# ------------------------------------------------------------ capability + I/O


# M12 realtime capability vocabulary (M12_REALTIME_VOICE_SPEC §2).
END_OF_TURN_SILENCE = "silence"
END_OF_TURN_SERVER_VAD = "server_vad"
END_OF_TURN_SEMANTIC = "semantic"
END_OF_TURN_MODES = (END_OF_TURN_SILENCE, END_OF_TURN_SERVER_VAD, END_OF_TURN_SEMANTIC)

TRANSPORT_WEBRTC = "webrtc"
TRANSPORT_WEBSOCKET = "websocket"
TRANSPORT_SIMULATED = "simulated"  # in-process, deterministic; the simulator only
TRANSPORTS = (TRANSPORT_WEBRTC, TRANSPORT_WEBSOCKET, TRANSPORT_SIMULATED)

INTERRUPT_LATENCY_FAST = "fast"  # provider cancels an in-flight response well under 150 ms
INTERRUPT_LATENCY_MEDIUM = "medium"
INTERRUPT_LATENCY_SLOW = "slow"
INTERRUPT_LATENCY_NA = "n/a"
INTERRUPT_LATENCY_CLASSES = (
    INTERRUPT_LATENCY_FAST, INTERRUPT_LATENCY_MEDIUM, INTERRUPT_LATENCY_SLOW, INTERRUPT_LATENCY_NA,
)


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """VOICE_SPEC §7 capability declaration, shared by all three subsystems."""

    name: str
    kind: str  # "tts" | "stt" | "realtime"
    languages: tuple[str, ...]
    streaming: bool
    long_form_stability: str  # "high" | "medium" | "low" | "n/a"
    pronunciation_dict: bool
    voice_selection: bool
    speed_control: bool
    cost_metadata: dict[str, Any]
    output_formats: tuple[str, ...]
    latency_class: str  # "realtime" | "low" | "batch"
    requires_api_key: bool

    # ---- M12 realtime vocabulary (M12_REALTIME_VOICE_SPEC §2). Every field
    # defaults to "not offered" so the M4 TTS/STT adapters and their tests are
    # untouched; a realtime adapter declares what it actually provides and the
    # pure selector (app/voice/selection.py) chooses by these, never by name.
    speech_to_speech: bool = False
    full_duplex: bool = False
    barge_in: bool = False
    end_of_turn: str = END_OF_TURN_SILENCE  # "silence" | "server_vad" | "semantic"
    tool_calling: bool = False
    transports: tuple[str, ...] = ()  # subset of TRANSPORTS
    ephemeral_credentials: bool = False
    input_formats: tuple[str, ...] = ()
    interrupt_latency_class: str = INTERRUPT_LATENCY_NA  # "fast" | "medium" | "slow" | "n/a"

    def __post_init__(self) -> None:
        if self.end_of_turn not in END_OF_TURN_MODES:
            raise ValueError(f"end_of_turn must be one of {END_OF_TURN_MODES}, "
                             f"got {self.end_of_turn!r}")
        if self.interrupt_latency_class not in INTERRUPT_LATENCY_CLASSES:
            raise ValueError(f"interrupt_latency_class must be one of "
                             f"{INTERRUPT_LATENCY_CLASSES}, got {self.interrupt_latency_class!r}")
        unknown = [t for t in self.transports if t not in TRANSPORTS]
        if unknown:
            raise ValueError(f"unknown transports {unknown}; known: {TRANSPORTS}")

    def supports_language(self, language: str) -> bool:
        base = language.split("-")[0].lower()
        return any(lang.split("-")[0].lower() == base for lang in self.languages)

    def is_conversation_capable(self, language: str = "tr-TR") -> bool:
        """The hard requirement for ``ConversationRealtime`` (spec §2):
        speech-to-speech AND full-duplex AND barge-in AND tool calling AND the
        language. Preferences (semantic end-of-turn, WebRTC) rank, not gate."""
        return (
            self.kind == "realtime"
            and self.speech_to_speech
            and self.full_duplex
            and self.barge_in
            and self.tool_calling
            and self.supports_language(language)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "languages": list(self.languages),
            "streaming": self.streaming,
            "long_form_stability": self.long_form_stability,
            "pronunciation_dict": self.pronunciation_dict,
            "voice_selection": self.voice_selection,
            "speed_control": self.speed_control,
            "cost_metadata": dict(self.cost_metadata),
            "output_formats": list(self.output_formats),
            "latency_class": self.latency_class,
            "requires_api_key": self.requires_api_key,
            "speech_to_speech": self.speech_to_speech,
            "full_duplex": self.full_duplex,
            "barge_in": self.barge_in,
            "end_of_turn": self.end_of_turn,
            "tool_calling": self.tool_calling,
            "transports": list(self.transports),
            "ephemeral_credentials": self.ephemeral_credentials,
            "input_formats": list(self.input_formats),
            "interrupt_latency_class": self.interrupt_latency_class,
        }


@dataclass(frozen=True, slots=True)
class TTSResult:
    audio: bytes
    audio_format: str
    duration_ms: int
    latency_ms: float
    provider: str
    voice: str
    char_count: int


@dataclass(frozen=True, slots=True)
class WordTiming:
    word: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class STTResult:
    text: str
    confidence: float
    word_timings: tuple[WordTiming, ...]
    latency_ms: float
    provider: str
    language: str


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """A fully-built HTTP request for a real adapter, produced without any I/O.

    Unit tests assert on this to prove each adapter targets the correct vendor
    endpoint / headers / body without ever opening a socket.
    """

    method: str
    url: str
    headers: dict[str, str]
    json_body: dict[str, Any] | None = None
    data: bytes | None = None
    query: dict[str, str] = field(default_factory=dict)


# ------------------------------------------------------------------- Protocols


@runtime_checkable
class TTSProvider(Protocol):
    name: str

    def capabilities(self) -> ProviderCapabilities: ...

    def synthesize(
        self, text: str, *, voice: str = "default", speed: float = 1.0, fmt: str = "wav"
    ) -> TTSResult: ...


@runtime_checkable
class STTProvider(Protocol):
    name: str

    def capabilities(self) -> ProviderCapabilities: ...

    def transcribe(self, audio: bytes, *, language: str = "tr-TR") -> STTResult: ...


# ---- M12 realtime session vocabulary (M12_REALTIME_VOICE_SPEC §1, §4).
#
# A realtime session handle is the client-side view of ONE provider media
# session: audio in, audio out, structured events, barge-in, tool-result
# submission. Cloud Core never holds the media leg in production (the direct
# media path is client <-> provider); the handle exists so the simulator and
# the unit tests can drive the exact same contract offline.

RT_SPEECH_STARTED = "speech_started"  # owner speech detected (server VAD / semantic)
RT_SPEECH_STOPPED = "speech_stopped"  # end of the owner's turn
RT_RESPONSE_STARTED = "response_started"
RT_RESPONSE_AUDIO = "response_audio"  # one audio frame; payload carries byte count only
RT_RESPONSE_DONE = "response_done"  # payload: {"cancelled": bool}
RT_TOOL_CALL = "tool_call"  # payload: {"call_id", "name", "arguments"}
RT_ERROR = "error"
RT_NETWORK_LOST = "network_lost"
RT_NETWORK_RESTORED = "network_restored"
REALTIME_EVENT_KINDS = (
    RT_SPEECH_STARTED, RT_SPEECH_STOPPED, RT_RESPONSE_STARTED, RT_RESPONSE_AUDIO,
    RT_RESPONSE_DONE, RT_TOOL_CALL, RT_ERROR, RT_NETWORK_LOST, RT_NETWORK_RESTORED,
)


@dataclass(frozen=True, slots=True)
class RealtimeSessionEvent:
    """A structured provider event with a session-relative timestamp in ms.

    ``at_ms`` is the session clock (monotonic, 0 at open) so latency can be
    computed by subtraction; the payload never carries audio bytes (frames are
    delivered to the audio sink, the event only records their size)."""

    kind: str
    at_ms: int
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "at_ms": self.at_ms, "payload": dict(self.payload)}


@dataclass(frozen=True, slots=True)
class EphemeralCredential:
    """A per-session, short-lived provider credential minted THROUGH the adapter.

    The owner-provisioned provider key (Settings) is never in this object; a
    client receives only ``secret`` and it is scoped to one media session.
    ``secret`` is never persisted and never logged/audited."""

    provider: str
    secret: str
    expires_at: datetime
    transport: str
    session_ref: str  # provider-side session identifier (opaque, non-secret)

    def to_client_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "secret": self.secret,
            "expires_at": self.expires_at.isoformat().replace("+00:00", "Z"),
            "transport": self.transport,
            "session_ref": self.session_ref,
        }


AudioSink = Callable[[bytes], None]
EventSink = Callable[[RealtimeSessionEvent], None]


@runtime_checkable
class RealtimeProvider(Protocol):
    name: str

    def capabilities(self) -> ProviderCapabilities: ...

    def open_session(self, *, language: str = "tr-TR") -> RealtimeSessionHandle: ...

    def mint_credential(
        self, *, session_id: str, ttl_s: int, transport: str
    ) -> EphemeralCredential: ...


@runtime_checkable
class RealtimeSessionHandle(Protocol):
    def push_audio(self, chunk: bytes) -> None: ...
    def on_audio(self, sink: AudioSink) -> None: ...
    def on_event(self, sink: EventSink) -> None: ...
    def request_barge_in(self) -> None: ...
    def submit_tool_result(self, call_id: str, result: dict[str, Any]) -> None: ...
    def close(self) -> None: ...


# ============================================================ deterministic fakes


def _tokenize(text: str) -> list[str]:
    return [t for t in text.replace("\n", " ").split(" ") if t]


class FakeTTSProvider:
    """Offline TTS fake: deterministic WAV, stable bytes, no network.

    ``seed`` shifts the tone so two fake providers produce distinguishable audio
    while each stays reproducible (used to prove the benchmark compares >= 2).
    """

    def __init__(self, name: str = "fake-tts", *, seed: int = 0,
                 stability: str = "high", latency_ms: float = 12.0) -> None:
        self.name = name
        self._seed = seed
        self._stability = stability
        self._latency_ms = latency_ms

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name,
            kind="tts",
            languages=("tr-TR", "en-US"),
            streaming=True,
            long_form_stability=self._stability,
            pronunciation_dict=True,
            voice_selection=True,
            speed_control=True,
            cost_metadata={"unit": "characters", "usd_per_1k": 0.0, "note": "fake/offline"},
            output_formats=("wav", "pcm16"),
            latency_class="low",
            requires_api_key=False,
        )

    def synthesize(
        self, text: str, *, voice: str = "default", speed: float = 1.0, fmt: str = "wav"
    ) -> TTSResult:
        if not text or not text.strip():
            raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "text must be non-empty",
                             provider=self.name)
        if fmt not in ("wav", "pcm16"):
            raise VoiceError(VoiceErrorClass.VALIDATION_ERROR,
                             f"fake provider only emits wav/pcm16, not {fmt!r}",
                             provider=self.name)
        if speed <= 0:
            raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "speed must be > 0",
                             provider=self.name)
        audio = synthesize_wav(text, seed=self._seed)
        duration = int(round(wav_duration_ms(audio) / speed))
        return TTSResult(
            audio=audio,
            audio_format="wav",
            duration_ms=duration,
            latency_ms=self._latency_ms,
            provider=self.name,
            voice=voice,
            char_count=len(text),
        )


class FailingTTSProvider:
    """A TTS fake that always fails with a fall-backable error (router tests)."""

    def __init__(self, name: str = "failing-tts",
                 error_class: VoiceErrorClass = VoiceErrorClass.DEPENDENCY_UNAVAILABLE) -> None:
        self.name = name
        self._error_class = error_class

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name, kind="tts", languages=("tr-TR",), streaming=False,
            long_form_stability="low", pronunciation_dict=False, voice_selection=False,
            speed_control=False, cost_metadata={}, output_formats=("wav",),
            latency_class="low", requires_api_key=False,
        )

    def synthesize(
        self, text: str, *, voice: str = "default", speed: float = 1.0, fmt: str = "wav"
    ) -> TTSResult:
        raise VoiceError(self._error_class, f"{self.name} is configured to fail",
                         provider=self.name, retryable=True)


class FakeSTTProvider:
    """Offline STT fake: recovers text from synthetic audio (see synthesize_wav).

    ``error_profile`` is a deterministic callable applied to the recovered token
    list so different fakes yield different WER/CER, which is what makes the STT
    benchmark a real comparison. Default profile is perfect recovery.
    """

    def __init__(self, name: str = "fake-stt", *, seed: int = 0,
                 error_profile: Any = None, confidence: float = 0.97,
                 latency_ms: float = 30.0) -> None:
        self.name = name
        self._seed = seed
        self._error_profile = error_profile
        self._confidence = confidence
        self._latency_ms = latency_ms

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name, kind="stt", languages=("tr-TR", "en-US"), streaming=True,
            long_form_stability="n/a", pronunciation_dict=False, voice_selection=False,
            speed_control=False,
            cost_metadata={"unit": "audio_seconds", "usd_per_min": 0.0, "note": "fake/offline"},
            output_formats=("text",), latency_class="low", requires_api_key=False,
        )

    def transcribe(self, audio: bytes, *, language: str = "tr-TR") -> STTResult:
        if not is_wav(audio):
            raise VoiceError(VoiceErrorClass.VALIDATION_ERROR,
                             "fake STT expects RIFF/WAVE audio", provider=self.name)
        text = _recover_text_from_wav(audio, seed=self._seed)
        tokens = _tokenize(text)
        if self._error_profile is not None:
            tokens = list(self._error_profile(tokens))
        recovered = " ".join(tokens)
        timings = _even_word_timings(tokens, wav_duration_ms(audio))
        return STTResult(
            text=recovered,
            confidence=self._confidence,
            word_timings=tuple(timings),
            latency_ms=self._latency_ms,
            provider=self.name,
            language=language,
        )


def _recover_text_from_wav(audio: bytes, *, seed: int = 0) -> str:
    """Inverse of synthesize_wav's text stamping (fakes only)."""
    if len(audio) < 44:
        return ""
    data_size = struct.unpack("<I", audio[40:44])[0]
    pcm = audio[44 : 44 + data_size]
    key = (seed & 0xFF) or 0x5A
    out = bytearray()
    for off in range(0, len(pcm) - 1, 2):
        sample = struct.unpack_from("<h", pcm, off)[0]
        tone = math.sin(
            2.0 * math.pi * (110.0 + (seed % 7) * 20.0) * ((off // 2) / _WAV_SAMPLE_RATE)
        )
        stamp = round((sample - int(tone * 8000)) / 4) + 128
        out.append((stamp ^ key) & 0xFF)
    # Strip repeated/padding tail: the source text was tiled; recover one period.
    try:
        recovered = bytes(out).decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""
    return _dedupe_tiled(recovered)


def _dedupe_tiled(recovered: str) -> str:
    """synthesize_wav tiles the source text across the frame; return one copy."""
    if not recovered:
        return ""
    for period in range(1, len(recovered) // 2 + 1):
        unit = recovered[:period]
        if unit * (len(recovered) // period) == recovered[: period * (len(recovered) // period)]:
            return unit.strip()
    return recovered.strip()


def _even_word_timings(tokens: list[str], total_ms: int) -> list[WordTiming]:
    if not tokens:
        return []
    step = max(1, total_ms // len(tokens))
    out = []
    for i, tok in enumerate(tokens):
        out.append(WordTiming(word=tok, start_ms=i * step, end_ms=(i + 1) * step))
    return out


# Ready-made deterministic error profiles for building distinguishable STT fakes.
def drop_last_word(tokens: list[str]) -> list[str]:
    return tokens[:-1] if len(tokens) > 1 else tokens


def lowercase_all(tokens: list[str]) -> list[str]:
    return [t.lower() for t in tokens]


class FakeRealtimeProvider:
    """Offline realtime fake. Produces a session handle backed by the barge-in
    state machine (see app/voice/realtime.py); no audio device, no network."""

    def __init__(self, name: str = "fake-realtime") -> None:
        self.name = name

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name, kind="realtime", languages=("tr-TR", "en-US"), streaming=True,
            long_form_stability="n/a", pronunciation_dict=False, voice_selection=True,
            speed_control=False,
            cost_metadata={"unit": "audio_minutes", "usd_per_min": 0.0, "note": "fake/offline"},
            output_formats=("pcm16", "opus"), latency_class="realtime", requires_api_key=False,
        )

    def open_session(self, *, language: str = "tr-TR"):  # noqa: ANN201 - see realtime.py
        from app.voice.realtime import RealtimeSession

        return RealtimeSession(provider=self.name, language=language)

    def mint_credential(
        self, *, session_id: str, ttl_s: int, transport: str = TRANSPORT_SIMULATED
    ) -> EphemeralCredential:
        """Control-only fake: a labelled, non-secret placeholder credential."""
        return EphemeralCredential(
            provider=self.name,
            secret=f"fake-realtime-credential:{session_id}",
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
            transport=transport,
            session_ref=f"fake:{session_id}",
        )


# ======================================================= real adapter skeletons
#
# Each real adapter reads its key from Settings (PAGENTOS_VOICE_* env). With no
# key the *real call path* raises PROVIDER_AUTH_MISSING and does NO I/O. The
# request builder is pure and unit-tested; _send() lazily imports httpx and is
# only reached once an owner has provisioned a key.


def _require_key(key: str | None, provider: str) -> str:
    if not key:
        raise VoiceError(
            VoiceErrorClass.PROVIDER_AUTH_MISSING,
            f"{provider}: no API key configured (owner action: set PAGENTOS_VOICE_* env)",
            provider=provider,
        )
    return key


def _send(req: ProviderRequest, *, timeout_s: float, provider: str) -> Any:
    """Lazily-imported real HTTP send. Never reached without a key; unit tests
    mock the transport so no real network call happens."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx present in dev group
        raise VoiceError(VoiceErrorClass.OPTIONAL_DEPENDENCY_MISSING,
                         "httpx is required for real provider calls",
                         provider=provider) from exc
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.request(
                req.method, req.url, headers=req.headers, params=req.query or None,
                json=req.json_body, content=req.data,
            )
            resp.raise_for_status()
            return resp
    except httpx.TimeoutException as exc:
        raise VoiceError(VoiceErrorClass.TIMEOUT, f"{provider}: request timed out",
                         provider=provider, retryable=True) from exc
    except httpx.HTTPError as exc:
        raise VoiceError(VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
                         f"{provider}: {type(exc).__name__}: {exc}",
                         provider=provider, retryable=True) from exc


class ElevenLabsTTSProvider:
    """ElevenLabs Multilingual v2 / v3 long-form Turkish TTS (VOICE_SPEC §7)."""

    name = "elevenlabs"
    _BASE = "https://api.elevenlabs.io/v1"

    def __init__(self, api_key: str | None = None, *, model_id: str = "eleven_multilingual_v2",
                 default_voice: str = "Rachel", timeout_s: float = 30.0) -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._default_voice = default_voice
        self._timeout_s = timeout_s

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name, kind="tts", languages=("tr-TR", "en-US", "de-DE"), streaming=True,
            long_form_stability="high", pronunciation_dict=True, voice_selection=True,
            speed_control=True,
            cost_metadata={"unit": "characters", "usd_per_1k": 0.30, "billing": "subscription"},
            output_formats=("mp3", "pcm16", "opus"), latency_class="low", requires_api_key=True,
        )

    def build_request(self, text: str, *, voice: str, speed: float, fmt: str) -> ProviderRequest:
        voice_id = voice if voice != "default" else self._default_voice
        return ProviderRequest(
            method="POST",
            url=f"{self._BASE}/text-to-speech/{voice_id}",
            headers={"xi-api-key": self._api_key or "", "accept": "audio/mpeg",
                     "content-type": "application/json"},
            json_body={
                "text": text,
                "model_id": self._model_id,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": speed},
            },
            query={"output_format": "mp3_44100_128" if fmt == "mp3" else "pcm_16000"},
        )

    def synthesize(
        self, text: str, *, voice: str = "default", speed: float = 1.0, fmt: str = "mp3"
    ) -> TTSResult:
        import time

        _require_key(self._api_key, self.name)  # inert without a key
        req = self.build_request(text, voice=voice, speed=speed, fmt=fmt)
        started = time.perf_counter()
        resp = _send(req, timeout_s=self._timeout_s, provider=self.name)
        audio = resp.content
        return TTSResult(
            audio=audio, audio_format=fmt, duration_ms=0,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            provider=self.name, voice=voice, char_count=len(text),
        )


class AzureTTSProvider:
    """Azure Speech neural Turkish TTS (VOICE_SPEC §7)."""

    name = "azure"

    def __init__(self, api_key: str | None = None, *, region: str = "westeurope",
                 default_voice: str = "tr-TR-EmelNeural", timeout_s: float = 30.0) -> None:
        self._api_key = api_key
        self._region = region
        self._default_voice = default_voice
        self._timeout_s = timeout_s

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name, kind="tts", languages=("tr-TR", "en-US"), streaming=True,
            long_form_stability="high", pronunciation_dict=True, voice_selection=True,
            speed_control=True,
            cost_metadata={"unit": "characters", "usd_per_1m": 15.0, "billing": "pay-as-you-go"},
            output_formats=("mp3", "pcm16", "opus"), latency_class="low", requires_api_key=True,
        )

    def build_request(self, text: str, *, voice: str, speed: float, fmt: str) -> ProviderRequest:
        voice_name = voice if voice != "default" else self._default_voice
        # SSML with a Turkish locale and a prosody rate derived from `speed`.
        rate = f"{int((speed - 1.0) * 100):+d}%"
        ssml = (
            f"<speak version='1.0' xml:lang='tr-TR'>"
            f"<voice name='{voice_name}'><prosody rate='{rate}'>{text}</prosody></voice></speak>"
        )
        out_fmt = "audio-16khz-32kbitrate-mono-mp3" if fmt == "mp3" else "riff-16khz-16bit-mono-pcm"
        return ProviderRequest(
            method="POST",
            url=f"https://{self._region}.tts.speech.microsoft.com/cognitiveservices/v1",
            headers={
                "Ocp-Apim-Subscription-Key": self._api_key or "",
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": out_fmt,
            },
            data=ssml.encode("utf-8"),
        )

    def synthesize(
        self, text: str, *, voice: str = "default", speed: float = 1.0, fmt: str = "mp3"
    ) -> TTSResult:
        import time

        _require_key(self._api_key, self.name)
        req = self.build_request(text, voice=voice, speed=speed, fmt=fmt)
        started = time.perf_counter()
        resp = _send(req, timeout_s=self._timeout_s, provider=self.name)
        return TTSResult(
            audio=resp.content, audio_format=fmt, duration_ms=0,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            provider=self.name, voice=voice, char_count=len(text),
        )


class OpenAITTSProvider:
    """OpenAI TTS endpoint for narration (VOICE_SPEC §7)."""

    name = "openai"
    _URL = "https://api.openai.com/v1/audio/speech"

    def __init__(self, api_key: str | None = None, *, model: str = "tts-1",
                 default_voice: str = "alloy", timeout_s: float = 30.0) -> None:
        self._api_key = api_key
        self._model = model
        self._default_voice = default_voice
        self._timeout_s = timeout_s

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name, kind="tts", languages=("tr-TR", "en-US"), streaming=True,
            long_form_stability="medium", pronunciation_dict=False, voice_selection=True,
            speed_control=True,
            cost_metadata={"unit": "characters", "usd_per_1m": 15.0, "billing": "pay-as-you-go"},
            output_formats=("mp3", "opus", "aac", "wav"), latency_class="low",
            requires_api_key=True,
        )

    def build_request(self, text: str, *, voice: str, speed: float, fmt: str) -> ProviderRequest:
        return ProviderRequest(
            method="POST", url=self._URL,
            headers={"Authorization": f"Bearer {self._api_key or ''}",
                     "Content-Type": "application/json"},
            json_body={
                "model": self._model,
                "input": text,
                "voice": voice if voice != "default" else self._default_voice,
                "response_format": fmt if fmt in ("mp3", "opus", "aac", "wav") else "mp3",
                "speed": speed,
            },
        )

    def synthesize(
        self, text: str, *, voice: str = "default", speed: float = 1.0, fmt: str = "mp3"
    ) -> TTSResult:
        import time

        _require_key(self._api_key, self.name)
        req = self.build_request(text, voice=voice, speed=speed, fmt=fmt)
        started = time.perf_counter()
        resp = _send(req, timeout_s=self._timeout_s, provider=self.name)
        return TTSResult(
            audio=resp.content, audio_format=fmt, duration_ms=0,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            provider=self.name, voice=voice, char_count=len(text),
        )


class OpenAISTTProvider:
    """OpenAI Whisper transcription endpoint (VOICE_SPEC §9)."""

    name = "openai-whisper"
    _URL = "https://api.openai.com/v1/audio/transcriptions"

    def __init__(self, api_key: str | None = None, *, model: str = "whisper-1",
                 timeout_s: float = 60.0) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name, kind="stt", languages=("tr-TR", "en-US"), streaming=False,
            long_form_stability="n/a", pronunciation_dict=False, voice_selection=False,
            speed_control=False,
            cost_metadata={"unit": "audio_minutes", "usd_per_min": 0.006, "billing": "usage"},
            output_formats=("text",), latency_class="batch", requires_api_key=True,
        )

    def build_request(self, audio: bytes, *, language: str) -> ProviderRequest:
        # multipart is assembled by httpx at send time from `data`/files; here we
        # carry the raw audio and let _send wrap it. We model the fields in query
        # for unit-test visibility of the target + model + language.
        return ProviderRequest(
            method="POST", url=self._URL,
            headers={"Authorization": f"Bearer {self._api_key or ''}"},
            data=audio,
            query={"model": self._model, "language": language.split("-")[0]},
        )

    def transcribe(self, audio: bytes, *, language: str = "tr-TR") -> STTResult:
        import time

        _require_key(self._api_key, self.name)
        req = self.build_request(audio, language=language)
        started = time.perf_counter()
        resp = _send(req, timeout_s=self._timeout_s, provider=self.name)
        payload = resp.json()
        return STTResult(
            text=payload.get("text", ""), confidence=payload.get("confidence", 1.0),
            word_timings=(), latency_ms=round((time.perf_counter() - started) * 1000, 2),
            provider=self.name, language=language,
        )


class AzureSTTProvider:
    """Azure Speech-to-Text short-audio REST endpoint (VOICE_SPEC §9)."""

    name = "azure-stt"

    def __init__(self, api_key: str | None = None, *, region: str = "westeurope",
                 timeout_s: float = 60.0) -> None:
        self._api_key = api_key
        self._region = region
        self._timeout_s = timeout_s

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name, kind="stt", languages=("tr-TR", "en-US"), streaming=True,
            long_form_stability="n/a", pronunciation_dict=True, voice_selection=False,
            speed_control=False,
            cost_metadata={"unit": "audio_hours", "usd_per_hour": 1.0, "billing": "usage"},
            output_formats=("text",), latency_class="low", requires_api_key=True,
        )

    def build_request(self, audio: bytes, *, language: str) -> ProviderRequest:
        return ProviderRequest(
            method="POST",
            url=(f"https://{self._region}.stt.speech.microsoft.com"
                 "/speech/recognition/conversation/cognitiveservices/v1"),
            headers={"Ocp-Apim-Subscription-Key": self._api_key or "",
                     "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000"},
            data=audio,
            query={"language": language, "format": "detailed"},
        )

    def transcribe(self, audio: bytes, *, language: str = "tr-TR") -> STTResult:
        import time

        _require_key(self._api_key, self.name)
        req = self.build_request(audio, language=language)
        started = time.perf_counter()
        resp = _send(req, timeout_s=self._timeout_s, provider=self.name)
        payload = resp.json()
        best = (payload.get("NBest") or [{}])[0]
        return STTResult(
            text=payload.get("DisplayText", best.get("Display", "")),
            confidence=best.get("Confidence", 1.0), word_timings=(),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            provider=self.name, language=language,
        )


class FasterWhisperSTTProvider:
    """Local STT fallback (VOICE_SPEC §9 / routing §4 stt_local_fallback).

    faster-whisper is an OPTIONAL, heavy local dependency (CTranslate2). It is
    import-guarded: absent, the provider is inert and raises
    OPTIONAL_DEPENDENCY_MISSING rather than being installed into the test env.
    """

    name = "faster-whisper"

    def __init__(self, *, model_size: str = "small", device: str = "cpu",
                 compute_type: str = "int8") -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model = None

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name, kind="stt", languages=("tr-TR", "en-US"), streaming=False,
            long_form_stability="n/a", pronunciation_dict=False, voice_selection=False,
            speed_control=False,
            cost_metadata={"unit": "local_compute", "usd": 0.0, "billing": "local/offline"},
            output_formats=("text",), latency_class="batch", requires_api_key=False,
        )

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise VoiceError(
                VoiceErrorClass.OPTIONAL_DEPENDENCY_MISSING,
                "faster-whisper is not installed (optional local STT fallback)",
                provider=self.name,
            ) from exc
        self._model = WhisperModel(self._model_size, device=self._device,
                                   compute_type=self._compute_type)
        return self._model

    def transcribe(self, audio: bytes, *, language: str = "tr-TR") -> STTResult:  # pragma: no cover
        import io
        import time

        model = self._load()  # raises OPTIONAL_DEPENDENCY_MISSING if absent
        started = time.perf_counter()
        segments, _info = model.transcribe(io.BytesIO(audio), language=language.split("-")[0])
        text = " ".join(seg.text.strip() for seg in segments)
        return STTResult(
            text=text.strip(), confidence=1.0, word_timings=(),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            provider=self.name, language=language,
        )


__all__ = [
    "AUDIO_FORMATS",
    "END_OF_TURN_MODES",
    "END_OF_TURN_SEMANTIC",
    "END_OF_TURN_SERVER_VAD",
    "END_OF_TURN_SILENCE",
    "INTERRUPT_LATENCY_CLASSES",
    "INTERRUPT_LATENCY_FAST",
    "INTERRUPT_LATENCY_MEDIUM",
    "INTERRUPT_LATENCY_NA",
    "INTERRUPT_LATENCY_SLOW",
    "REALTIME_EVENT_KINDS",
    "TRANSPORTS",
    "TRANSPORT_SIMULATED",
    "TRANSPORT_WEBRTC",
    "TRANSPORT_WEBSOCKET",
    "AudioSink",
    "AzureSTTProvider",
    "AzureTTSProvider",
    "ElevenLabsTTSProvider",
    "EphemeralCredential",
    "EventSink",
    "FailingTTSProvider",
    "FakeRealtimeProvider",
    "FakeSTTProvider",
    "FakeTTSProvider",
    "FasterWhisperSTTProvider",
    "OpenAISTTProvider",
    "OpenAITTSProvider",
    "ProviderCapabilities",
    "ProviderRequest",
    "RealtimeProvider",
    "RealtimeSessionEvent",
    "RealtimeSessionHandle",
    "STTProvider",
    "STTResult",
    "TTSProvider",
    "TTSResult",
    "WordTiming",
    "drop_last_word",
    "is_wav",
    "lowercase_all",
    "synthesize_wav",
    "wav_duration_ms",
]
