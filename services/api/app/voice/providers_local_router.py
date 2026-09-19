"""The ``local-router`` realtime provider (docs/DECISIONS.md ADR-0173).

A ConversationRealtime provider with NO media leg and NO language model: the browser
transcribes with its own recogniser (Chrome Web Speech, tr-TR) and speaks with its own
``speechSynthesis``; what reaches Cloud Core is text, over the SAME relay every real
provider uses (``POST .../events`` with ``utterance`` events, ``POST .../tool-calls`` for
what the deterministic router resolved). The relay's tool contract, audit and step-up
rules therefore apply unchanged; this adapter's only job is to be a truthful entry in the
provider registry that mints nothing.

Why it can never win the default selection: it declares what it actually is -
``speech_to_speech=False``, ``full_duplex=False``, ``barge_in=False``,
``ephemeral_credentials=False`` - and the selector's hard requirement
(``app/voice/selection.py``) rejects it on every one of those, by capability, never by
name. It is chosen only when a client asks for ``transport="text"`` explicitly
(``RealtimeVoiceRuntime.select(transport=TRANSPORT_TEXT)``), which is the owner's
"Yerel mod" switch and nothing else. The paid path stays the default (ADR-0173).

Free of keys, free of network, always registered: the local mode must work on a
deployment whose vendor key is missing, because that is exactly when the owner reaches
for it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import (
    DEFAULT_LEG_MAX_SECONDS,
    END_OF_TURN_SILENCE,
    INTERRUPT_LATENCY_NA,
    TRANSPORT_TEXT,
    EphemeralCredential,
    ProviderCapabilities,
    RealtimeSessionConfig,
    RealtimeSessionHandle,
)

LOCAL_ROUTER_PROVIDER_NAME = "local-router"


class LocalRouterRealtimeProvider:
    """See the module docstring. Stateless; one instance serves every session."""

    name = LOCAL_ROUTER_PROVIDER_NAME

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name,
            kind="realtime",
            languages=("tr-TR",),
            streaming=False,
            long_form_stability="n/a",
            pronunciation_dict=False,
            voice_selection=False,
            speed_control=False,
            cost_metadata={
                "unit": "audio_minutes",
                "usd_per_min": 0.0,
                "note": "ADR-0173 local mode: browser STT/TTS, deterministic router, no model",
            },
            output_formats=(),
            latency_class="realtime",
            requires_api_key=False,
            # The honest declaration that keeps this out of the default selection: the
            # browser does the listening and the speaking, this adapter does neither.
            speech_to_speech=False,
            full_duplex=False,
            barge_in=False,
            end_of_turn=END_OF_TURN_SILENCE,
            tool_calling=True,
            transports=(TRANSPORT_TEXT,),
            ephemeral_credentials=False,
            input_formats=(),
            interrupt_latency_class=INTERRUPT_LATENCY_NA,
        )

    def leg_max_seconds(self) -> int:
        """No media leg, so no ceiling on one (and the client arms no renewal)."""
        return DEFAULT_LEG_MAX_SECONDS

    def open_session(self, *, language: str = "tr-TR") -> RealtimeSessionHandle:
        """There is no in-process media session to open: audio never reaches the server.

        The benchmark harness (``realtime_bench``) is the one caller, and a provider that
        cannot be benchmarked for audio latency must say so rather than hand back a handle
        that would measure nothing."""
        raise VoiceError(
            VoiceErrorClass.CAPABILITY_MISSING,
            f"provider {self.name!r} has no media session; the browser holds the audio",
            provider=self.name,
            details={"language": language},
        )

    def mint_credential(
        self,
        *,
        session_id: str,
        ttl_s: int,
        transport: str = TRANSPORT_TEXT,
        session_config: RealtimeSessionConfig | None = None,
    ) -> EphemeralCredential:
        """An inert credential: nothing to authenticate against, so no secret is minted.

        ``EphemeralCredential.to_client_dict`` omits an empty secret, so the create
        response carries none - which ``test_voice_local_mode.py`` pins, because a
        credential-shaped string in a mode whose whole point is "no vendor" would be a
        lie a security review could only catch by luck."""
        if transport != TRANSPORT_TEXT:
            raise VoiceError(
                VoiceErrorClass.VALIDATION_ERROR,
                f"provider {self.name!r} offers only transport {TRANSPORT_TEXT!r}",
                provider=self.name,
                details={"transport": transport},
            )
        return EphemeralCredential(
            provider=self.name,
            secret="",
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
            transport=TRANSPORT_TEXT,
            session_ref=f"local:{session_id}",
        )


__all__ = ["LOCAL_ROUTER_PROVIDER_NAME", "LocalRouterRealtimeProvider"]
