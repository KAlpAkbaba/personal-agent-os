"""OpenAI Realtime — the first REAL native speech-to-speech adapter (M12 track B).

One implementation of the ``ConversationRealtime`` capability behind the
provider abstraction in ``app/voice/providers.py`` (ADR-0034 §1, ADR-0038). It
is selected by declared capability, never by name; this module and ``Settings``
are the only places the vendor's model names may appear.

What lives here and what does not (spec §1 "direct media path"):

- **Here**: the capability declaration; ephemeral credential minting
  (``POST {base}/realtime/client_secrets``) with the session configuration Cloud
  Core wants baked in server-side (instructions, tool manifest, semantic VAD,
  audio formats, input transcription); the *transport descriptor* a client needs
  to open the media leg itself; the mapping of vendor server events to
  ``RealtimeSessionEvent``; and the outgoing command shapes for barge-in and
  tool-result submission. All of it is pure and unit-tested without a socket.
- **Not here**: the media leg. Clients (Session Companion / web) open WebRTC or
  WebSocket to the vendor with the ephemeral secret and this adapter's
  descriptor. Cloud Core never carries audio.

Secret discipline: the owner's standing API key is read from ``Settings`` and
used for exactly one thing — the ``Authorization`` header of the credential
mint. It is never returned, never placed in a response, never logged, and every
error raised from the mint path is scrubbed of it before it leaves this module.

Vendor facts and their citations are in
``docs/research/realtime-providers-2026-09.md`` §1; where that survey marks a
field UNVERIFIED (Turkish quality, the exact GA nesting of the audio-format
fields, usable credential lifetime), this module treats it as unverified too and
the opt-in smoke script (``scripts/realtime_smoke.py``) echoes the server's own
session object so the shape is confirmed live before a client is built on it.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.logging import get_logger
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import (
    END_OF_TURN_SEMANTIC,
    INTERRUPT_LATENCY_MEDIUM,
    RT_ERROR,
    RT_RESPONSE_AUDIO,
    RT_RESPONSE_DONE,
    RT_RESPONSE_STARTED,
    RT_SPEECH_STARTED,
    RT_SPEECH_STOPPED,
    RT_TOOL_CALL,
    TRANSPORT_WEBRTC,
    TRANSPORT_WEBSOCKET,
    EphemeralCredential,
    ProviderCapabilities,
    ProviderRequest,
    RealtimeSessionConfig,
    RealtimeSessionEvent,
    _require_key,
    _send,
    cloud_tool_name,
    vendor_tool_name,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.config import Settings

logger = get_logger("app.voice.providers_openai_realtime")

OPENAI_REALTIME_PROVIDER_NAME = "openai-realtime"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
#: The qualified Realtime model (owner decision 2026-09-02); overridable via
#: PAGENTOS_VOICE_REALTIME_OPENAI_MODEL when live model discovery proves a newer one.
DEFAULT_MODEL = "gpt-realtime-2.1"
#: The minimal client_secrets contract is exactly
#:   {"session": {"type": "realtime", "model": ..., "audio": {"output": {"voice": ...}}}}
#: Everything else is a LAYER, added one at a time so a live smoke can say which
#: option the vendor refuses. Order = the order the probe adds them.
SESSION_LAYERS = (
    "expires_after",       # request-level: expires_after.anchor/seconds
    "output_modalities",   # session.output_modalities = ["audio"]
    "audio_formats",       # session.audio.input.format / session.audio.output.format
    "transcription",       # session.audio.input.transcription
    "turn_detection",      # session.audio.input.turn_detection = semantic_vad + interrupt
    "instructions",        # session.instructions (Cloud Core persona)
    "tools",               # session.tools + tool_choice
)
MINIMAL_LAYERS: tuple[str, ...] = ()
FULL_LAYERS: tuple[str, ...] = SESSION_LAYERS

#: WebRTC events data channel — the vendor requires exactly this name.
DATA_CHANNEL_NAME = "oai-events"
#: Wire-dialect name the web client selects its event mapping by (must match
#: apps/web/app/lib/voice/dialects/index.ts REGISTRY).
WEB_CLIENT_DIALECT = "openai-realtime"
#: pcm16 is 16-bit mono little-endian at 24 kHz, the only documented rate.
AUDIO_SAMPLE_RATE_HZ = 24000
AUDIO_FORMAT_PCM16 = "pcm16"
AUDIO_FORMAT_G711_ULAW = "g711_ulaw"
AUDIO_FORMAT_G711_ALAW = "g711_alaw"
SUPPORTED_AUDIO_FORMATS = (AUDIO_FORMAT_PCM16, AUDIO_FORMAT_G711_ULAW, AUDIO_FORMAT_G711_ALAW)
#: semantic_vad eagerness levels; ``auto`` ≡ ``medium`` per the VAD guide.
EAGERNESS_LEVELS = ("low", "medium", "high", "auto")
#: client_secrets ``expires_after.seconds`` bounds (anchor ``created_at`` only).
CREDENTIAL_TTL_MIN_S = 10
CREDENTIAL_TTL_MAX_S = 7200
#: Per-connection ceiling published by the vendor; continuity (spec §7) must
#: reattach before it.
SESSION_MAX_MINUTES = 60
SUPPORTED_TRANSPORTS = (TRANSPORT_WEBRTC, TRANSPORT_WEBSOCKET)
REDACTED = "[redacted]"

# ---- vendor server event names (survey §1.2-§1.5). Both the GA and the
# pre-GA audio-delta names are accepted so a client SDK on either schema maps.
EV_SPEECH_STARTED = "input_audio_buffer.speech_started"
EV_SPEECH_STOPPED = "input_audio_buffer.speech_stopped"
EV_RESPONSE_CREATED = "response.created"
EV_OUTPUT_AUDIO_DELTA = "response.output_audio.delta"
EV_OUTPUT_AUDIO_DELTA_LEGACY = "response.audio.delta"
EV_RESPONSE_DONE = "response.done"
EV_RESPONSE_CANCELLED = "response.cancelled"
EV_FUNCTION_CALL_ARGS_DELTA = "response.function_call_arguments.delta"
EV_FUNCTION_CALL_ARGS_DONE = "response.function_call_arguments.done"
EV_ERROR = "error"
EV_SESSION_CREATED = "session.created"
EV_SESSION_UPDATED = "session.updated"
EV_INPUT_TRANSCRIPT_COMPLETED = "conversation.item.input_audio_transcription.completed"

# ---- vendor client command names (survey §1.3, §1.4).
CMD_RESPONSE_CANCEL = "response.cancel"
CMD_OUTPUT_AUDIO_CLEAR = "output_audio_buffer.clear"
CMD_ITEM_TRUNCATE = "conversation.item.truncate"
CMD_ITEM_CREATE = "conversation.item.create"
CMD_RESPONSE_CREATE = "response.create"
CMD_SESSION_UPDATE = "session.update"

#: Keys that must never survive into anything the adapter returns or logs from
#: a vendor response (the echo of the session object carries the secret too).
_SECRET_KEYS = frozenset({"value", "client_secret", "secret", "api_key", "authorization"})


# ------------------------------------------------------------------ helpers


def _audio_format_object(fmt: str) -> dict[str, Any]:
    """GA session schema audio format object for ``audio.input/output.format``."""
    if fmt == AUDIO_FORMAT_PCM16:
        return {"type": "audio/pcm", "rate": AUDIO_SAMPLE_RATE_HZ}
    if fmt == AUDIO_FORMAT_G711_ULAW:
        return {"type": "audio/pcmu"}
    if fmt == AUDIO_FORMAT_G711_ALAW:
        return {"type": "audio/pcma"}
    raise VoiceError(VoiceErrorClass.VALIDATION_ERROR,
                     f"unsupported audio format {fmt!r}; supported: {SUPPORTED_AUDIO_FORMATS}",
                     provider=OPENAI_REALTIME_PROVIDER_NAME)


def map_tools(manifest: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cloud Core tool manifest entries -> Realtime ``tools`` (function schema).

    ``long_running`` and ``preamble`` are Cloud Core relay concerns (spec §4
    step 3, §6): the provider only needs name/description/parameters."""
    out: list[dict[str, Any]] = []
    for entry in manifest:
        name = str(entry.get("name") or "").strip()
        if not name:
            raise VoiceError(VoiceErrorClass.VALIDATION_ERROR,
                             "tool manifest entry without a name",
                             provider=OPENAI_REALTIME_PROVIDER_NAME)
        params = entry.get("parameters") or {"type": "object", "properties": {}}
        out.append({
            "type": "function",
            "name": vendor_tool_name(name),
            "description": str(entry.get("description") or ""),
            "parameters": params,
        })
    return out


