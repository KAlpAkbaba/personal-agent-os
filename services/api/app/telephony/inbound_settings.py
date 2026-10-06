"""The inbound line's settings: the limits, the notice, and the credentials it reuses.

Off by default. The two new knobs (``PAGENTOS_TELEPHONY_INBOUND_ENABLED``,
``PAGENTOS_TELEPHONY_INBOUND_DAILY_MINUTES``) join ``app.config.Settings`` in the binding card
(ADR); until then ``from_settings`` reads them when they exist and keeps the defaults when they
do not. Everything else comes from settings that already exist: Twilio's auth token and the
public base url (jarvis-calls-owner) and the owner's OpenAI key and realtime model (voice).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from app.telephony.inbound_twilio import KVKK_ANNOUNCEMENT_TR
from app.voice.providers_openai_realtime import DEFAULT_BASE_URL


@dataclasses.dataclass(frozen=True, slots=True)
class InboundSettings:
    enabled: bool = False
    max_concurrent: int = 1
    max_call_seconds: int = 300
    daily_minutes_cap: int = 30
    bridge_token_ttl_s: int = 120
    #: Seconds between the goodbye and the hang-up at the time limit.
    farewell_grace_s: int = 10
    #: A call is not answered when less than this is left of the day's allowance.
    min_call_seconds: int = 60
    kvkk_text: str = KVKK_ANNOUNCEMENT_TR
    public_base_url: str = ""
    twilio_auth_token: str = dataclasses.field(default="", repr=False)
    openai_api_key: str = dataclasses.field(default="", repr=False)
    realtime_base_url: str = DEFAULT_BASE_URL
    realtime_model: str = "gpt-realtime-2.1"
    realtime_voice: str = "cedar"
    transcription_model: str = "gpt-4o-transcribe"

    @property
    def configured(self) -> bool:
        """Twilio can reach us, we can check its signature, and the realtime leg can open."""
        return bool(
            self.public_base_url.startswith("https://")
            and self.twilio_auth_token
            and self.openai_api_key
        )


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else (value or ""))


def from_settings(settings: Any, **overrides: Any) -> InboundSettings:
    values: dict[str, Any] = {
        "enabled": bool(getattr(settings, "telephony_inbound_enabled", False)),
        "daily_minutes_cap": int(getattr(settings, "telephony_inbound_daily_minutes", 30)),
        "public_base_url": str(getattr(settings, "telephony_public_base_url", "") or ""),
        "twilio_auth_token": _secret(getattr(settings, "telephony_twilio_auth_token", "")),
        "openai_api_key": _secret(getattr(settings, "voice_openai_api_key", "")),
        "realtime_base_url": str(
            getattr(settings, "voice_realtime_openai_base_url", "") or DEFAULT_BASE_URL
        ),
        "realtime_model": str(
            getattr(settings, "voice_realtime_openai_model", "") or "gpt-realtime-2.1"
        ),
        "realtime_voice": str(getattr(settings, "voice_realtime_openai_voice", "") or "cedar"),
        "transcription_model": str(
            getattr(settings, "voice_realtime_openai_transcription_model", "")
            or "gpt-4o-transcribe"
        ),
    }
    values.update(overrides)
    return InboundSettings(**values)


__all__ = ["InboundSettings", "from_settings"]
