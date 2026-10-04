"""OpenAI GPT-Live — a SECOND ``ConversationRealtime`` candidate, for MEASUREMENT only.

gpt-live-provider (d20261004, team/plans/gpt-live-provider-adr.md). Behind
``PAGENTOS_VOICE_REALTIME_OPENAI_LIVE_ENABLED`` (off by default) and not in the default
preference order: a session gets it only by asking (``prefer_provider: "openai-live"``).
Adopting it is a separate decision. Same contract as ``providers_openai_realtime.py``
(ADR-0034): selected by declared capability, this module and ``Settings`` are the only
places the vendor's model name appears.

How Live differs from Realtime, from the vendor's own pages (read 2026-10-04):

- There is NO documented ephemeral client key for Live. The documented WebRTC path is:
  the browser makes an SDP offer, *the application server* sends it with the project key
  to ``POST /v1/live/sessions`` (JSON ``{"session": ..., "transport": {"type": "webrtc",
  "sdp": <offer>}}``) and hands the answer (``transport.sdp``) back. So
  ``mint_credential`` calls nobody: it issues a short-lived, single-use Cloud Core
  *ticket* bound to one session, and the descriptor points the browser at Cloud Core's
  own exchange route (``/v1/voice/realtime/sessions/{id}/live-sdp``), which calls
  :meth:`OpenAILiveProvider.exchange_sdp`. The vendor key never reaches the browser.
- Tools are not function tools: Live hands work off as a *client delegation*
  (``session.delegation.created`` with ``delegation.id``); the result goes back as
  ``session.commentary.append`` with the SAME ``delegation_id``. The delegation event
  carries no task text (the transcript deltas do).
- Live documents no speech-started/stopped or response-audio event on WebRTC (audio is on
  the media tracks). Those are NOT guessed here: the timing marks come from the client,
  exactly like the benchmark's client source.

Secret discipline (as in the Realtime adapter): the standing key is read from
``Settings`` and used for exactly one thing — the ``Authorization`` header of the session
request. It is never returned, never logged, and every error leaving this module is
scrubbed of it.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.logging import get_logger
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import (
    END_OF_TURN_SEMANTIC,
    INTERRUPT_LATENCY_MEDIUM,
    RT_ERROR,
    RT_TOOL_CALL,
    TRANSPORT_WEBRTC,
    EphemeralCredential,
    ProviderCapabilities,
    ProviderRequest,
    RealtimeSessionConfig,
    RealtimeSessionEvent,
    _require_key,
    _send,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.config import Settings

logger = get_logger("app.voice.providers_openai_live")

OPENAI_LIVE_PROVIDER_NAME = "openai-live"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-live-1"
#: "Voice sessions cost $0.05 per minute, billed per second. Backend model and tool
#: usage is billed separately" (models/gpt-live-1, read 2026-10-04).
USD_PER_MINUTE = 0.05
PRICE_VERIFIED = "2026-10-04 developers.openai.com/api/docs/models/gpt-live-1"
#: The data-channel label in the vendor's WebRTC example.
DATA_CHANNEL_NAME = "oai-events"
#: Wire-dialect name the web client selects its event mapping by (gpt-live-web-bridge).
WEB_CLIENT_DIALECT = "openai-live"
#: Voices listed on guides/live-conversations (2026-10-04); ``marin`` is the default.
#: Their Turkish pronunciation is UNVERIFIED.
DEFAULT_VOICE = "marin"
SUPPORTED_VOICES = (
    "marin",
    "quartz",
    "ripple",
    "vesper",
    "willow",
    "stone",
    "gleam",
    "meridian",
    "bossa",
    "tempo",
    "beacon",
    "delta",
    "cinder",
)
SUPPORTED_VOICES_DISCOVERED = "2026-10-04"
SUPPORTED_TRANSPORTS = (TRANSPORT_WEBRTC,)
#: Cloud Core's own SDP exchange route (routes.py); relative to the API base.
SDP_EXCHANGE_PATH = "/v1/voice/realtime/sessions/{session_id}/live-sdp"
#: The ticket travels in its own header: ``Authorization`` already carries the owner
#: session on that route.
TICKET_HEADER = "X-PagentOS-Live-Ticket"
TICKET_TTL_MIN_S = 10
TICKET_TTL_MAX_S = 600
MAX_SDP_BYTES = 64 * 1024
REDACTED = "[redacted]"

# ---- documented vendor server events (guides/live-conversations, live-delegation).
EV_SESSION_STARTED = "session.started"
EV_SESSION_CLOSED = "session.closed"
EV_DELEGATION_CREATED = "session.delegation.created"
EV_INPUT_TRANSCRIPT_DELTA = "session.input_transcript.delta"
EV_OUTPUT_TRANSCRIPT_DELTA = "session.output_transcript.delta"
EV_USAGE_UPDATED = "session.usage.updated"
EV_ERROR = "error"

# ---- documented client commands.
CMD_COMMENTARY_APPEND = "session.commentary.append"
CMD_SESSION_CLOSE = "session.close"

_SECRET_KEYS = frozenset({"value", "client_secret", "secret", "api_key", "authorization"})


def scrub_secrets(obj: Any) -> Any:
    """Recursively drop secret-shaped keys from a vendor payload."""
    if isinstance(obj, dict):
        return {k: scrub_secrets(v) for k, v in obj.items() if str(k).lower() not in _SECRET_KEYS}
    if isinstance(obj, list):
        return [scrub_secrets(v) for v in obj]
    return obj


def _clamp_ttl(ttl_s: int) -> int:
    return max(TICKET_TTL_MIN_S, min(TICKET_TTL_MAX_S, int(ttl_s)))


@dataclass(slots=True)
class _Ticket:
    session_id: str
    expires_at: datetime
    session_config: RealtimeSessionConfig | None


@dataclass(frozen=True, slots=True)
class LiveSessionAnswer:
    """``exchange_sdp`` output: the vendor's SDP answer and its opaque session id, plus
    the request body as sent (no headers, so no key) and the scrubbed response echo."""

    session_ref: str
    sdp_answer: str
    request_body: dict[str, Any] = field(default_factory=dict)
    response_echo: dict[str, Any] = field(default_factory=dict)


class OpenAILiveProvider:
    """``RealtimeProvider`` for OpenAI GPT-Live (ticket + SDP exchange + contract)."""

    name = OPENAI_LIVE_PROVIDER_NAME

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_MODEL,
        voice: str = DEFAULT_VOICE,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 15.0,
    ) -> None:
        self._api_key = api_key or None
        self._model = model
        self._voice = voice if voice in SUPPORTED_VOICES else DEFAULT_VOICE
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._tickets: dict[str, _Ticket] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAILiveProvider:
        return cls(
            settings.voice_openai_api_key or None,
            model=settings.voice_realtime_openai_live_model,
            base_url=settings.voice_realtime_openai_base_url,
            timeout_s=settings.voice_realtime_openai_timeout_s,
        )

    def __repr__(self) -> str:  # never the key
        return (
            f"OpenAILiveProvider(model={self._model!r}, voice={self._voice!r}, "
            f"key={'configured' if self._api_key else 'absent'})"
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def has_key(self) -> bool:
        return bool(self._api_key)

    def supported_voices(self) -> tuple[str, ...]:
        return SUPPORTED_VOICES

    def require_supported_voice(self, voice: str) -> str:
        if voice not in SUPPORTED_VOICES:
            raise VoiceError(
                VoiceErrorClass.VALIDATION_ERROR,
                f"voice {voice!r} is not offered by {self.name} "
                f"(listed {SUPPORTED_VOICES_DISCOVERED}): {list(SUPPORTED_VOICES)}",
                provider=self.name,
                details={"supported_voices": list(SUPPORTED_VOICES)},
            )
        return voice

    # ------------------------------------------------------------ capability

    def leg_max_seconds(self) -> int:
        # The Live pages publish no per-connection ceiling (UNVERIFIED); 0 = none known.
        return 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name,
            kind="realtime",
            # tr-TR makes it a CANDIDATE so it can be measured at all; no Live page
            # states Turkish support (UNVERIFIED, ADR). Eligibility, not a quality claim:
            # `language_verified: False` below says so to every reader of /providers.
            languages=("tr-TR", "en-US"),
            streaming=True,
            long_form_stability="n/a",
            pronunciation_dict=False,
            voice_selection=True,
            speed_control=False,
            cost_metadata={
                "unit": "minute",
                "usd_per_minute": USD_PER_MINUTE,
                "billing": "per second of voice session; backend model/tool usage separate",
                "verified": PRICE_VERIFIED,
                "language_verified": False,
            },
            output_formats=("webrtc-negotiated",),
            latency_class="realtime",
            requires_api_key=True,
            speech_to_speech=True,
            full_duplex=True,
            barge_in=True,
            # Turn-taking is the model's own (full duplex, no VAD knob is documented):
            # the closest declared mode is semantic. ADR records the reasoning.
            end_of_turn=END_OF_TURN_SEMANTIC,
            # Through client delegation (session.delegation.created ->
            # session.commentary.append with the same delegation_id), not function tools.
            tool_calling=True,
            transports=SUPPORTED_TRANSPORTS,
            # A Cloud Core ticket: single use, one session, minutes; never the vendor key.
            ephemeral_credentials=True,
            input_formats=("webrtc-negotiated",),
            interrupt_latency_class=INTERRUPT_LATENCY_MEDIUM,
        )

    def open_session(self, *, language: str = "tr-TR") -> Any:
        raise VoiceError(
            VoiceErrorClass.CAPABILITY_MISSING,
            f"{self.name}: the media session is the client's; Cloud Core only exchanges "
            "its SDP offer (live-sdp route) and never holds a media leg",
            provider=self.name,
        )

    # ------------------------------------------------------------- contract

    def transport_descriptor(self, transport: str, *, session_id: str) -> dict[str, Any]:
        if transport != TRANSPORT_WEBRTC:
            raise VoiceError(
                VoiceErrorClass.VALIDATION_ERROR,
                f"{self.name} does not offer transport {transport!r}; "
                f"offered: {SUPPORTED_TRANSPORTS}",
                provider=self.name,
            )
        return {
            "transport": TRANSPORT_WEBRTC,
            "sdp_exchange_url": SDP_EXCHANGE_PATH.format(session_id=session_id),
            "sdp_content_type": "application/sdp",
            "data_channel": DATA_CHANNEL_NAME,
            "dialect": WEB_CLIENT_DIALECT,
            "auth": "pagentos_live_ticket",
            "ticket_header": TICKET_HEADER,
            # WebRTC negotiates the audio format through SDP (the vendor: omit audio.format).
            "audio": {"format": "webrtc-negotiated"},
        }

    def build_session_config(self, session_config: RealtimeSessionConfig | None) -> dict[str, Any]:
        """The documented ``session`` object: model, instructions, voice, client delegation.
        No ``audio.format`` (WebRTC) and no language field (none is documented)."""
        cfg = session_config or RealtimeSessionConfig()
        session: dict[str, Any] = {"model": self._model}
        if cfg.instructions:
            session["instructions"] = cfg.instructions
        voice = cfg.voice if cfg.voice in SUPPORTED_VOICES else self._voice
        session["audio"] = {"output": {"voice": voice}}
        session["delegation"] = {"type": "client"}
        return session

    def build_session_request(
        self, *, sdp_offer: str, session_config: RealtimeSessionConfig | None = None
    ) -> ProviderRequest:
        """Pure request construction (no I/O) — asserted exactly by unit tests."""
        return ProviderRequest(
            method="POST",
            url=f"{self._base_url}/live/sessions",
            headers={
                "Authorization": f"Bearer {self._api_key or ''}",
                "Content-Type": "application/json",
            },
            json_body={
                "session": self.build_session_config(session_config),
                "transport": {"type": TRANSPORT_WEBRTC, "sdp": sdp_offer},
            },
        )

    # ---------------------------------------------------------- ticket (mint)

    def mint_credential(
        self,
        *,
        session_id: str,
        ttl_s: int,
        transport: str = TRANSPORT_WEBRTC,
        session_config: RealtimeSessionConfig | None = None,
    ) -> EphemeralCredential:
        """No vendor call: a single-use Cloud Core ticket for this session's SDP exchange.
        The session configuration Cloud Core built is kept with it server-side, so the
        browser cannot present another persona."""
        descriptor = self.transport_descriptor(transport, session_id=session_id)
        ticket = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=_clamp_ttl(ttl_s))
        with self._lock:
            now = datetime.now(UTC)
            for key in [k for k, t in self._tickets.items() if t.expires_at <= now]:
                del self._tickets[key]
            self._tickets[ticket] = _Ticket(session_id, expires_at, session_config)
        return EphemeralCredential(
            provider=self.name,
            secret=ticket,
            expires_at=expires_at,
            transport=transport,
            session_ref=f"pagentos-live:{session_id}",
            transport_descriptor=descriptor,
        )

    def _redeem(self, session_id: str, ticket: str) -> _Ticket:
        with self._lock:
            found: _Ticket | None = None
            for key, entry in self._tickets.items():
                if hmac.compare_digest(key.encode(), (ticket or "").encode()):
                    found = entry
                    del self._tickets[key]  # single use, whatever happens next
                    break
        if found is None or found.session_id != session_id or found.expires_at <= datetime.now(UTC):
            raise VoiceError(
                VoiceErrorClass.VALIDATION_ERROR,
                f"{self.name}: SDP exchange ticket is unknown, used, expired or for "
                "another session",
                provider=self.name,
            )
        return found

    # ------------------------------------------------------------ exchange

    def _scrub(self, text: str) -> str:
        if self._api_key and self._api_key in text:
            return text.replace(self._api_key, REDACTED)
        return text

    def _scrubbed(self, exc: VoiceError) -> VoiceError:
        return VoiceError(
            exc.error_class,
            self._scrub(exc.message),
            provider=self.name,
            retryable=exc.retryable,
            details=json.loads(self._scrub(json.dumps(scrub_secrets(exc.details), default=str))),
        )

    def exchange_sdp(self, *, session_id: str, ticket: str, sdp_offer: str) -> LiveSessionAnswer:
        """Redeem the ticket, then open the vendor session with the browser's offer."""
        entry = self._redeem(session_id, ticket)
        return self.open_live_session(
            sdp_offer=sdp_offer, session_config=entry.session_config, session_id=session_id
        )

    def open_live_session(
        self,
        *,
        sdp_offer: str,
        session_config: RealtimeSessionConfig | None = None,
        session_id: str = "",
    ) -> LiveSessionAnswer:
        """``POST /v1/live/sessions`` (one real call; the smoke script's step too).
        Inert without a key (``PROVIDER_AUTH_MISSING``, no I/O)."""
        _require_key(self._api_key, self.name)
        if not sdp_offer.strip() or len(sdp_offer.encode("utf-8")) > MAX_SDP_BYTES:
            raise VoiceError(
                VoiceErrorClass.VALIDATION_ERROR,
                f"{self.name}: SDP offer is empty or larger than {MAX_SDP_BYTES} bytes",
                provider=self.name,
            )
        req = self.build_session_request(sdp_offer=sdp_offer, session_config=session_config)
        try:
            payload = _send(req, timeout_s=self._timeout_s, provider=self.name).json()
        except VoiceError as exc:
            raise self._scrubbed(exc) from None
        except ValueError as exc:
            raise VoiceError(
                VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
                f"{self.name}: live/sessions response was not JSON ({type(exc).__name__})",
                provider=self.name,
                retryable=True,
            ) from None
        session_obj = payload.get("session") if isinstance(payload, dict) else None
        transport_obj = payload.get("transport") if isinstance(payload, dict) else None
        answer = transport_obj.get("sdp") if isinstance(transport_obj, dict) else None
        if not isinstance(answer, str) or not answer:
            raise VoiceError(
                VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
                f"{self.name}: live/sessions response carried no SDP answer",
                provider=self.name,
                retryable=True,
            )
        ref = str((session_obj or {}).get("id") or f"openai-live:{session_id}")
        logger.info("openai_live_session_opened", session_id=session_id, session_ref=ref)
        return LiveSessionAnswer(
            session_ref=ref,
            sdp_answer=answer,
            request_body=dict(req.json_body or {}),
            response_echo=scrub_secrets({"session": session_obj or {}}),
        )


