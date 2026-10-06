"""The TwiML a call runs: our own Turkish audio, or Twilio's Turkish voice when ours is down.

``<Play>`` fetches the one-time url (``app.telephony.routes.audio_router``) once; ``<Say
language="tr-TR">`` is the fallback - a Turkish voice Twilio synthesises itself, so the owner
still hears what happened when our TTS cannot speak. Everything is XML-escaped: the text is
an event's own sentence and may carry ``&`` or ``<``.
"""

from __future__ import annotations

from typing import Final
from xml.sax.saxutils import escape, quoteattr

SAY_LANGUAGE: Final[str] = "tr-TR"
#: Amazon Polly's Turkish voice in Twilio's <Say> voice list.
SAY_VOICE: Final[str] = "Polly.Filiz"


def build_twiml(*, text: str, audio_url: str | None) -> str:
    if audio_url:
        spoken = f"<Play>{escape(audio_url)}</Play>"
    else:
        spoken = (
            f"<Say language={quoteattr(SAY_LANGUAGE)} voice={quoteattr(SAY_VOICE)}>"
            f"{escape(text)}</Say>"
        )
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Pause length="1"/>{spoken}</Response>'


__all__ = ["SAY_LANGUAGE", "SAY_VOICE", "build_twiml"]
