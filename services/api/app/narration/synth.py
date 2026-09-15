"""B21 req 224: the narration engine's missing half — something that actually speaks.

`app.narration.engine` has planned, split, cached and cancelled since M4, and
`NarrationEngine` — the read-ahead pipeline requirement 226 asks for — has existed the
whole time with **no implementation of its `Synthesizer` seam anywhere under `app/`**. The
only thing that ever passed one was a test. So the narration path could tell you which
sentence you were on, resume it on another device, and never once make a sound.

Two things stood between the seam and a provider, and the matrix names one of them
("seam imzası sağlayıcıyla uyumsuz"):

* the engine asks for ``synthesize(text, settings) -> bytes`` and every TTS provider in
  this repository offers ``synthesize(text, *, voice, speed, fmt) -> TTSResult``;
* and the offline fake would have answered — with a 110 Hz sine wave. B13 req 267 and B20
  req 234 settled what happens then: a synthetic tone is never handed to the owner AS
  speech. Narration is the longest form of speech this system produces and the least
  forgiving place to buzz for a minute and a half.

So `build_synthesizer` returns **None** when this deployment has no provider that speaks,
and the caller degrades to text (req 233's rule, applied to the third delivery path). A
narration session with no voice is a reader that shows you the sentence; it is not a
reader that hums at you.
"""

from __future__ import annotations

from typing import Any

from app.logging import get_logger
from app.narration.engine import VoiceSettings

logger = get_logger("app.narration.synth")

#: What the cache and the HTTP surface carry. WAV because every provider in this repo can
#: emit it, it is header-verifiable with no dependency, and `wav_duration_ms` can measure
#: what was produced rather than estimating it.
NARRATION_FORMAT = "wav"

#: A chunk is one sentence (or one table/code block). Anything past this is not a chunk,
#: it is a document, and synthesising it would block the request that asked for it.
MAX_CHUNK_CHARS = 2000


class NarrationSynthesisFailed(RuntimeError):
    """The provider was asked and could not answer. Never raised for 'no provider'."""


class ProviderSynthesizer:
    """Adapts a `TTSProvider` to `app.narration.engine.Synthesizer`.

    The whole adapter is the signature translation the matrix calls a mismatch, plus one
    rule of its own: the text handed over is what the caller already normalised (the plan
    carries `pronunciation`-applied text), so this does not normalise again — running the
    normaliser twice is how "yüzde 20" becomes "yüzde yirmi yirmi".
    """

    def __init__(self, provider: Any, *, fmt: str = NARRATION_FORMAT) -> None:
        self.provider = provider
        self.fmt = fmt
        self.calls = 0

    @property
    def name(self) -> str:
        return str(getattr(self.provider, "name", "") or "unknown")

    def synthesize(self, text: str, settings: VoiceSettings) -> bytes:
        body = (text or "").strip()
        if not body:
            # An empty chunk is a planning bug, not a provider failure, and asking a paid
            # provider to say nothing is the wrong way to find out about it.
            raise NarrationSynthesisFailed("empty chunk text")
        if len(body) > MAX_CHUNK_CHARS:
            body = body[:MAX_CHUNK_CHARS]
            logger.warning("narration_chunk_truncated", chars=len(text))
        self.calls += 1
        try:
            result = self.provider.synthesize(
                body,
                voice=settings.voice or "default",
                speed=settings.speed,
                fmt=self.fmt,
            )
        except Exception as exc:  # noqa: BLE001 - the caller decides what a failure means
            raise NarrationSynthesisFailed(f"{type(exc).__name__}: {exc}") from exc
        audio = getattr(result, "audio", None)
        if not isinstance(audio, bytes) or not audio:
            raise NarrationSynthesisFailed(f"{self.name} returned no audio")
        return audio


def build_synthesizer(settings: Any, *, provider: Any = None) -> ProviderSynthesizer | None:
    """The narration voice for this deployment, or None when there is none.

    None is a first-class answer and the caller must handle it: with no configured key the
    only provider available is the offline fake, and what it produces is a tone. B20 req
    234's policy asks the provider what it IS rather than what it is called, so a fake
    under any name is refused here exactly as it is on the wake path.
    """
    from app.alarms.greeting_audio import build_greeting_tts, is_fallback_provider

    chosen = provider if provider is not None else build_greeting_tts(settings)
    if chosen is None or is_fallback_provider(chosen):
        logger.info(
            "narration_has_no_voice", provider=str(getattr(chosen, "name", "") or "none")
        )
        return None
    return ProviderSynthesizer(chosen)


def voice_settings_for(row: Any, *, voice: str = "default") -> VoiceSettings:
    """The narration session's own voice settings.

    `speed` is the owner's, persisted per session and changed by "daha hızlı oku"; it is
    part of the cache key, so a speed change re-synthesises rather than replaying the old
    pace at a new label.
    """
    speed = float(getattr(row, "speed", 1.0) or 1.0)
    return VoiceSettings(voice=voice, speed=max(0.5, min(3.0, speed)), locale="tr-TR")


__all__ = [
    "MAX_CHUNK_CHARS",
    "NARRATION_FORMAT",
    "NarrationSynthesisFailed",
    "ProviderSynthesizer",
    "build_synthesizer",
    "voice_settings_for",
]