# ------------------------------------------------------- event mapping (in)


def map_server_event(event: dict[str, Any], *, at_ms: int) -> tuple[RealtimeSessionEvent, ...]:
    """Documented Live server event -> ``RealtimeSessionEvent`` (zero or more).

    Only what the vendor documents is mapped: a client delegation becomes a tool call
    whose ``call_id`` IS the ``delegation_id`` (the relay key for the result), and
    ``error`` becomes an error. Speech/response-audio events are not documented on
    WebRTC and are not guessed; transcript deltas are read with
    :func:`input_transcript_delta`."""
    kind = event.get("type")
    if kind == EV_DELEGATION_CREATED:
        delegation = event.get("delegation") or {}
        delegation_id = delegation.get("id")
        return (
            RealtimeSessionEvent(
                RT_TOOL_CALL,
                at_ms,
                {
                    "call_id": delegation_id,
                    "delegation_id": delegation_id,
                    "name": None,  # a delegation names no tool; the transcript says what
                    "arguments": {},
                    "target": delegation.get("target"),
                    "offset_ms": event.get("offset_ms"),
                },
            ),
        )
    if kind == EV_ERROR:
        err = event.get("error") or {}
        return (
            RealtimeSessionEvent(
                RT_ERROR,
                at_ms,
                {
                    "type": err.get("type"),
                    "code": err.get("code"),
                    "message": str(err.get("message") or "")[:500],
                    "event_id": event.get("event_id"),
                },
            ),
        )
    return ()