def scrub_secrets(obj: Any) -> Any:
    """Recursively drop secret-shaped keys from a vendor payload (session echo)."""
    if isinstance(obj, dict):
        return {k: scrub_secrets(v) for k, v in obj.items()
                if str(k).lower() not in _SECRET_KEYS}
    if isinstance(obj, list):
        return [scrub_secrets(v) for v in obj]
    return obj


def _clamp_ttl(ttl_s: int) -> int:
    return max(CREDENTIAL_TTL_MIN_S, min(CREDENTIAL_TTL_MAX_S, int(ttl_s)))


def _validate_layers(layers: tuple[str, ...]) -> None:
    unknown = [layer for layer in layers if layer not in SESSION_LAYERS]
    if unknown:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR,
                         f"unknown session layer(s) {unknown}; known: {list(SESSION_LAYERS)}",
                         provider=OPENAI_REALTIME_PROVIDER_NAME)


@dataclass(frozen=True, slots=True)
class MintResult:
    """``mint()`` output: the client credential plus the vendor's scrubbed
    session echo (field-name confirmation for the smoke script; no secret)."""

    credential: EphemeralCredential
    session_echo: dict[str, Any]
    #: The request body as sent (no headers, so no key); what the smoke prints.
    request_body: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------- adapter


class OpenAIRealtimeProvider:
    """``RealtimeProvider`` for OpenAI Realtime (credential + contract only)."""

    name = OPENAI_REALTIME_PROVIDER_NAME

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_MODEL,
        voice: str = "marin",
        eagerness: str = "low",
        transcription_model: str = "gpt-4o-transcribe",
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 15.0,
        audio_format: str = AUDIO_FORMAT_PCM16,
    ) -> None:
        if eagerness not in EAGERNESS_LEVELS:
            raise VoiceError(VoiceErrorClass.VALIDATION_ERROR,
                             f"eagerness must be one of {EAGERNESS_LEVELS}, got {eagerness!r}",
                             provider=self.name)
        _audio_format_object(audio_format)  # validates
        self._api_key = api_key or None
        self._model = model
        self._voice = voice
        self._eagerness = eagerness
        self._transcription_model = transcription_model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._audio_format = audio_format

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAIRealtimeProvider:
        return cls(
            settings.voice_openai_api_key or None,
            model=settings.voice_realtime_openai_model,
            voice=settings.voice_realtime_openai_voice,
            eagerness=settings.voice_realtime_openai_eagerness,
            transcription_model=settings.voice_realtime_openai_transcription_model,
            base_url=settings.voice_realtime_openai_base_url,
            timeout_s=settings.voice_realtime_openai_timeout_s,
        )

    def __repr__(self) -> str:  # never the key
        return (f"OpenAIRealtimeProvider(model={self._model!r}, voice={self._voice!r}, "
                f"eagerness={self._eagerness!r}, "
                f"key={'configured' if self._api_key else 'absent'})")

    @property
    def model(self) -> str:
        return self._model

    @property
    def has_key(self) -> bool:
        return bool(self._api_key)

    # ------------------------------------------------------------ capability

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name,
            kind="realtime",
            # tr-TR is listed because the model is multilingual and the vendor's
            # prompting guide documents a configured default language — but the
            # vendor publishes NO Turkish support/quality statement for the
            # conversational model (survey §1.5). Turkish quality is UNVERIFIED
            # here and is measured on the owner's machine (ADR-0034 §6); this
            # declaration is eligibility, not a quality claim.
            languages=("tr-TR", "en-US"),
            streaming=True,
            long_form_stability="n/a",
            pronunciation_dict=False,
            voice_selection=True,
            speed_control=False,
            cost_metadata={
                "unit": "tokens",
                "usd_per_1m_audio_input": 32.0,
                "usd_per_1m_audio_output": 64.0,
                "usd_per_1m_text_input": 4.0,
                "usd_per_1m_text_output": 16.0,
                "billing": "usage",
                "verified": "2026-09-02 vendor pricing page; per-minute cost is computed "
                            "from real session token usage, not assumed",
            },
            output_formats=SUPPORTED_AUDIO_FORMATS,
            latency_class="realtime",
            requires_api_key=True,
            speech_to_speech=True,
            full_duplex=True,
            barge_in=True,
            end_of_turn=END_OF_TURN_SEMANTIC,  # turn_detection.type = semantic_vad
            tool_calling=True,
            transports=SUPPORTED_TRANSPORTS,  # WebRTC preferred (spec §1); SIP not wired
            ephemeral_credentials=True,
            input_formats=SUPPORTED_AUDIO_FORMATS,
            # The server truncates unplayed audio on interrupt, but the vendor
            # publishes no cancel latency and the perceived stop is client-bound
            # (stop-first, spec §5). "medium" until the harness measures it.
            interrupt_latency_class=INTERRUPT_LATENCY_MEDIUM,
        )

    def open_session(self, *, language: str = "tr-TR") -> Any:
        """The media leg is the CLIENT's (spec §1). Cloud Core never opens it."""
        raise VoiceError(
            VoiceErrorClass.CAPABILITY_MISSING,
            f"{self.name}: the media session is opened by the client with the ephemeral "
            "credential and transport descriptor; Cloud Core does not hold a media leg",
            provider=self.name,
        )

    # ------------------------------------------------------------- contract

    def transport_descriptor(self, transport: str) -> dict[str, Any]:
        """The NON-secret contract a client needs to open the media leg."""
        audio = {
            "format": self._audio_format,
            "sample_rate_hz": AUDIO_SAMPLE_RATE_HZ,
            "channels": 1,
            "sample_width_bits": 16,
            "endianness": "little",
        }
        if transport == TRANSPORT_WEBRTC:
            # Key names are the cross-track contract the web client (track D,
            # apps/web/app/lib/voice/transport.ts TransportDescriptor) reads:
            # sdp_exchange_url + data_channel + dialect are REQUIRED there.
            return {
                "transport": TRANSPORT_WEBRTC,
                "sdp_exchange_url": f"{self._base_url}/realtime/calls",
                "sdp_content_type": "application/sdp",
                "data_channel": DATA_CHANNEL_NAME,
                "dialect": WEB_CLIENT_DIALECT,
                "auth": "bearer_ephemeral_secret",
                "audio": audio,
                "session_max_minutes": SESSION_MAX_MINUTES,
            }
        if transport == TRANSPORT_WEBSOCKET:
            ws_base = self._base_url.replace("https://", "wss://", 1).replace(
                "http://", "ws://", 1)
            return {
                "transport": TRANSPORT_WEBSOCKET,
                "websocket_url": f"{ws_base}/realtime",
                "dialect": WEB_CLIENT_DIALECT,
                "query": {"model": self._model},
                "auth": "bearer_ephemeral_secret",
                "audio": audio,
                "audio_encoding": "base64 in input_audio_buffer.append / "
                                  "response.output_audio.delta",
                "session_max_minutes": SESSION_MAX_MINUTES,
            }
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"{self.name} does not offer transport {transport!r}; offered: {SUPPORTED_TRANSPORTS}",
            provider=self.name,
        )

    def build_session_config(
        self, session_config: RealtimeSessionConfig | None = None,
        *, layers: tuple[str, ...] = FULL_LAYERS,
    ) -> dict[str, Any]:
        """The ``session`` object of the client_secrets request (current GA schema).

        With ``layers=MINIMAL_LAYERS`` this is exactly the minimal contract:
        ``{"type": "realtime", "model": ..., "audio": {"output": {"voice": ...}}}``.
        Each layer adds one option on top (see ``SESSION_LAYERS``); the live
        smoke adds them one at a time so an incompatible option is named, not
        guessed. Full M12 configuration:

        - ``turn_detection`` is ``semantic_vad`` (the reason this adapter declares
          ``end_of_turn=semantic``) with the configured eagerness; ``low`` by
          default so Turkish hesitation ("şey", "yani", "hani") is not chunked
          away (spec §5). ``interrupt_response`` keeps server-side barge-in on.
        - Input transcription is enabled so the client can report utterances
          to Cloud Core for intent resolution (spec §5) and the benchmark.
        - Cloud Core's persona ``instructions`` and tool manifest are baked in
          here, server-side, so a client cannot substitute its own.
        """
        _validate_layers(layers)
        cfg = session_config or RealtimeSessionConfig()
        language_hint = cfg.language.split("-")[0].lower() if cfg.language else "tr"
        session: dict[str, Any] = {
            "type": "realtime",
            "model": self._model,
            "audio": {"output": {"voice": cfg.voice or self._voice}},
        }
        if "output_modalities" in layers:
            session["output_modalities"] = ["audio"]
        if "audio_formats" in layers:
            fmt = _audio_format_object(self._audio_format)
            session["audio"].setdefault("input", {})["format"] = dict(fmt)
            session["audio"]["output"]["format"] = dict(fmt)
        if "transcription" in layers:
            session["audio"].setdefault("input", {})["transcription"] = {
                "model": self._transcription_model,
                "language": language_hint,
            }
        if "turn_detection" in layers:
            session["audio"].setdefault("input", {})["turn_detection"] = {
                "type": "semantic_vad",
                "eagerness": self._eagerness,
                "create_response": True,
                "interrupt_response": True,
            }
        if "instructions" in layers and cfg.instructions:
            session["instructions"] = cfg.instructions
        if "tools" in layers:
            tools = map_tools(cfg.tools)
            if tools:
                session["tools"] = tools
                session["tool_choice"] = "auto"
        return session

    def build_credential_request(
        self, *, session_id: str, ttl_s: int, transport: str,
        session_config: RealtimeSessionConfig | None = None,
        layers: tuple[str, ...] = FULL_LAYERS,
    ) -> ProviderRequest:
        """Pure request construction (no I/O) — asserted exactly by unit tests."""
        if transport not in SUPPORTED_TRANSPORTS:
            self.transport_descriptor(transport)  # raises the typed error
        _validate_layers(layers)
        body: dict[str, Any] = {}
        if "expires_after" in layers:
            body["expires_after"] = {"anchor": "created_at", "seconds": _clamp_ttl(ttl_s)}
        body["session"] = self.build_session_config(session_config, layers=layers)
        return ProviderRequest(
            method="POST",
            url=f"{self._base_url}/realtime/client_secrets",
            headers={
                "Authorization": f"Bearer {self._api_key or ''}",
                "Content-Type": "application/json",
                # Owner-side correlation only; opaque to the vendor, non-secret.
                "X-PagentOS-Session": session_id,
            },
            json_body=body,
        )

    def build_models_request(self) -> ProviderRequest:
        """``GET /models`` - live model discovery for the smoke (ids only)."""
        return ProviderRequest(
            method="GET",
            url=f"{self._base_url}/models",
            headers={"Authorization": f"Bearer {self._api_key or ''}"},
        )

    def list_realtime_models(self) -> list[str]:
        """Ids of the account's models whose id mentions ``realtime``, sorted.
        One real call; nothing but ids is returned or logged."""
        _require_key(self._api_key, self.name)
        try:
            payload = _send(self.build_models_request(), timeout_s=self._timeout_s,
                            provider=self.name).json()
        except VoiceError as exc:
            raise self._scrubbed(exc) from None
        except ValueError:
            return []
        data = payload.get("data") if isinstance(payload, dict) else None
        ids = [str(m.get("id")) for m in (data or []) if isinstance(m, dict) and m.get("id")]
        return sorted(i for i in ids if "realtime" in i.lower())

    # ---------------------------------------------------------------- mint

    def _scrub(self, text: str) -> str:
        if self._api_key and self._api_key in text:
            return text.replace(self._api_key, REDACTED)
        return text

    def _scrubbed(self, exc: VoiceError) -> VoiceError:
        return VoiceError(
            exc.error_class, self._scrub(exc.message), provider=self.name,
            retryable=exc.retryable,
            details=json.loads(self._scrub(json.dumps(exc.details, default=str))),
        )

    def mint(
        self, *, session_id: str, ttl_s: int, transport: str,
        session_config: RealtimeSessionConfig | None = None,
        layers: tuple[str, ...] = FULL_LAYERS,
    ) -> MintResult:
        """Mint one single-session ephemeral credential through the vendor.

        Inert without a key (``PROVIDER_AUTH_MISSING``, no I/O). Every failure
        is re-raised with the standing key scrubbed from message and details."""
        _require_key(self._api_key, self.name)
        req = self.build_credential_request(
            session_id=session_id, ttl_s=ttl_s, transport=transport,
            session_config=session_config, layers=layers,
        )
        try:
            resp = _send(req, timeout_s=self._timeout_s, provider=self.name)
            payload = resp.json()
        except VoiceError as exc:
            raise self._scrubbed(exc) from None
        except ValueError as exc:  # non-JSON body
            raise VoiceError(
                VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
                f"{self.name}: client_secrets response was not JSON ({type(exc).__name__})",
                provider=self.name, retryable=True,
            ) from None
        if not isinstance(payload, dict):
            raise VoiceError(VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
                             f"{self.name}: client_secrets response was not an object",
                             provider=self.name, retryable=True)
        value = payload.get("value")
        if not isinstance(value, str) or not value:
            raise VoiceError(VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
                             f"{self.name}: client_secrets response carried no credential",
                             provider=self.name, retryable=True)
        expires_raw = payload.get("expires_at")
        if isinstance(expires_raw, int | float) and expires_raw > 0:
            expires_at = datetime.fromtimestamp(float(expires_raw), tz=UTC)
        else:
            expires_at = datetime.now(UTC) + timedelta(seconds=_clamp_ttl(ttl_s))
        session_obj = payload.get("session") if isinstance(payload.get("session"), dict) else {}
        session_ref = str(session_obj.get("id") or f"openai:{session_id}")
        credential = EphemeralCredential(
            provider=self.name,
            secret=value,
            expires_at=expires_at,
            transport=transport,
            session_ref=session_ref,
            transport_descriptor=self.transport_descriptor(transport),
        )
        logger.info(
            "openai_realtime_credential_minted",
            session_id=session_id, session_ref=session_ref, transport=transport,
            expires_at=expires_at.isoformat(), ttl_requested_s=_clamp_ttl(ttl_s),
        )
        return MintResult(credential=credential, session_echo=scrub_secrets(session_obj),
                          request_body=req.json_body or {})

    def mint_credential(
        self, *, session_id: str, ttl_s: int, transport: str = TRANSPORT_WEBRTC,
        session_config: RealtimeSessionConfig | None = None,
    ) -> EphemeralCredential:
        return self.mint(session_id=session_id, ttl_s=ttl_s, transport=transport,
                         session_config=session_config).credential


