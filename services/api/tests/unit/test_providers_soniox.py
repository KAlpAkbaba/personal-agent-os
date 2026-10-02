"""Unit tests: the Soniox real-time STT adapter, against a fake WebSocket server only.

No Soniox account exists and none is opened here (a separate owner approval); the server
below speaks the wire format the integration plan read on 2026-10-01 and records every
frame it is sent, in order.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from websockets.sync.server import ServerConnection, serve

from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import synthesize_wav
from app.voice.providers_soniox import SONIOX_URL_EU, SONIOX_URL_US, SonioxSTTProvider

KEY = "soniox-test-key-0123456789"
SENTENCE = "Ofis bilgisayarımdan hesap makinesini aç"


def _token(text: str, *, final: bool, confidence: float = 0.9) -> dict[str, Any]:
    return {"text": text, "is_final": final, "confidence": confidence}


#: Sub-word tokens that carry their own spaces, a non-final guess ("Ofi", "ında") that a
#: later final token replaces: a " ".join or a "count the non-final too" reading is wrong.
TRANSCRIPT_RESPONSES: tuple[dict[str, Any], ...] = (
    {"tokens": [_token("Ofi", final=False)]},
    {
        "tokens": [
            _token("Ofis", final=True, confidence=0.8),
            _token(" bilgisayar", final=True, confidence=1.0),
            _token("ında", final=False),
        ]
    },
    {
        "tokens": [
            _token("ımdan", final=True),
            _token(" hesap", final=True),
            _token(" makinesini", final=True),
            _token(" aç", final=True),
        ]
    },
    {"tokens": [], "finished": True},
)


@dataclass
class FakeSoniox:
    url: str
    connections: int = 0
    paths: list[str] = field(default_factory=list)
    frames: list[str | bytes] = field(default_factory=list)


@pytest.fixture
def soniox_server() -> Iterator[Callable[..., FakeSoniox]]:
    """Start a fake server; ``after_audio(ws)`` is what it does once the stream ended."""
    stoppers: list[Callable[[], None]] = []

    def start(after_audio: Callable[[ServerConnection], None] | None = None) -> FakeSoniox:
        state = FakeSoniox(url="")

        def respond(ws: ServerConnection) -> None:
            for response in TRANSCRIPT_RESPONSES:
                ws.send(json.dumps(response))

        def handler(ws: ServerConnection) -> None:
            state.connections += 1
            state.paths.append(ws.request.path if ws.request else "")
            for frame in ws:
                state.frames.append(frame)
                if frame == b"" or frame == "":
                    break
            (after_audio or respond)(ws)

        server = serve(handler, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.socket.getsockname()[1]
        state.url = f"ws://127.0.0.1:{port}/transcribe-websocket"

        def stop() -> None:
            server.shutdown()
            thread.join(timeout=5)

        stoppers.append(stop)
        return state

    yield start
    for stop in stoppers:
        stop()


def test_transcript_is_the_final_tokens_joined_without_a_separator(
    soniox_server: Callable[..., FakeSoniox],
) -> None:
    server = soniox_server()
    audio = synthesize_wav(SENTENCE)
    result = SonioxSTTProvider(KEY, url=server.url).transcribe(audio, language="tr-TR")
    assert result.text == SENTENCE
    assert result.provider == "soniox"
    assert result.language == "tr-TR"
    # mean of the six final tokens' confidence: (0.8 + 1.0 + 4 * 0.9) / 6
    assert result.confidence == pytest.approx(0.9)


def test_wire_order_is_config_then_audio_then_an_empty_frame(
    soniox_server: Callable[..., FakeSoniox],
) -> None:
    server = soniox_server()
    audio = synthesize_wav(SENTENCE)
    assert len(audio) > 3 * 4000
    SonioxSTTProvider(KEY, url=server.url, chunk_bytes=4000).transcribe(audio)

    assert server.connections == 1
    # the key travels in the first frame and never in the URL
    assert server.paths == ["/transcribe-websocket"]
    config = server.frames[0]
    assert isinstance(config, str)
    assert json.loads(config) == {
        "api_key": KEY,
        "model": "stt-rt-v5",
        "audio_format": "auto",
        "language_hints": ["tr"],
    }
    chunks = server.frames[1:-1]
    assert len(chunks) > 3 and all(isinstance(c, bytes) and 0 < len(c) <= 4000 for c in chunks)
    assert b"".join(c for c in chunks if isinstance(c, bytes)) == audio
    assert server.frames[-1] == b""


def test_without_a_key_it_refuses_and_never_connects(
    soniox_server: Callable[..., FakeSoniox],
) -> None:
    server = soniox_server()
    for missing in (None, "", "   "):
        with pytest.raises(VoiceError) as caught:
            SonioxSTTProvider(missing, url=server.url).transcribe(synthesize_wav(SENTENCE))
        assert caught.value.error_class == VoiceErrorClass.PROVIDER_AUTH_MISSING
    assert server.connections == 0 and server.frames == []


def test_with_no_audio_chunk_nothing_is_sent_not_even_the_key(
    soniox_server: Callable[..., FakeSoniox],
) -> None:
    server = soniox_server()
    with pytest.raises(VoiceError) as caught:
        SonioxSTTProvider(KEY, url=server.url).transcribe(b"")
    assert caught.value.error_class == VoiceErrorClass.VALIDATION_ERROR
    assert server.connections == 0 and server.frames == []


def test_a_vendor_error_is_typed_and_never_carries_the_key(
    soniox_server: Callable[..., FakeSoniox],
) -> None:
    def refuse(ws: ServerConnection) -> None:
        ws.send(
            json.dumps(
                {
                    "error_code": 401,
                    "error_type": "unauthorized",
                    "error_message": f"Invalid API key {KEY}.",
                    "request_id": "req-1",
                }
            )
        )

    server = soniox_server(refuse)
    with pytest.raises(VoiceError) as caught:
        SonioxSTTProvider(KEY, url=server.url).transcribe(synthesize_wav(SENTENCE))
    error = caught.value
    assert error.error_class == VoiceErrorClass.PROVIDER_AUTH_MISSING
    assert error.details["error_code"] == 401 and error.details["request_id"] == "req-1"
    assert KEY not in json.dumps(error.to_dict())


def test_a_vendor_failure_that_is_not_about_the_key_is_a_dependency_failure(
    soniox_server: Callable[..., FakeSoniox],
) -> None:
    def fail(ws: ServerConnection) -> None:
        ws.send(json.dumps({"error_code": 503, "error_type": "unavailable", "error_message": "x"}))

    server = soniox_server(fail)
    with pytest.raises(VoiceError) as caught:
        SonioxSTTProvider(KEY, url=server.url).transcribe(synthesize_wav(SENTENCE))
    assert caught.value.error_class == VoiceErrorClass.DEPENDENCY_UNAVAILABLE


def test_a_server_that_hangs_up_before_finishing_is_a_dependency_failure(
    soniox_server: Callable[..., FakeSoniox],
) -> None:
    def hang_up(ws: ServerConnection) -> None:
        ws.send(json.dumps(TRANSCRIPT_RESPONSES[1]))
        ws.close()

    server = soniox_server(hang_up)
    with pytest.raises(VoiceError) as caught:
        SonioxSTTProvider(KEY, url=server.url).transcribe(synthesize_wav(SENTENCE))
    assert caught.value.error_class == VoiceErrorClass.DEPENDENCY_UNAVAILABLE


def test_a_silent_server_is_a_timeout_not_a_hang(
    soniox_server: Callable[..., FakeSoniox],
) -> None:
    release = threading.Event()

    def say_nothing(ws: ServerConnection) -> None:
        release.wait(timeout=30)

    server = soniox_server(say_nothing)
    try:
        with pytest.raises(VoiceError) as caught:
            SonioxSTTProvider(KEY, url=server.url, timeout_s=0.3).transcribe(
                synthesize_wav(SENTENCE)
            )
        assert caught.value.error_class == VoiceErrorClass.TIMEOUT
    finally:
        release.set()


def test_nobody_listening_is_a_dependency_failure() -> None:
    with pytest.raises(VoiceError) as caught:
        SonioxSTTProvider(KEY, url="ws://127.0.0.1:9/transcribe-websocket", timeout_s=5).transcribe(
            synthesize_wav(SENTENCE)
        )
    assert caught.value.error_class in (
        VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
        VoiceErrorClass.TIMEOUT,
    )
    assert KEY not in str(caught.value.to_dict())


def test_capabilities_and_the_two_regions() -> None:
    caps = SonioxSTTProvider(KEY).capabilities()
    assert caps.kind == "stt" and caps.requires_api_key is True
    assert caps.supports_language("tr-TR")
    assert SONIOX_URL_US == "wss://stt-rt.soniox.com/transcribe-websocket"
    assert SONIOX_URL_EU == "wss://stt-rt.eu.soniox.com/transcribe-websocket"