def input_transcript_delta(event: dict[str, Any]) -> str | None:
    """The owner's speech fragment from ``session.input_transcript.delta`` (verbatim)."""
    if event.get("type") != EV_INPUT_TRANSCRIPT_DELTA:
        return None
    delta = event.get("delta")
    return delta if isinstance(delta, str) else None


# ---------------------------------------------------- outgoing commands (out)


def commentary_command(delegation_id: str, content: str) -> dict[str, Any]:
    """A delegation's result back to the model. Never without its ``delegation_id``
    (the shared contract: an id-less or cancelled delegation's result is not sent)."""
    if not delegation_id:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            "session.commentary.append needs the delegation_id it answers",
            provider=OPENAI_LIVE_PROVIDER_NAME,
        )
    return {"type": CMD_COMMENTARY_APPEND, "delegation_id": delegation_id, "content": content}


def close_command() -> dict[str, Any]:
    return {"type": CMD_SESSION_CLOSE}


__all__ = [
    "CMD_COMMENTARY_APPEND",
    "CMD_SESSION_CLOSE",
    "DATA_CHANNEL_NAME",
    "DEFAULT_MODEL",
    "EV_DELEGATION_CREATED",
    "EV_ERROR",
    "EV_INPUT_TRANSCRIPT_DELTA",
    "EV_OUTPUT_TRANSCRIPT_DELTA",
    "EV_SESSION_CLOSED",
    "EV_SESSION_STARTED",
    "EV_USAGE_UPDATED",
    "OPENAI_LIVE_PROVIDER_NAME",
    "SUPPORTED_VOICES",
    "TICKET_HEADER",
    "USD_PER_MINUTE",
    "LiveSessionAnswer",
    "OpenAILiveProvider",
    "close_command",
    "commentary_command",
    "input_transcript_delta",
    "map_server_event",
    "scrub_secrets",
]