# ------------------------------------------------------- event mapping (in)


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = raw if isinstance(raw, str) else ""
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {"_raw": text[:2000], "_parse_error": True}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _b64_len(delta: Any) -> int:
    if not isinstance(delta, str) or not delta:
        return 0
    try:
        return len(base64.b64decode(delta, validate=False))
    except (binascii.Error, ValueError):
        return 0


def map_server_event(event: dict[str, Any], *, at_ms: int) -> tuple[RealtimeSessionEvent, ...]:
    """Vendor server event -> zero or more ``RealtimeSessionEvent``.

    Deterministic and duplicate-free by construction: a tool call is emitted
    from ``response.function_call_arguments.done`` only (``response.done``
    merely lists the call ids it closed), so a client relaying to Cloud Core's
    idempotent ``/tool-calls`` never submits the same call twice from one
    stream. Audio deltas carry a byte count, never the audio (providers.py).
    Unknown or purely incremental events map to ``()``."""
    kind = event.get("type")
    if kind == EV_SPEECH_STARTED:
        return (RealtimeSessionEvent(RT_SPEECH_STARTED, at_ms, {
            "item_id": event.get("item_id"), "audio_start_ms": event.get("audio_start_ms"),
        }),)
    if kind == EV_SPEECH_STOPPED:
        return (RealtimeSessionEvent(RT_SPEECH_STOPPED, at_ms, {
            "item_id": event.get("item_id"), "audio_end_ms": event.get("audio_end_ms"),
        }),)
    if kind == EV_RESPONSE_CREATED:
        resp = event.get("response") or {}
        return (RealtimeSessionEvent(RT_RESPONSE_STARTED, at_ms,
                                     {"response_id": resp.get("id")}),)
    if kind in (EV_OUTPUT_AUDIO_DELTA, EV_OUTPUT_AUDIO_DELTA_LEGACY):
        return (RealtimeSessionEvent(RT_RESPONSE_AUDIO, at_ms, {
            "response_id": event.get("response_id"), "item_id": event.get("item_id"),
            "bytes": _b64_len(event.get("delta")),
        }),)
    if kind == EV_RESPONSE_DONE:
        resp = event.get("response") or {}
        status = resp.get("status")
        call_ids = [item.get("call_id") for item in (resp.get("output") or [])
                    if isinstance(item, dict) and item.get("type") == "function_call"]
        return (RealtimeSessionEvent(RT_RESPONSE_DONE, at_ms, {
            "response_id": resp.get("id"), "cancelled": status == "cancelled",
            "status": status, "tool_call_ids": call_ids,
        }),)
    if kind == EV_RESPONSE_CANCELLED:
        resp = event.get("response") or {}
        return (RealtimeSessionEvent(RT_RESPONSE_DONE, at_ms, {
            "response_id": resp.get("id") or event.get("response_id"),
            "cancelled": True, "status": "cancelled", "tool_call_ids": [],
        }),)
    if kind == EV_FUNCTION_CALL_ARGS_DONE:
        raw_name = event.get("name")
        return (RealtimeSessionEvent(RT_TOOL_CALL, at_ms, {
            "call_id": event.get("call_id"),
            "name": cloud_tool_name(raw_name) if isinstance(raw_name, str) else raw_name,
            "arguments": _parse_arguments(event.get("arguments")),
            "response_id": event.get("response_id"), "item_id": event.get("item_id"),
        }),)
    if kind == EV_ERROR:
        err = event.get("error") or {}
        return (RealtimeSessionEvent(RT_ERROR, at_ms, {
            "type": err.get("type"), "code": err.get("code"),
            "message": str(err.get("message") or "")[:500], "event_id": err.get("event_id"),
        }),)
    return ()


