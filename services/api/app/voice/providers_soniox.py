"""Soniox real-time STT behind the :class:`~app.voice.providers.STTProvider` seam.

MEASUREMENT ONLY (owner decision 2026-10-01): this adapter is used by
:mod:`app.voice.stt_compare` and by nothing else. It is not registered in ``registry.py``,
no ``Settings`` field names it and no default points at it - adoption is a separate
approval, and so is the Soniox account its key would come from.

The wire format is the one read on 2026-10-01 (``team/plans/stt-engines-measure-integration.md``
§5): one text frame of JSON configuration that CARRIES THE API KEY, then the audio as binary
frames, then an empty frame; the server answers with token lists and ends with
``"finished": true``. Three rules follow from the terms that were read:

* real-time WebSocket only - the Async/file API stores audio for 30 days and is never used;
* no ``client_reference_id`` is sent (it would be logged with the usage metadata);
* because the key travels in the first frame, nothing is sent - the socket is not even
  opened - until there is a key AND a non-empty audio chunk in hand.

The key is never in the URL, a log line or a :class:`VoiceError`.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import ProviderCapabilities, STTResult

SONIOX_URL_US = "wss://stt-rt.soniox.com/transcribe-websocket"
#: The EU region is "enabled by request" and has its own keys (plan §2).
SONIOX_URL_EU = "wss://stt-rt.eu.soniox.com/transcribe-websocket"
#: From the vendor's documentation example; confirm the current name at the first real run.
SONIOX_DEFAULT_MODEL = "stt-rt-v5"

_AUTH_ERROR_CODES = (401, 402, 403)
_VENDOR_MESSAGE_MAX = 300


class SonioxSTTProvider:
    """Soniox real-time transcription of one finished recording (unpaced streaming)."""

    name = "soniox"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = SONIOX_DEFAULT_MODEL,
        url: str = SONIOX_URL_US,
        timeout_s: float = 30.0,
        chunk_bytes: int = 32_000,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._model = model
        self._url = url
        self._timeout_s = timeout_s
        self._chunk_bytes = max(1, chunk_bytes)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name,
            kind="stt",
            languages=("tr-TR", "en-US"),
            streaming=True,
            long_form_stability="n/a",
            pronunciation_dict=False,
            voice_selection=False,
            speed_control=False,
            cost_metadata={"unit": "audio_hours", "usd_per_hour": 0.12, "billing": "usage"},
            output_formats=("text",),
            latency_class="realtime",
            requires_api_key=True,
        )

    def build_config(self, *, language: str) -> dict[str, Any]:
        """The first frame. Built without I/O so a test can assert every field."""
        return {
            "api_key": self._api_key,
            "model": self._model,
            "audio_format": "auto",
            "language_hints": [language.split("-")[0]],
        }

    def transcribe(self, audio: bytes, *, language: str = "tr-TR") -> STTResult:
        if not self._api_key:
            raise VoiceError(
                VoiceErrorClass.PROVIDER_AUTH_MISSING,
                f"{self.name}: no API key configured (owner action: a Soniox account is a "
                "separate approval; then store PAGENTOS_VOICE_SONIOX_API_KEY)",
                provider=self.name,
            )
        chunks = [
            audio[start : start + self._chunk_bytes]
            for start in range(0, len(audio), self._chunk_bytes)
        ]
        if not chunks:
            raise VoiceError(
                VoiceErrorClass.VALIDATION_ERROR,
                f"{self.name}: no audio to transcribe",
                provider=self.name,
            )
        try:
            from websockets.exceptions import ConnectionClosed, WebSocketException
            from websockets.sync.client import connect
        except ImportError as exc:  # pragma: no cover - websockets is a locked dependency
            raise VoiceError(
                VoiceErrorClass.OPTIONAL_DEPENDENCY_MISSING,
                f"{self.name}: the websockets package is not installed",
                provider=self.name,
            ) from exc

        started = time.perf_counter()
        finals: list[str] = []
        confidences: list[float] = []
        try:
            with connect(
                self._url, open_timeout=self._timeout_s, close_timeout=self._timeout_s
            ) as ws:
                ws.send(json.dumps(self.build_config(language=language)))
                for chunk in chunks:
                    ws.send(chunk)
                ws.send(b"")
                while True:
                    message = self._decode(ws.recv(timeout=self._timeout_s))
                    if "error_code" in message or "error_message" in message:
                        raise self._vendor_error(message)
                    for token in message.get("tokens") or ():
                        if isinstance(token, dict) and token.get("is_final"):
                            finals.append(str(token.get("text", "")))
                            if isinstance(token.get("confidence"), int | float):
                                confidences.append(float(token["confidence"]))
                    if message.get("finished"):
                        break
        except VoiceError:
            raise
        except TimeoutError as exc:
            raise VoiceError(
                VoiceErrorClass.TIMEOUT,
                f"{self.name}: no answer within {self._timeout_s} s",
                provider=self.name,
                retryable=True,
            ) from exc
        except ConnectionClosed as exc:
            raise VoiceError(
                VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
                f"{self.name}: the connection closed before the transcript was finished",
                provider=self.name,
                retryable=True,
            ) from exc
        except (OSError, WebSocketException) as exc:
            # the exception text may quote the URL, never the key (the key is not in it)
            raise VoiceError(
                VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
                f"{self.name}: transport failure ({type(exc).__name__})",
                provider=self.name,
                retryable=True,
            ) from exc

        return STTResult(
            # tokens are sub-word pieces that carry their own spaces: no separator
            text="".join(finals).strip(),
            confidence=round(sum(confidences) / len(confidences), 4) if confidences else 1.0,
            word_timings=(),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            provider=self.name,
            language=language,
        )

    def _decode(self, frame: str | bytes) -> dict[str, Any]:
        try:
            message = json.loads(frame)
        except (ValueError, UnicodeDecodeError) as exc:
            raise VoiceError(
                VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
                f"{self.name}: a response that is not JSON",
                provider=self.name,
            ) from exc
        if not isinstance(message, dict):
            raise VoiceError(
                VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
                f"{self.name}: a response that is not a JSON object",
                provider=self.name,
            )
        return message

    def _vendor_error(self, message: dict[str, Any]) -> VoiceError:
        code = message.get("error_code")
        auth = code in _AUTH_ERROR_CODES
        # a vendor may echo what it was sent; the key must not ride out in an error
        said = str(message.get("error_message", "")).replace(self._api_key, "[key]")
        return VoiceError(
            VoiceErrorClass.PROVIDER_AUTH_MISSING
            if auth
            else VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{self.name}: {said[:_VENDOR_MESSAGE_MAX]}",
            provider=self.name,
            retryable=not auth,
            details={
                "error_code": code,
                "error_type": message.get("error_type"),
                "request_id": message.get("request_id"),
            },
        )


__all__ = ["SONIOX_DEFAULT_MODEL", "SONIOX_URL_EU", "SONIOX_URL_US", "SonioxSTTProvider"]
