"""M12 unit tests: capability vocabulary + pure ConversationRealtime selection."""

from __future__ import annotations

import pytest

from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import (
    END_OF_TURN_SEMANTIC,
    END_OF_TURN_SERVER_VAD,
    END_OF_TURN_SILENCE,
    TRANSPORT_SIMULATED,
    TRANSPORT_WEBRTC,
    TRANSPORT_WEBSOCKET,
    FakeRealtimeProvider,
    FakeSTTProvider,
    FakeTTSProvider,
    ProviderCapabilities,
    RealtimeProvider,
    RealtimeSessionHandle,
)
from app.voice.selection import (
    missing_requirements,
    preferred_transport,
    select_conversation_provider,
)
from app.voice.simulator import SimulatedRealtimeProvider


def realtime_caps(name: str, **overrides) -> ProviderCapabilities:
    base = dict(
        name=name, kind="realtime", languages=("tr-TR", "en-US"), streaming=True,
        long_form_stability="n/a", pronunciation_dict=False, voice_selection=True,
        speed_control=False, cost_metadata={}, output_formats=("pcm16",),
        latency_class="realtime", requires_api_key=True,
        speech_to_speech=True, full_duplex=True, barge_in=True,
        end_of_turn=END_OF_TURN_SERVER_VAD, tool_calling=True,
        transports=(TRANSPORT_WEBSOCKET,), ephemeral_credentials=True,
        input_formats=("pcm16",), interrupt_latency_class="fast",
    )
    base.update(overrides)
    return ProviderCapabilities(**base)


# ------------------------------------------------------- backward compatible


def test_m4_capabilities_keep_working_with_realtime_defaults() -> None:
    for provider in (FakeTTSProvider(), FakeSTTProvider(), FakeRealtimeProvider()):
        caps = provider.capabilities()
        d = caps.to_dict()
        assert d["speech_to_speech"] is False
        assert d["full_duplex"] is False
        assert d["barge_in"] is False
        assert d["end_of_turn"] == END_OF_TURN_SILENCE
        assert d["tool_calling"] is False
        assert d["transports"] == []
        assert d["ephemeral_credentials"] is False
        assert d["input_formats"] == []
        assert d["interrupt_latency_class"] == "n/a"
        assert caps.is_conversation_capable() is False


def test_capability_vocabulary_is_validated() -> None:
    with pytest.raises(ValueError):
        realtime_caps("x", end_of_turn="magic")
    with pytest.raises(ValueError):
        realtime_caps("x", transports=("carrier-pigeon",))
    with pytest.raises(ValueError):
        realtime_caps("x", interrupt_latency_class="instant")


def test_simulator_and_m4_fake_satisfy_the_extended_protocols() -> None:
    sim = SimulatedRealtimeProvider()
    assert isinstance(sim, RealtimeProvider)
    assert isinstance(sim.open_session(), RealtimeSessionHandle)
    fake = FakeRealtimeProvider()
    assert isinstance(fake, RealtimeProvider)
    assert isinstance(fake.open_session(), RealtimeSessionHandle)
    cred = fake.mint_credential(session_id="s1", ttl_s=10)
    assert cred.provider == fake.name and cred.session_ref.endswith("s1")


# ----------------------------------------------------------------- selection


def test_hard_requirements_are_all_enforced() -> None:
    assert missing_requirements(realtime_caps("ok")) == ()
    assert "speech_to_speech" in missing_requirements(realtime_caps("a", speech_to_speech=False))
    assert "full_duplex" in missing_requirements(realtime_caps("b", full_duplex=False))
    assert "barge_in" in missing_requirements(realtime_caps("c", barge_in=False))
    assert "tool_calling" in missing_requirements(realtime_caps("d", tool_calling=False))
    assert "language" in missing_requirements(realtime_caps("e", languages=("en-US",)))
    assert "kind=realtime" in missing_requirements(realtime_caps("f", kind="tts"))
    assert "ephemeral_credentials" in missing_requirements(
        realtime_caps("g", ephemeral_credentials=False), require_ephemeral_credentials=True
    )


def test_tts_and_stt_providers_are_never_conversation_candidates() -> None:
    with pytest.raises(VoiceError) as info:
        select_conversation_provider(
            [FakeTTSProvider().capabilities(), FakeSTTProvider().capabilities()]
        )
    assert info.value.error_class == VoiceErrorClass.CAPABILITY_MISSING
    assert set(info.value.details["rejected"]) == {"fake-tts", "fake-stt"}


def test_semantic_end_of_turn_beats_preference_order() -> None:
    a = realtime_caps("a", end_of_turn=END_OF_TURN_SERVER_VAD)
    b = realtime_caps("b", end_of_turn=END_OF_TURN_SEMANTIC)
    result = select_conversation_provider([a, b], preference_order=["a", "b"])
    assert result.selected.name == "b"
    assert "end_of_turn=semantic" in result.reasons
    assert result.ranked == ("b", "a")


def test_webrtc_beats_websocket_when_end_of_turn_ties() -> None:
    ws = realtime_caps("ws", transports=(TRANSPORT_WEBSOCKET,))
    rtc = realtime_caps("rtc", transports=(TRANSPORT_WEBSOCKET, TRANSPORT_WEBRTC))
    result = select_conversation_provider([ws, rtc], preference_order=["ws", "rtc"])
    assert result.selected.name == "rtc"
    assert result.transport == TRANSPORT_WEBRTC
    assert preferred_transport(ws) == TRANSPORT_WEBSOCKET


def test_preference_order_decides_otherwise_equal_candidates() -> None:
    a = realtime_caps("a")
    b = realtime_caps("b")
    assert select_conversation_provider([a, b], preference_order=["b", "a"]).selected.name == "b"
    assert select_conversation_provider([a, b], preference_order=["a", "b"]).selected.name == "a"
    # Unlisted candidates rank after listed ones; the name is the final tie-break.
    assert select_conversation_provider([b, a], preference_order=[]).selected.name == "a"


def test_rejected_candidates_are_reported_with_reasons() -> None:
    good = realtime_caps("good")
    bad = realtime_caps("bad", barge_in=False, tool_calling=False)
    result = select_conversation_provider([bad, good])
    assert result.selected.name == "good"
    assert result.rejected == {"bad": ("barge_in", "tool_calling")}
    assert result.to_dict()["rejected"]["bad"] == ["barge_in", "tool_calling"]


def test_selection_is_by_capability_not_by_name() -> None:
    """A provider literally named like the preferred one is still rejected
    when it lacks a hard capability."""
    impostor = realtime_caps("openai-realtime", speech_to_speech=False)
    sim = SimulatedRealtimeProvider().capabilities()
    result = select_conversation_provider([impostor, sim], preference_order=["openai-realtime"])
    assert result.selected.name == "simulator"
    assert result.transport == TRANSPORT_SIMULATED
    assert "openai-realtime" in result.rejected


def test_no_transport_declared_is_a_capability_error() -> None:
    with pytest.raises(VoiceError):
        preferred_transport(realtime_caps("none", transports=()))