def input_transcript(event: dict[str, Any]) -> str | None:
    """The owner's final utterance text from an input-transcription event, for
    the client to report as an ``utterance`` (Cloud Core resolves intents from
    it — spec §5). ``None`` for any other event."""
    if event.get("type") != EV_INPUT_TRANSCRIPT_COMPLETED:
        return None
    transcript = event.get("transcript")
    return transcript if isinstance(transcript, str) else None


# ---------------------------------------------------- outgoing commands (out)


def barge_in_commands(
    *, transport: str, item_id: str | None = None, content_index: int = 0,
    audio_end_ms: int | None = None,
) -> tuple[dict[str, Any], ...]:
    """The provider-side half of barge-in (spec §5: the client stops LOCAL
    playback first, then sends these in order):

    1. ``response.cancel`` — stop the in-flight response;
    2. ``output_audio_buffer.clear`` — WebRTC only (the server-managed buffer);
    3. ``conversation.item.truncate`` — drop the unheard tail from history so the
       model's context matches what the owner actually heard. Required on
       WebSocket (no server buffer); harmless on WebRTC. Emitted when the
       client knows the item and how much was played.
    """
    if transport not in SUPPORTED_TRANSPORTS:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR,
                         f"unsupported transport {transport!r}",
                         provider=OPENAI_REALTIME_PROVIDER_NAME)
    commands: list[dict[str, Any]] = [{"type": CMD_RESPONSE_CANCEL}]
    if transport == TRANSPORT_WEBRTC:
        commands.append({"type": CMD_OUTPUT_AUDIO_CLEAR})
    if item_id is not None and audio_end_ms is not None:
        commands.append({
            "type": CMD_ITEM_TRUNCATE, "item_id": item_id,
            "content_index": int(content_index), "audio_end_ms": max(0, int(audio_end_ms)),
        })
    return tuple(commands)


