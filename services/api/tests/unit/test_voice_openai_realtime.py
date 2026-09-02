"""M12 track B unit tests: the OpenAI Realtime adapter, offline.

No test here opens a socket or needs a key. The mint path is exercised against
an httpx MockTransport; the standing vendor key is a NON-secret sentinel that
must never appear in a credential, a client dict, an exception, a repr or a
captured log line.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import httpx
import pytest
import structlog

from app.config import Settings
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import (
    END_OF_TURN_SEMANTIC,
    RT_ERROR,
    RT_RESPONSE_AUDIO,
    RT_RESPONSE_DONE,
    RT_RESPONSE_STARTED,
    RT_SPEECH_STARTED,
    RT_SPEECH_STOPPED,
    RT_TOOL_CALL,
    TRANSPORT_SIMULATED,
    TRANSPORT_WEBRTC,
    TRANSPORT_WEBSOCKET,
    VENDOR_TOOL_NAME_PATTERN,
    EphemeralCredential,
    RealtimeProvider,
    RealtimeSessionConfig,
    vendor_tool_name,
)
from app.voice.providers_openai_realtime import (
    CMD_ITEM_CREATE,
    CMD_ITEM_TRUNCATE,
    CMD_OUTPUT_AUDIO_CLEAR,
    CMD_RESPONSE_CANCEL,
    CMD_RESPONSE_CREATE,
    CMD_SESSION_UPDATE,
    CREDENTIAL_TTL_MAX_S,
    CREDENTIAL_TTL_MIN_S,
    DATA_CHANNEL_NAME,
    EV_ERROR,
    EV_FUNCTION_CALL_ARGS_DELTA,
    EV_FUNCTION_CALL_ARGS_DONE,
    EV_INPUT_TRANSCRIPT_COMPLETED,
    EV_OUTPUT_AUDIO_DELTA,
    EV_OUTPUT_AUDIO_DELTA_LEGACY,
    EV_RESPONSE_CANCELLED,
    EV_RESPONSE_CREATED,
    EV_RESPONSE_DONE,
    EV_SPEECH_STARTED,
    EV_SPEECH_STOPPED,
    OPENAI_REALTIME_PROVIDER_NAME,
    OpenAIRealtimeProvider,
    barge_in_commands,
    command_types,
    input_transcript,
    map_server_event,
    map_tools,
    say_command,
    scrub_secrets,
    session_update_command,
    tool_result_commands,
)
from app.voice.realtime_sessions.runtime import (
    REJECT_SIMULATED_OUTSIDE_DEV,
    RealtimeVoiceRuntime,
    default_providers,
    inactive_candidates,
    simulator_allowed,
)
from app.voice.realtime_sessions.tools import default_registry
from app.voice.selection import missing_requirements
from app.voice.simulator import SIMULATOR_PROVIDER_NAME, SimulatedRealtimeProvider

# A recognisable NON-secret sentinel (not shaped like any vendor key format).
KEY = "unit-test-openai-standing-key-sentinel-never-leaves-cloud-core"
EPHEMERAL = "ek_unit_test_ephemeral_value"
TOOLS = tuple(default_registry().manifest())
CONFIG = RealtimeSessionConfig(
    language="tr-TR", instructions="Sen PagentOS'un sesli asistanısın.", tools=TOOLS,
)


def provider(key: str | None = KEY, **kw) -> OpenAIRealtimeProvider:
    return OpenAIRealtimeProvider(key, **kw)


def _assert_no_key(*objects: object) -> None:
    for obj in objects:
        assert KEY not in repr(obj)
        assert KEY not in str(obj)


@pytest.fixture()
def mock_http(monkeypatch):
    """Route the adapter's lazily-imported httpx.Client through a MockTransport.
    Returns a recorder; ``recorder.handler`` can be swapped per test."""

    class Recorder:
        def __init__(self) -> None:
            self.requests: list[httpx.Request] = []
            self.handler = self.ok

        def ok(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "value": EPHEMERAL,
                "expires_at": 1_800_000_000,
                "session": {
                    "id": "sess_unit_1", "object": "realtime.session",
                    "client_secret": {"value": EPHEMERAL, "expires_at": 1_800_000_000},
                    "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000}}},
                },
            })

        def __call__(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.handler(request)

    recorder = Recorder()
    transport = httpx.MockTransport(recorder)
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    return recorder


# ------------------------------------------------------------ capabilities


def test_capabilities_are_declared_truthfully_from_the_survey() -> None:
    caps = provider().capabilities()
    assert caps.name == OPENAI_REALTIME_PROVIDER_NAME
    assert caps.kind == "realtime"
    assert caps.speech_to_speech and caps.full_duplex and caps.barge_in and caps.tool_calling
    assert caps.end_of_turn == END_OF_TURN_SEMANTIC  # semantic_vad exists
    assert caps.transports == (TRANSPORT_WEBRTC, TRANSPORT_WEBSOCKET)
    assert TRANSPORT_SIMULATED not in caps.transports
    assert caps.ephemeral_credentials is True
    assert caps.requires_api_key is True
    assert "pcm16" in caps.input_formats and "pcm16" in caps.output_formats
    assert caps.supports_language("tr-TR")
    assert caps.is_conversation_capable("tr-TR")
    assert missing_requirements(caps, require_ephemeral_credentials=True) == ()
    assert caps.interrupt_latency_class in ("fast", "medium", "slow")
    assert caps.cost_metadata["usd_per_1m_audio_input"] == 32.0
    assert caps.cost_metadata["usd_per_1m_audio_output"] == 64.0


def test_adapter_satisfies_realtime_provider_protocol_and_hides_the_key() -> None:
    p = provider()
    assert isinstance(p, RealtimeProvider)
    _assert_no_key(p)
    assert "configured" in repr(p)
    assert "absent" in repr(provider(None))


def test_open_session_is_not_a_cloud_core_concern() -> None:
    with pytest.raises(VoiceError) as exc:
        provider().open_session()
    assert exc.value.error_class is VoiceErrorClass.CAPABILITY_MISSING


def test_invalid_eagerness_and_format_rejected_at_construction() -> None:
    with pytest.raises(VoiceError):
        provider(eagerness="eager")
    with pytest.raises(VoiceError):
        provider(audio_format="mp3")


def test_from_settings_reads_the_realtime_settings() -> None:
    settings = Settings(
        _env_file=None, voice_openai_api_key=KEY, voice_realtime_openai_model="unit-model",
        voice_realtime_openai_voice="cedar", voice_realtime_openai_eagerness="high",
    )
    p = OpenAIRealtimeProvider.from_settings(settings)
    session = p.build_session_config(CONFIG)
    assert session["model"] == "unit-model"
    assert session["audio"]["output"]["voice"] == "cedar"
    assert session["audio"]["input"]["turn_detection"]["eagerness"] == "high"
    assert p.has_key
    _assert_no_key(p)


def test_settings_defaults_are_sane() -> None:
    s = Settings(_env_file=None)
    assert s.voice_realtime_openai_eagerness in ("low", "medium", "high", "auto")
    assert s.voice_realtime_openai_base_url.startswith("https://")
    assert s.voice_realtime_simulator_enabled is False
    assert CREDENTIAL_TTL_MIN_S <= s.voice_realtime_credential_ttl_s <= CREDENTIAL_TTL_MAX_S


# ------------------------------------------------------- credential request


def test_credential_request_shape_exact_endpoint_headers_and_body() -> None:
    req = provider().build_credential_request(
        session_id="sess-1", ttl_s=600, transport=TRANSPORT_WEBRTC, session_config=CONFIG,
    )
    assert req.method == "POST"
    assert req.url == "https://api.openai.com/v1/realtime/client_secrets"
    assert req.headers["Authorization"].startswith("Bearer ")
    assert req.headers["Content-Type"] == "application/json"
    assert req.headers["X-PagentOS-Session"] == "sess-1"
    assert req.query == {} and req.data is None
    body = req.json_body
    assert body["expires_after"] == {"anchor": "created_at", "seconds": 600}
    session = body["session"]
    assert session["type"] == "realtime"
    assert session["model"] == "gpt-realtime-2.1"
    assert session["instructions"] == CONFIG.instructions
    assert session["output_modalities"] == ["audio"]
    assert session["tool_choice"] == "auto"
    assert set(session["audio"]) == {"input", "output"}


def test_session_config_audio_transcription_and_semantic_vad() -> None:
    session = provider().build_session_config(CONFIG)
    audio_in = session["audio"]["input"]
    assert audio_in["format"] == {"type": "audio/pcm", "rate": 24000}
    assert audio_in["transcription"] == {"model": "gpt-4o-transcribe", "language": "tr"}
    assert audio_in["turn_detection"] == {
        "type": "semantic_vad", "eagerness": "low",
        "create_response": True, "interrupt_response": True,
    }
    assert session["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session["audio"]["output"]["voice"] == "marin"


@pytest.mark.parametrize("eagerness", ["low", "medium", "high", "auto"])
def test_semantic_vad_eagerness_is_configurable(eagerness: str) -> None:
    session = provider(eagerness=eagerness).build_session_config(CONFIG)
    assert session["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert session["audio"]["input"]["turn_detection"]["eagerness"] == eagerness


def test_session_config_voice_override_and_language_hint() -> None:
    cfg = RealtimeSessionConfig(language="en-US", instructions="x", voice="cedar")
    session = provider().build_session_config(cfg)
    assert session["audio"]["output"]["voice"] == "cedar"
    assert session["audio"]["input"]["transcription"]["language"] == "en"


def test_tools_manifest_maps_to_realtime_function_schema() -> None:
    session = provider().build_session_config(CONFIG)
    tools = session["tools"]
    assert len(tools) == len(TOOLS) > 0
    by_name = {t["name"]: t for t in tools}
    for entry in TOOLS:
        mapped = by_name[vendor_tool_name(entry["name"])]
        assert VENDOR_TOOL_NAME_PATTERN.match(mapped["name"]), mapped["name"]
        assert mapped["type"] == "function"
        assert mapped["description"] == entry["description"]
        assert mapped["parameters"] == entry["parameters"]
        assert "long_running" not in mapped and "preamble" not in mapped


def test_map_tools_defaults_and_rejects_nameless() -> None:
    mapped = map_tools([{"name": "t", "description": "d"}])
    assert mapped[0]["parameters"] == {"type": "object", "properties": {}}
    with pytest.raises(VoiceError):
        map_tools([{"description": "no name"}])
    session = provider().build_session_config(RealtimeSessionConfig(instructions=""))
    assert "tools" not in session and "instructions" not in session


@pytest.mark.parametrize("ttl,expected", [(1, 10), (10, 10), (600, 600), (99999, 7200)])
def test_credential_ttl_clamped_to_vendor_bounds(ttl: int, expected: int) -> None:
    req = provider().build_credential_request(
        session_id="s", ttl_s=ttl, transport=TRANSPORT_WEBSOCKET)
    assert req.json_body["expires_after"]["seconds"] == expected


def test_credential_request_rejects_unsupported_transport() -> None:
    with pytest.raises(VoiceError) as exc:
        provider().build_credential_request(session_id="s", ttl_s=60, transport=TRANSPORT_SIMULATED)
    assert exc.value.error_class is VoiceErrorClass.VALIDATION_ERROR


# ---------------------------------------------------- transport descriptor


def test_webrtc_transport_descriptor_is_the_client_contract() -> None:
    d = provider().transport_descriptor(TRANSPORT_WEBRTC)
    assert d["transport"] == TRANSPORT_WEBRTC
    assert d["sdp_exchange_url"] == "https://api.openai.com/v1/realtime/calls"
    assert d["data_channel"] == DATA_CHANNEL_NAME == "oai-events"
    assert d["audio"] == {"format": "pcm16", "sample_rate_hz": 24000, "channels": 1,
                          "sample_width_bits": 16, "endianness": "little"}
    assert d["session_max_minutes"] == 60
    _assert_no_key(d)


def test_websocket_transport_descriptor() -> None:
    d = provider(base_url="https://api.openai.com/v1/").transport_descriptor(TRANSPORT_WEBSOCKET)
    assert d["transport"] == TRANSPORT_WEBSOCKET
    assert d["websocket_url"] == "wss://api.openai.com/v1/realtime"
    assert d["query"] == {"model": "gpt-realtime-2.1"}
    assert d["audio"]["sample_rate_hz"] == 24000
    _assert_no_key(d)


def test_transport_descriptor_rejects_unknown_transport() -> None:
    with pytest.raises(VoiceError):
        provider().transport_descriptor("sip")


# ------------------------------------------------------------------ mint


def test_mint_without_key_is_inert_no_io(mock_http) -> None:
    def boom(request):  # pragma: no cover - must not be reached
        raise AssertionError("network must not be touched without a key")

    mock_http.handler = boom
    with pytest.raises(VoiceError) as exc:
        provider(None).mint_credential(session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC)
    assert exc.value.error_class is VoiceErrorClass.PROVIDER_AUTH_MISSING
    assert mock_http.requests == []


def test_mint_returns_ephemeral_only_and_never_the_key(mock_http) -> None:
    p = provider()
    with structlog.testing.capture_logs() as logs:
        result = p.mint(session_id="sess-9", ttl_s=120, transport=TRANSPORT_WEBRTC,
                        session_config=CONFIG)
    cred = result.credential
    assert isinstance(cred, EphemeralCredential)
    assert cred.provider == OPENAI_REALTIME_PROVIDER_NAME
    assert cred.secret == EPHEMERAL
    assert cred.expires_at == datetime.fromtimestamp(1_800_000_000, tz=UTC)
    assert cred.transport == TRANSPORT_WEBRTC
    assert cred.session_ref == "sess_unit_1"
    assert cred.transport_descriptor["data_channel"] == "oai-events"
    client = cred.to_client_dict()
    assert client["secret"] == EPHEMERAL
    assert client["transport_descriptor"]["sdp_exchange_url"].endswith("/realtime/calls")
    assert client["expires_at"].endswith("Z")
    # the standing key: sent to the vendor exactly once, nowhere else
    assert len(mock_http.requests) == 1
    sent = mock_http.requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == "https://api.openai.com/v1/realtime/client_secrets"
    assert sent.headers["Authorization"] == f"Bearer {KEY}"
    body = json.loads(sent.content)
    assert body["session"]["instructions"] == CONFIG.instructions
    assert body["session"]["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    _assert_no_key(cred, client, result.session_echo, logs)
    # the vendor's echo is scrubbed of the secret too
    assert "client_secret" not in result.session_echo
    assert result.session_echo["audio"]["input"]["format"]["rate"] == 24000
    assert any(log["event"] == "openai_realtime_credential_minted" for log in logs)
    assert all(EPHEMERAL not in json.dumps(log, default=str) for log in logs)


def test_mint_credential_protocol_entry_point(mock_http) -> None:
    cred = provider().mint_credential(session_id="s", ttl_s=60, transport=TRANSPORT_WEBSOCKET)
    assert cred.transport == TRANSPORT_WEBSOCKET
    assert cred.transport_descriptor["websocket_url"] == "wss://api.openai.com/v1/realtime"


def test_mint_http_error_is_typed_retryable_and_scrubbed(mock_http) -> None:
    mock_http.handler = lambda request: httpx.Response(500, json={"error": "server"})
    with pytest.raises(VoiceError) as exc:
        provider().mint_credential(session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC)
    err = exc.value
    assert err.error_class is VoiceErrorClass.DEPENDENCY_UNAVAILABLE
    assert err.retryable is True
    assert err.provider == OPENAI_REALTIME_PROVIDER_NAME
    _assert_no_key(err, err.to_dict(), err.message, err.details)


def test_mint_timeout_is_typed_and_scrubbed(mock_http) -> None:
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    mock_http.handler = slow
    with pytest.raises(VoiceError) as exc:
        provider().mint_credential(session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC)
    assert exc.value.error_class is VoiceErrorClass.TIMEOUT
    _assert_no_key(exc.value, exc.value.to_dict())


def test_mint_error_that_echoes_the_key_is_redacted(mock_http) -> None:
    """A transport error whose text carries the Authorization value (proxies,
    debug builds) must not surface the key through the adapter."""

    def leaky(request):
        raise httpx.ConnectError(f"refused for Bearer {KEY}", request=request)

    mock_http.handler = leaky
    with structlog.testing.capture_logs() as logs:
        with pytest.raises(VoiceError) as exc:
            provider().mint_credential(session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC)
    assert "[redacted]" in exc.value.message
    _assert_no_key(exc.value, exc.value.to_dict(), logs)


@pytest.mark.parametrize("body", [
    {"expires_at": 1_800_000_000},  # no value
    {"value": ""},
    [],
])
def test_mint_rejects_responses_without_a_credential(mock_http, body) -> None:
    mock_http.handler = lambda request: httpx.Response(200, json=body)
    with pytest.raises(VoiceError) as exc:
        provider().mint_credential(session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC)
    assert exc.value.error_class is VoiceErrorClass.DEPENDENCY_UNAVAILABLE
    _assert_no_key(exc.value)


def test_mint_non_json_response(mock_http) -> None:
    mock_http.handler = lambda request: httpx.Response(200, content=b"<html>")
    with pytest.raises(VoiceError) as exc:
        provider().mint_credential(session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC)
    assert exc.value.error_class is VoiceErrorClass.DEPENDENCY_UNAVAILABLE


def test_mint_falls_back_to_requested_ttl_when_expiry_missing(mock_http) -> None:
    mock_http.handler = lambda request: httpx.Response(200, json={"value": EPHEMERAL})
    before = datetime.now(UTC)
    cred = provider().mint_credential(session_id="s7", ttl_s=60, transport=TRANSPORT_WEBRTC)
    assert 55 <= (cred.expires_at - before).total_seconds() <= 65
    assert cred.session_ref == "openai:s7"


def test_scrub_secrets_is_recursive() -> None:
    echo = {"id": "x", "client_secret": {"value": "v"}, "nested": [{"value": "v", "ok": 1}],
            "Authorization": "b"}
    assert scrub_secrets(echo) == {"id": "x", "nested": [{"ok": 1}]}


# --------------------------------------------------------- event mapping


def _one(event: dict, at_ms: int = 100):
    events = map_server_event(event, at_ms=at_ms)
    assert len(events) == 1
    return events[0]


def test_map_speech_started_and_stopped() -> None:
    e = _one({"type": EV_SPEECH_STARTED, "item_id": "it1", "audio_start_ms": 1200})
    assert e.kind == RT_SPEECH_STARTED and e.at_ms == 100
    assert e.payload == {"item_id": "it1", "audio_start_ms": 1200}
    e = _one({"type": EV_SPEECH_STOPPED, "item_id": "it1", "audio_end_ms": 2400})
    assert e.kind == RT_SPEECH_STOPPED
    assert e.payload == {"item_id": "it1", "audio_end_ms": 2400}


def test_map_response_started_and_audio_delta_counts_bytes_only() -> None:
    e = _one({"type": EV_RESPONSE_CREATED, "response": {"id": "r1"}})
    assert e.kind == RT_RESPONSE_STARTED and e.payload == {"response_id": "r1"}
    pcm = b"\x00\x01" * 480
    delta = base64.b64encode(pcm).decode()
    e = _one({"type": EV_OUTPUT_AUDIO_DELTA, "response_id": "r1", "item_id": "i1",
              "delta": delta})
    assert e.kind == RT_RESPONSE_AUDIO
    assert e.payload == {"response_id": "r1", "item_id": "i1", "bytes": len(pcm)}
    assert delta not in json.dumps(e.to_dict())
    legacy = _one({"type": EV_OUTPUT_AUDIO_DELTA_LEGACY, "delta": delta})
    assert legacy.kind == RT_RESPONSE_AUDIO and legacy.payload["bytes"] == len(pcm)
    assert _one({"type": EV_OUTPUT_AUDIO_DELTA, "delta": "!!not-b64"}).payload["bytes"] == 0


def test_map_response_done_completed_and_cancelled() -> None:
    done = _one({"type": EV_RESPONSE_DONE, "response": {
        "id": "r1", "status": "completed",
        "output": [{"type": "message"}, {"type": "function_call", "call_id": "c1"}],
    }})
    assert done.kind == RT_RESPONSE_DONE
    assert done.payload == {"response_id": "r1", "cancelled": False, "status": "completed",
                            "tool_call_ids": ["c1"]}
    cancelled = _one({"type": EV_RESPONSE_DONE, "response": {"id": "r2", "status": "cancelled"}})
    assert cancelled.payload["cancelled"] is True
    explicit = _one({"type": EV_RESPONSE_CANCELLED, "response": {"id": "r3"}})
    assert explicit.kind == RT_RESPONSE_DONE and explicit.payload["cancelled"] is True


def test_map_tool_call_with_parsed_arguments() -> None:
    e = _one({"type": EV_FUNCTION_CALL_ARGS_DONE, "call_id": "call_1", "name": "research.start",
              "arguments": '{"topic": "gecikme", "scope": "Türkçe"}', "response_id": "r1",
              "item_id": "i9"})
    assert e.kind == RT_TOOL_CALL
    assert e.payload["call_id"] == "call_1"
    assert e.payload["name"] == "research.start"
    assert e.payload["arguments"] == {"topic": "gecikme", "scope": "Türkçe"}
    assert e.payload["item_id"] == "i9"


def test_map_tool_call_malformed_arguments_are_preserved_not_lost() -> None:
    e = _one({"type": EV_FUNCTION_CALL_ARGS_DONE, "call_id": "c", "name": "n",
              "arguments": "{not json"})
    assert e.payload["arguments"] == {"_raw": "{not json", "_parse_error": True}
    e = _one({"type": EV_FUNCTION_CALL_ARGS_DONE, "call_id": "c", "name": "n", "arguments": "[1]"})
    assert e.payload["arguments"] == {"value": [1]}
    e = _one({"type": EV_FUNCTION_CALL_ARGS_DONE, "call_id": "c", "name": "n"})
    assert e.payload["arguments"] == {}


def test_map_error_event_bounded() -> None:
    e = _one({"type": EV_ERROR, "error": {"type": "invalid_request_error", "code": "x",
                                           "message": "m" * 2000, "event_id": "ev"}})
    assert e.kind == RT_ERROR
    assert e.payload["type"] == "invalid_request_error"
    assert e.payload["code"] == "x" and e.payload["event_id"] == "ev"
    assert len(e.payload["message"]) == 500


def test_incremental_and_unknown_events_map_to_nothing() -> None:
    assert map_server_event({"type": EV_FUNCTION_CALL_ARGS_DELTA, "delta": "{"}, at_ms=1) == ()
    assert map_server_event({"type": "session.created"}, at_ms=1) == ()
    assert map_server_event({"type": "rate_limits.updated"}, at_ms=1) == ()
    assert map_server_event({}, at_ms=1) == ()


def test_tool_call_is_emitted_once_per_stream() -> None:
    """function_call_arguments.done carries the call; response.done only lists it."""
    stream = [
        {"type": EV_FUNCTION_CALL_ARGS_DELTA, "call_id": "c1", "delta": "{"},
        {"type": EV_FUNCTION_CALL_ARGS_DONE, "call_id": "c1", "name": "clock.now",
         "arguments": "{}"},
        {"type": EV_RESPONSE_DONE, "response": {"id": "r", "status": "completed", "output": [
            {"type": "function_call", "call_id": "c1", "name": "clock.now", "arguments": "{}"},
        ]}},
    ]
    kinds = [e.kind for ev in stream for e in map_server_event(ev, at_ms=0)]
    assert kinds.count(RT_TOOL_CALL) == 1
    assert kinds == [RT_TOOL_CALL, RT_RESPONSE_DONE]


def test_input_transcript_helper() -> None:
    assert input_transcript({"type": EV_INPUT_TRANSCRIPT_COMPLETED,
                             "transcript": "ikinci maddeyi tekrar oku"}) == (
        "ikinci maddeyi tekrar oku")
    assert input_transcript({"type": EV_SPEECH_STARTED}) is None
    assert input_transcript({"type": EV_INPUT_TRANSCRIPT_COMPLETED, "transcript": 3}) is None


# ------------------------------------------------------ outgoing commands


def test_barge_in_sequence_webrtc_cancel_clear_truncate() -> None:
    cmds = barge_in_commands(transport=TRANSPORT_WEBRTC, item_id="it1", content_index=0,
                             audio_end_ms=1234)
    assert command_types(cmds) == (CMD_RESPONSE_CANCEL, CMD_OUTPUT_AUDIO_CLEAR, CMD_ITEM_TRUNCATE)
    assert cmds[2] == {"type": CMD_ITEM_TRUNCATE, "item_id": "it1", "content_index": 0,
                       "audio_end_ms": 1234}


def test_barge_in_sequence_websocket_has_no_server_buffer_clear() -> None:
    cmds = barge_in_commands(transport=TRANSPORT_WEBSOCKET, item_id="it1", audio_end_ms=500)
    assert command_types(cmds) == (CMD_RESPONSE_CANCEL, CMD_ITEM_TRUNCATE)


def test_barge_in_without_item_knowledge_still_cancels() -> None:
    assert command_types(barge_in_commands(transport=TRANSPORT_WEBRTC)) == (
        CMD_RESPONSE_CANCEL, CMD_OUTPUT_AUDIO_CLEAR)
    assert command_types(barge_in_commands(transport=TRANSPORT_WEBSOCKET)) == (
        CMD_RESPONSE_CANCEL,)
    negative = barge_in_commands(transport=TRANSPORT_WEBSOCKET, item_id="i", audio_end_ms=-5)
    assert negative[-1]["audio_end_ms"] == 0
    with pytest.raises(VoiceError):
        barge_in_commands(transport=TRANSPORT_SIMULATED)


def test_tool_result_submission_sequence() -> None:
    cmds = tool_result_commands("call_7", {"status": "ok", "özet": "gecikme düştü"})
    assert command_types(cmds) == (CMD_ITEM_CREATE, CMD_RESPONSE_CREATE)
    item = cmds[0]["item"]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "call_7"
    assert json.loads(item["output"]) == {"status": "ok", "özet": "gecikme düştü"}
    assert "\\u" not in item["output"]  # Turkish characters preserved
    assert cmds[1] == {"type": CMD_RESPONSE_CREATE}
    with pytest.raises(VoiceError):
        tool_result_commands("", {})


def test_say_and_session_update_commands() -> None:
    cmd = say_command("Bakıyorum.")
    assert cmd["type"] == CMD_RESPONSE_CREATE
    assert cmd["response"]["output_modalities"] == ["audio"]
    assert cmd["response"]["instructions"].endswith("Bakıyorum.")
    with pytest.raises(VoiceError):
        say_command("   ")
    upd = session_update_command({"instructions": "x"})
    assert upd == {"type": CMD_SESSION_UPDATE, "session": {"instructions": "x"}}


# ------------------------------------------------ selection: dev vs prod


def _runtime(settings: Settings, providers=None) -> RealtimeVoiceRuntime:
    return RealtimeVoiceRuntime(settings, providers=providers)


def test_dev_without_key_registers_only_the_simulator() -> None:
    settings = Settings(_env_file=None, environment="dev")
    assert simulator_allowed(settings)
    providers = default_providers(settings)
    assert set(providers) == {SIMULATOR_PROVIDER_NAME}
    assert OPENAI_REALTIME_PROVIDER_NAME in inactive_candidates(settings)
    rt = _runtime(settings)
    chosen, result = rt.select()
    assert chosen.name == SIMULATOR_PROVIDER_NAME
    health = rt.health_check()
    assert health["status"] == "ok"
    assert "provider_auth_missing" in health["inactive"][OPENAI_REALTIME_PROVIDER_NAME]
    _assert_no_key(health)


def test_dev_with_key_ranks_the_real_adapter_above_the_simulator() -> None:
    settings = Settings(_env_file=None, environment="dev", voice_openai_api_key=KEY)
    providers = default_providers(settings)
    assert set(providers) == {OPENAI_REALTIME_PROVIDER_NAME, SIMULATOR_PROVIDER_NAME}
    rt = _runtime(settings)
    chosen, result = rt.select()
    assert chosen.name == OPENAI_REALTIME_PROVIDER_NAME
    assert result.ranked == (OPENAI_REALTIME_PROVIDER_NAME, SIMULATOR_PROVIDER_NAME)
    assert result.transport == TRANSPORT_WEBRTC
    assert "transport=webrtc" in result.reasons and "end_of_turn=semantic" in result.reasons
    assert inactive_candidates(settings) == {}
    _assert_no_key(rt.health_check())


def test_prod_without_key_has_no_provider_and_says_why() -> None:
    settings = Settings(_env_file=None, environment="prod")
    assert not simulator_allowed(settings)
    assert default_providers(settings) == {}
    inactive = inactive_candidates(settings)
    assert set(inactive) == {OPENAI_REALTIME_PROVIDER_NAME, SIMULATOR_PROVIDER_NAME}
    rt = _runtime(settings)
    with pytest.raises(VoiceError) as exc:
        rt.select()
    assert exc.value.error_class is VoiceErrorClass.CAPABILITY_MISSING
    assert set(exc.value.details["inactive"]) == set(inactive)
    health = rt.health_check()
    assert health["status"] == "fail"
    assert health["providers"] == []
    assert health["environment"] == "prod"


def test_prod_with_key_registers_only_the_real_adapter() -> None:
    settings = Settings(_env_file=None, environment="prod", voice_openai_api_key=KEY)
    rt = _runtime(settings)
    assert set(rt.providers) == {OPENAI_REALTIME_PROVIDER_NAME}
    chosen, result = rt.select()
    assert chosen.name == OPENAI_REALTIME_PROVIDER_NAME
    assert result.ranked == (OPENAI_REALTIME_PROVIDER_NAME,)
    assert rt.inactive == {SIMULATOR_PROVIDER_NAME: inactive_candidates(settings)[
        SIMULATOR_PROVIDER_NAME]}
    _assert_no_key(rt.health_check())


def test_prod_bars_an_explicitly_registered_simulator() -> None:
    """Defence in depth: even handed the simulator directly, production refuses it."""
    settings = Settings(_env_file=None, environment="prod", voice_openai_api_key=KEY)
    sim = SimulatedRealtimeProvider()
    openai = OpenAIRealtimeProvider.from_settings(settings)
    rt = _runtime(settings, providers={sim.name: sim, openai.name: openai})
    chosen, result = rt.select()
    assert chosen.name == OPENAI_REALTIME_PROVIDER_NAME
    assert result.rejected[SIMULATOR_PROVIDER_NAME] == (REJECT_SIMULATED_OUTSIDE_DEV,)
    assert SIMULATOR_PROVIDER_NAME not in result.ranked
    # and with the simulator alone, production has no provider rather than a fake one
    rt_only_sim = _runtime(settings, providers={sim.name: sim})
    with pytest.raises(VoiceError) as exc:
        rt_only_sim.select()
    assert exc.value.details["rejected"][SIMULATOR_PROVIDER_NAME] == [REJECT_SIMULATED_OUTSIDE_DEV]


def test_explicit_setting_re_enables_the_simulator_outside_dev() -> None:
    settings = Settings(_env_file=None, environment="staging",
                        voice_realtime_simulator_enabled=True)
    assert simulator_allowed(settings)
    rt = _runtime(settings)
    chosen, _ = rt.select()
    assert chosen.name == SIMULATOR_PROVIDER_NAME
    assert SIMULATOR_PROVIDER_NAME not in inactive_candidates(settings)


def test_preference_order_never_outranks_capability() -> None:
    """The real adapter wins on semantic+webrtc even if the preference list
    names the simulator first — selection is by capability, not by name."""
    settings = Settings(_env_file=None, environment="dev", voice_openai_api_key=KEY,
                        voice_realtime_provider_preference=(SIMULATOR_PROVIDER_NAME,))
    chosen, result = _runtime(settings).select()
    assert chosen.name == OPENAI_REALTIME_PROVIDER_NAME
    assert result.ranked[0] == OPENAI_REALTIME_PROVIDER_NAME


def test_descriptors_carry_the_keys_the_web_client_requires() -> None:
    # Cross-track contract (tracks B <-> D): apps/web/app/lib/voice/transport.ts
    # refuses to open a leg without sdp_exchange_url + data_channel + dialect, and
    # dialects/index.ts resolves the dialect by name with no default. Track B first
    # shipped "sdp_endpoint" and no dialect at all, which the web client could not use.
    from app.voice.providers_openai_realtime import WEB_CLIENT_DIALECT, OpenAIRealtimeProvider

    provider = OpenAIRealtimeProvider(api_key="test-not-a-secret")
    webrtc = provider.transport_descriptor("webrtc")
    assert webrtc["sdp_exchange_url"].endswith("/realtime/calls")
    assert webrtc["data_channel"] and webrtc["dialect"] == WEB_CLIENT_DIALECT == "openai-realtime"
    assert "sdp_endpoint" not in webrtc
    assert provider.transport_descriptor("websocket")["dialect"] == WEB_CLIENT_DIALECT


# ----------------------------------------------- contract regression (owner smoke 2026-09-02)
# A REAL owner smoke returned 400 after the key was fixed, with the body discarded by the
# HTTP helper. These pin (a) the minimal current client_secrets contract byte for byte,
# (b) that no beta-era key can silently return, (c) that each M12 option is a separate,
# cumulative layer the smoke can add one at a time, and (d) that a non-2xx carries the
# vendor's error object so the next 400 names its field.

BETA_ERA_SESSION_KEYS = {
    "modalities", "voice", "input_audio_format", "output_audio_format",
    "input_audio_transcription", "turn_detection", "temperature", "max_response_output_tokens",
}


def test_minimal_request_is_exactly_the_current_client_secrets_contract() -> None:
    from app.voice.providers_openai_realtime import MINIMAL_LAYERS

    req = provider().build_credential_request(
        session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC, session_config=CONFIG,
        layers=MINIMAL_LAYERS,
    )
    assert req.json_body == {
        "session": {
            "type": "realtime",
            "model": "gpt-realtime-2.1",
            "audio": {"output": {"voice": "marin"}},
        }
    }


def test_full_session_uses_only_the_ga_schema_no_beta_era_keys() -> None:
    session = provider().build_session_config(CONFIG)
    assert not (set(session) & BETA_ERA_SESSION_KEYS)
    assert set(session) == {
        "type", "model", "audio", "output_modalities", "instructions", "tools", "tool_choice",
    }
    assert set(session["audio"]) == {"input", "output"}
    assert set(session["audio"]["input"]) == {"format", "transcription", "turn_detection"}
    assert set(session["audio"]["output"]) == {"voice", "format", "speed"}


def _path_present(body: dict, path: tuple[str, ...]) -> bool:
    node: object = body
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True


def test_layers_are_cumulative_each_adding_exactly_its_option() -> None:
    from app.voice.providers_openai_realtime import SESSION_LAYERS

    expected_path = {
        "expires_after": ("expires_after",),
        "output_modalities": ("session", "output_modalities"),
        "audio_formats": ("session", "audio", "input", "format"),
        "transcription": ("session", "audio", "input", "transcription"),
        "turn_detection": ("session", "audio", "input", "turn_detection"),
        "instructions": ("session", "instructions"),
        "tools": ("session", "tools"),
    }
    assert set(expected_path) == set(SESSION_LAYERS)
    p = provider()
    for idx, layer in enumerate(SESSION_LAYERS):
        before = p.build_credential_request(
            session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC, session_config=CONFIG,
            layers=SESSION_LAYERS[:idx]).json_body
        after = p.build_credential_request(
            session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC, session_config=CONFIG,
            layers=SESSION_LAYERS[:idx + 1]).json_body
        assert not _path_present(before, expected_path[layer]), layer
        assert _path_present(after, expected_path[layer]), layer
    full = p.build_credential_request(
        session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC, session_config=CONFIG).json_body
    assert full == p.build_credential_request(
        session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC, session_config=CONFIG,
        layers=SESSION_LAYERS).json_body
    turn = full["session"]["audio"]["input"]["turn_detection"]
    assert turn["type"] == "semantic_vad" and turn["interrupt_response"] is True


def test_unknown_layer_is_refused_before_any_io() -> None:
    with pytest.raises(VoiceError):
        provider().build_session_config(CONFIG, layers=("vad",))


def test_http_400_carries_the_vendor_error_body_and_is_not_retryable(mock_http) -> None:
    vendor = {
        "type": "invalid_request_error",
        "code": "unknown_parameter",
        "param": "session.audio.input.turn_detection",
        "message": "Unknown parameter: 'session.audio.input.turn_detection'.",
    }
    mock_http.handler = lambda request: httpx.Response(400, json={"error": vendor})
    with pytest.raises(VoiceError) as exc:
        provider().mint_credential(session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC)
    err = exc.value
    assert err.error_class is VoiceErrorClass.DEPENDENCY_UNAVAILABLE
    assert err.retryable is False
    assert err.details["http_status"] == 400
    assert err.details["vendor_error"] == vendor
    assert "Unknown parameter" in err.message
    _assert_no_key(err, err.to_dict(), err.message, err.details)


@pytest.mark.parametrize("status,retryable", [(400, False), (404, False), (429, True), (503, True)])
def test_http_status_decides_retryability(mock_http, status: int, retryable: bool) -> None:
    mock_http.handler = lambda request: httpx.Response(status, text="nope")
    with pytest.raises(VoiceError) as exc:
        provider().mint_credential(session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC)
    assert exc.value.retryable is retryable
    assert exc.value.details["http_status"] == status
    assert exc.value.details["vendor_error"]["message"] == "nope"


def test_list_realtime_models_returns_ids_only(mock_http) -> None:
    mock_http.handler = lambda request: httpx.Response(200, json={"data": [
        {"id": "gpt-realtime-2.1", "object": "model"},
        {"id": "gpt-4o", "object": "model"},
        {"id": "gpt-realtime-mini", "object": "model"},
    ]})
    assert provider().list_realtime_models() == ["gpt-realtime-2.1", "gpt-realtime-mini"]
    sent = mock_http.requests[-1]
    assert sent.method == "GET" and str(sent.url) == "https://api.openai.com/v1/models"
    assert sent.headers["Authorization"] == f"Bearer {KEY}"


def test_mint_result_carries_the_request_body_without_headers(mock_http) -> None:
    result = provider().mint(session_id="s", ttl_s=60, transport=TRANSPORT_WEBRTC,
                             session_config=CONFIG)
    assert result.request_body["session"]["model"] == "gpt-realtime-2.1"
    assert "Authorization" not in json.dumps(result.request_body)
    _assert_no_key(result.request_body)


# ------------------------------------------------ tool names at the vendor boundary (real 400)
# The REAL probe on 2026-09-02 accepted every M12 layer except tools: OpenAI refuses
# function names outside ^[a-zA-Z0-9_-]+$ and Cloud Core's tools are dotted. The mapping
# is reversible ('.' <-> '__'), applied outbound in map_tools and inbound on the
# function-call event, and the registry accepts the vendor spelling a client relays.


def test_every_registry_tool_name_maps_to_the_vendor_pattern_and_back() -> None:
    from app.voice.providers import cloud_tool_name

    for entry in TOOLS:
        mapped = vendor_tool_name(entry["name"])
        assert VENDOR_TOOL_NAME_PATTERN.match(mapped), mapped
        assert "." not in mapped
        assert cloud_tool_name(mapped) == entry["name"]
    assert vendor_tool_name("research.start") == "research__start"
    assert cloud_tool_name("clock__now") == "clock.now"
    assert cloud_tool_name("clock.now") == "clock.now"


@pytest.mark.parametrize("bad", ["a__b", "bad name", "tür.şey", ""])
def test_unmappable_tool_names_are_refused_before_the_vendor_sees_them(bad: str) -> None:
    with pytest.raises(VoiceError):
        vendor_tool_name(bad)


def test_full_session_tools_carry_vendor_names_only() -> None:
    session = provider().build_session_config(CONFIG)
    names = [t["name"] for t in session["tools"]]
    assert names and all(VENDOR_TOOL_NAME_PATTERN.match(n) for n in names)
    assert "research__start" in names and "research.start" not in names


def test_function_call_event_maps_the_vendor_name_back_to_cloud_core() -> None:
    ev = _one({"type": EV_FUNCTION_CALL_ARGS_DONE, "call_id": "c1", "name": "research__start",
               "arguments": '{"topic": "x"}', "response_id": "r", "item_id": "i"})
    assert ev.payload["name"] == "research.start"
    assert ev.payload["arguments"] == {"topic": "x"}


def test_registry_resolves_the_vendor_spelling() -> None:
    from app.voice.realtime_sessions.tools import default_registry

    registry = default_registry()
    assert registry.get("research__start") is registry.get("research.start")
    assert registry.get("clock__now").name == "clock.now"
    assert registry.get("nope__tool") is None


# ------------------------------------------ voice profile (ADR-0043, owner feedback 2026-09-02)


def test_supported_voices_is_the_live_discovered_list_and_arbor_is_not_in_it() -> None:
    from app.voice.providers_openai_realtime import SUPPORTED_VOICES

    p = provider()
    assert p.supported_voices() == SUPPORTED_VOICES
    assert set(SUPPORTED_VOICES) == {"alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer",
                                     "verse", "marin", "cedar"}
    assert "arbor" not in SUPPORTED_VOICES
    assert p.require_supported_voice("cedar") == "cedar"
    with pytest.raises(VoiceError) as exc:
        p.require_supported_voice("arbor")
    assert exc.value.error_class is VoiceErrorClass.VALIDATION_ERROR
    assert exc.value.details["supported_voices"] == list(SUPPORTED_VOICES)


def test_output_speed_rides_the_audio_formats_layer_and_is_bounded() -> None:
    from app.voice.providers_openai_realtime import MINIMAL_LAYERS

    session = provider(speed=0.9).build_session_config(CONFIG)
    assert session["audio"]["output"]["speed"] == 0.9
    minimal = provider(speed=0.9).build_session_config(CONFIG, layers=MINIMAL_LAYERS)
    assert "speed" not in minimal["audio"]["output"]  # the minimal contract stays exact
    with pytest.raises(VoiceError):
        provider(speed=3.0)
    with pytest.raises(VoiceError):
        provider(speed=0.1)


def test_persona_carries_the_arbor_style_block_only_for_that_profile() -> None:
    from app.voice.realtime_sessions.persona import VOICE_STYLE_ARBOR_TR, build_instructions

    assert VOICE_STYLE_ARBOR_TR in build_instructions(voice_profile="arbor")
    assert VOICE_STYLE_ARBOR_TR in build_instructions(voice_profile="Arbor")
    assert VOICE_STYLE_ARBOR_TR not in build_instructions()
    assert VOICE_STYLE_ARBOR_TR not in build_instructions(voice_profile="none")
    # the block speaks about HOW to speak, never names a vendor voice id
    for vendor_voice in ("marin", "cedar", "alloy"):
        assert vendor_voice not in VOICE_STYLE_ARBOR_TR.lower()
