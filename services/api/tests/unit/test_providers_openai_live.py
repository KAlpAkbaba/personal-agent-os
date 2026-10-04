"""gpt-live-provider (d20261004): the OpenAI GPT-Live adapter, measured offline.

Fake HTTP only (``app.voice.providers._send`` is patched); no vendor call is made. The
vendor shapes asserted here are the ones the official pages document (read 2026-10-04,
team/plans/gpt-live-provider-adr.md): the browser's SDP offer is exchanged by OUR server
with ``POST /v1/live/sessions`` (JSON ``session`` + ``transport``), the key never leaves
that one header, and only documented events are mapped - a Realtime-style
``speech_started`` is NOT guessed into a Live event.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.voice import providers_openai_live as live
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import (
    END_OF_TURN_SEMANTIC,
    RT_ERROR,
    RT_TOOL_CALL,
    TRANSPORT_WEBRTC,
    ProviderRequest,
    RealtimeSessionConfig,
)
from app.voice.selection import missing_requirements

KEY = "unit-test-openai-live-key-sentinel-never-leaves-cloud-core"
SESSION_ID = "4b0c5c0e-7a51-4c33-9d39-1f0f5a3e2b11"
OFFER = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\n"


class _Resp:
    def __init__(self, payload: Any, status: int = 201) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _provider(**kw: Any) -> live.OpenAILiveProvider:
    return live.OpenAILiveProvider(KEY, **kw)


# ------------------------------------------------------------------- (a) capabilities


def test_capabilities_are_honest_and_dialect_is_openai_live() -> None:
    p = _provider()
    caps = p.capabilities()
    assert caps.name == "openai-live" == live.OPENAI_LIVE_PROVIDER_NAME
    assert caps.kind == "realtime"
    assert caps.speech_to_speech and caps.full_duplex and caps.barge_in
    assert caps.tool_calling  # through client delegation, ADR
    assert caps.transports == (TRANSPORT_WEBRTC,)
    assert caps.end_of_turn == END_OF_TURN_SEMANTIC
    assert caps.ephemeral_credentials  # a Cloud Core ticket, never the vendor key
    # Turkish is a candidate language but UNMEASURED - said in the declaration itself.
    assert caps.supports_language("tr-TR")
    assert caps.cost_metadata["language_verified"] is False
    assert caps.cost_metadata["usd_per_minute"] == live.USD_PER_MINUTE == 0.05
    # it is eligible for a Turkish conversation when the setting is on
    assert missing_requirements(caps, language="tr-TR", require_ephemeral_credentials=True) == ()
    cred = p.mint_credential(session_id=SESSION_ID, ttl_s=60, transport=TRANSPORT_WEBRTC)
    d = cred.transport_descriptor
    assert d["dialect"] == "openai-live"
    assert d["data_channel"] == "oai-events"
    assert d["sdp_exchange_url"] == f"/v1/voice/realtime/sessions/{SESSION_ID}/live-sdp"
    assert d["ticket_header"] == live.TICKET_HEADER
    assert "api.openai.com" not in json.dumps(d)  # the browser never talks to the vendor first


def test_mint_credential_makes_no_vendor_call_and_never_returns_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("mint_credential must not call the vendor")

    monkeypatch.setattr(live, "_send", boom)
    cred = _provider().mint_credential(session_id=SESSION_ID, ttl_s=60, transport="webrtc")
    assert cred.secret and KEY not in cred.secret
    assert KEY not in json.dumps(cred.to_client_dict())
    assert cred.expires_at <= datetime.now(UTC) + timedelta(seconds=61)
    assert KEY not in repr(_provider())


# ------------------------------------------------------------ (b) documented request


def test_session_request_is_the_documented_live_sessions_shape() -> None:
    cfg = RealtimeSessionConfig(instructions="Türkçe konuş.", voice=None)
    req = _provider().build_session_request(sdp_offer=OFFER, session_config=cfg)
    assert isinstance(req, ProviderRequest)
    assert req.method == "POST"
    assert req.url == "https://api.openai.com/v1/live/sessions"
    assert req.headers["Authorization"] == f"Bearer {KEY}"
    assert req.headers["Content-Type"] == "application/json"
    assert req.json_body == {
        "session": {
            "model": "gpt-live-1",
            "instructions": "Türkçe konuş.",
            "audio": {"output": {"voice": "marin"}},
            "delegation": {"type": "client"},
        },
        "transport": {"type": "webrtc", "sdp": OFFER},
    }
    # the key is only in the header
    assert KEY not in json.dumps(req.json_body)


def test_exchange_sdp_returns_the_answer_and_is_single_use(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[ProviderRequest] = []

    def fake_send(req: ProviderRequest, **_k: Any) -> _Resp:
        sent.append(req)
        return _Resp({"session": {"id": "live_123"}, "transport": {"type": "webrtc", "sdp": "A"}})

    monkeypatch.setattr(live, "_send", fake_send)
    p = _provider()
    cred = p.mint_credential(
        session_id=SESSION_ID,
        ttl_s=60,
        transport="webrtc",
        session_config=RealtimeSessionConfig(instructions="kişilik"),
    )
    answer = p.exchange_sdp(session_id=SESSION_ID, ticket=cred.secret, sdp_offer=OFFER)
    assert answer.sdp_answer == "A"
    assert answer.session_ref == "live_123"
    # the persona Cloud Core minted with is what reaches the vendor, not the browser's
    assert sent[0].json_body["session"]["instructions"] == "kişilik"
    with pytest.raises(VoiceError) as again:
        p.exchange_sdp(session_id=SESSION_ID, ticket=cred.secret, sdp_offer=OFFER)
    assert again.value.error_class == VoiceErrorClass.VALIDATION_ERROR
    assert len(sent) == 1


def test_exchange_sdp_refuses_a_ticket_for_another_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live, "_send", lambda *_a, **_k: pytest.fail("no vendor call"))
    p = _provider()
    cred = p.mint_credential(session_id=SESSION_ID, ttl_s=60, transport="webrtc")
    with pytest.raises(VoiceError):
        p.exchange_sdp(session_id="other", ticket=cred.secret, sdp_offer=OFFER)
    with pytest.raises(VoiceError):
        p.exchange_sdp(session_id=SESSION_ID, ticket="not-a-ticket", sdp_offer=OFFER)


# ----------------------------------------------------------------- (c) secret discipline


def test_vendor_error_never_carries_the_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def failing_send(req: ProviderRequest, **_k: Any) -> Any:
        # a vendor/transport error that echoes the Authorization header back
        raise VoiceError(
            VoiceErrorClass.ALL_PROVIDERS_FAILED,
            f"HTTP 401 for header {req.headers['Authorization']}",
            provider="openai-live",
            details={"echo": {"authorization": req.headers["Authorization"], "k": KEY}},
        )

    monkeypatch.setattr(live, "_send", failing_send)
    p = _provider()
    cred = p.mint_credential(session_id=SESSION_ID, ttl_s=60, transport="webrtc")
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(VoiceError) as exc:
            p.exchange_sdp(session_id=SESSION_ID, ticket=cred.secret, sdp_offer=OFFER)
    err = exc.value
    assert KEY not in err.message
    assert KEY not in json.dumps(err.details, default=str)
    assert KEY not in json.dumps(err.to_dict(), default=str)
    assert KEY not in caplog.text
    assert live.REDACTED in err.message


def test_without_a_key_the_exchange_is_refused_without_io(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live, "_send", lambda *_a, **_k: pytest.fail("no vendor call"))
    p = live.OpenAILiveProvider(None)
    cred = p.mint_credential(session_id=SESSION_ID, ttl_s=60, transport="webrtc")
    with pytest.raises(VoiceError) as exc:
        p.exchange_sdp(session_id=SESSION_ID, ticket=cred.secret, sdp_offer=OFFER)
    assert exc.value.error_class == VoiceErrorClass.PROVIDER_AUTH_MISSING


# ------------------------------------------------------------------- (d) event mapping


def test_delegation_created_maps_to_a_tool_call_keeping_the_delegation_id() -> None:
    ev = {
        "type": "session.delegation.created",
        "event_id": "event_1",
        "offset_ms": 1000,
        "delegation": {"id": "item_abc", "type": "delegation", "target": "client"},
    }
    (out,) = live.map_server_event(ev, at_ms=42)
    assert out.kind == RT_TOOL_CALL
    assert out.at_ms == 42
    assert out.payload["delegation_id"] == "item_abc"
    assert out.payload["call_id"] == "item_abc"
    assert out.payload["target"] == "client"
    assert out.payload["offset_ms"] == 1000


def test_error_event_maps_and_is_bounded() -> None:
    ev = {
        "type": "error",
        "event_id": "event_error",
        "error": {"type": "invalid_request_error", "code": "x", "message": "m" * 900},
    }
    (out,) = live.map_server_event(ev, at_ms=1)
    assert out.kind == RT_ERROR
    assert out.payload["code"] == "x"
    assert len(out.payload["message"]) == 500


@pytest.mark.parametrize(
    "vendor_type",
    [
        # Realtime names: Live documents NO speech started/stopped or response-audio
        # event on WebRTC; mapping them would be a guess (ADR UNVERIFIED list).
        "input_audio_buffer.speech_started",
        "input_audio_buffer.speech_stopped",
        "response.output_audio.delta",
        "session.started",
        "session.input_transcript.delta",
    ],
)
def test_undocumented_or_non_timing_events_map_to_nothing(vendor_type: str) -> None:
    assert live.map_server_event({"type": vendor_type, "delta": "x"}, at_ms=0) == ()


def test_input_transcript_delta_is_read_verbatim() -> None:
    ev = {"type": "session.input_transcript.delta", "delta": "Merhaba ", "start_ms": 0}
    assert live.input_transcript_delta(ev) == "Merhaba "
    assert live.input_transcript_delta({"type": "error"}) is None


def test_commentary_needs_a_delegation_id() -> None:
    cmd = live.commentary_command("item_abc", "Hava 21 derece.")
    assert cmd == {
        "type": "session.commentary.append",
        "delegation_id": "item_abc",
        "content": "Hava 21 derece.",
    }
    with pytest.raises(VoiceError):
        live.commentary_command("", "sonuç")