def tool_result_commands(call_id: str, result: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Submit a Cloud Core tool result and resume generation (survey §1.4):
    ``conversation.item.create(function_call_output)`` then ``response.create``.
    The output is a JSON string with Turkish characters preserved."""
    if not call_id:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "call_id must be non-empty",
                         provider=OPENAI_REALTIME_PROVIDER_NAME)
    return (
        {
            "type": CMD_ITEM_CREATE,
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result, ensure_ascii=False, default=str),
            },
        },
        {"type": CMD_RESPONSE_CREATE},
    )


def say_command(text: str) -> dict[str, Any]:
    """Have the model speak a short phrase Cloud Core wants said (sideband
    ``say`` / a long-running tool's Turkish preamble, spec §6) as one audio
    response. The survey found no vendor primitive for "keep talking while a
    tool runs", so the preamble is an explicit, client-driven response."""
    phrase = (text or "").strip()
    if not phrase:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "say text must be non-empty",
                         provider=OPENAI_REALTIME_PROVIDER_NAME)
    return {
        "type": CMD_RESPONSE_CREATE,
        "response": {
            "output_modalities": ["audio"],
            "instructions": f"Şu cümleyi aynen, başka bir şey eklemeden söyle: {phrase}",
        },
    }


def session_update_command(session: dict[str, Any]) -> dict[str, Any]:
    """``session.update`` with a (partial) session object — for a client that
    must re-apply the minted configuration after a reconnect."""
    return {"type": CMD_SESSION_UPDATE, "session": dict(session)}


def command_types(commands: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(str(c.get("type")) for c in commands)


__all__ = [
    "AUDIO_SAMPLE_RATE_HZ",
    "CMD_ITEM_CREATE",
    "CMD_ITEM_TRUNCATE",
    "CMD_OUTPUT_AUDIO_CLEAR",
    "CMD_RESPONSE_CANCEL",
    "CMD_RESPONSE_CREATE",
    "CMD_SESSION_UPDATE",
    "CREDENTIAL_TTL_MAX_S",
    "CREDENTIAL_TTL_MIN_S",
    "DATA_CHANNEL_NAME",
    "DEFAULT_BASE_URL",
    "EAGERNESS_LEVELS",
    "EV_ERROR",
    "EV_FUNCTION_CALL_ARGS_DELTA",
    "EV_FUNCTION_CALL_ARGS_DONE",
    "EV_INPUT_TRANSCRIPT_COMPLETED",
    "EV_OUTPUT_AUDIO_DELTA",
    "EV_OUTPUT_AUDIO_DELTA_LEGACY",
    "EV_RESPONSE_CANCELLED",
    "EV_RESPONSE_CREATED",
    "EV_RESPONSE_DONE",
    "EV_SESSION_CREATED",
    "EV_SESSION_UPDATED",
    "EV_SPEECH_STARTED",
    "EV_SPEECH_STOPPED",
    "OPENAI_REALTIME_PROVIDER_NAME",
    "SESSION_MAX_MINUTES",
    "SUPPORTED_AUDIO_FORMATS",
    "SUPPORTED_TRANSPORTS",
    "MintResult",
    "OpenAIRealtimeProvider",
    "barge_in_commands",
    "command_types",
    "input_transcript",
    "map_server_event",
    "map_tools",
    "say_command",
    "scrub_secrets",
    "session_update_command",
    "tool_result_commands",
]
