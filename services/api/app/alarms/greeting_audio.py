"""Synthesising the wake greeting (M18.3 spec §3.7).

The greeting is a WAV the COMPANION plays through its own render endpoint — not a realtime
session, not the microphone, not the browser. That is the whole point: the owner is asleep,
there is no live voice session, and the alarm must speak anyway. This module therefore
depends on a TTS provider and nothing else; ``tests/unit/test_alarms_structure.py`` asserts
the alarms package never imports ``RealtimeSessionRow``.

Provider: ``app.voice.providers.OpenAITTSProvider`` (the owner's OpenAI key is already the
realtime key), with the voice nearest the realtime persona. The realtime adapter's own
configured voice is ``cedar`` (``Settings.voice_realtime_openai_voice``), and the TTS
endpoint takes a voice NAME, so :data:`GREETING_VOICE_PREFERENCE` asks for ``cedar`` first
and falls back explicitly to ``alloy`` — the one voice the TTS endpoint has always
accepted — rather than letting an unknown-voice error silence the greeting. The fallback is
an explicit second attempt, not a silent substitution: which voice actually spoke is
returned on the result and lands in the receipt.

Text goes through the narration normaliser with the owner's pronunciation map when a
session is supplied, exactly like ``app.routines.dispatch.RealtimeSayBriefing`` does, so a
custom ``greeting_policy.text`` is read the way the owner taught the system to read things.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Protocol

from app.logging import get_logger

logger = get_logger("app.alarms.greeting_audio")

#: The realtime persona's voice first (ADR-0043: the owner's A/B verdict was cedar), then
#: the always-accepted default. Ordered, tried in order, never guessed past.
GREETING_VOICE_PREFERENCE: Final[tuple[str, ...]] = ("cedar", "alloy")

#: Spec §3.7: WAV PCM16 mono 24 kHz, at most 15 s.
GREETING_FORMAT: Final = "wav"
GREETING_MAX_SECONDS: Final = 15
#: Spec §5.4's playback level for the greeting.
GREETING_LEVEL: Final = 0.75


class TTSProviderLike(Protocol):
    """Structurally satisfied by every provider in ``app.voice.providers`` (real and fake)
    without importing one, so this module is unit-testable against the Fake provider and
    the alarms package keeps no hard edge into the voice provider registry."""

    name: str

    def synthesize(
        self, text: str, *, voice: str = ..., speed: float = ..., fmt: str = ...
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class GreetingAudio:
    """The synthesised greeting and the truth about how it was made."""

    audio: bytes
    voice: str
    provider: str
    text: str
    duration_ms: int = 0

    def as_evidence(self) -> dict[str, Any]:
        return {
            "kind": "greeting_audio",
            "ref": f"{self.provider}:{self.voice}",
            "bytes": len(self.audio),
            "duration_ms": self.duration_ms,
        }


class GreetingSynthesisFailed(RuntimeError):
    """Every candidate voice failed. The caller records it truthfully and the music keeps
    playing — a greeting that could not be spoken never becomes a claim that it was."""


def synthesize_greeting(
    text: str,
    *,
    provider: TTSProviderLike,
    voices: tuple[str, ...] = GREETING_VOICE_PREFERENCE,
    cache: dict[str, GreetingAudio] | None = None,
) -> GreetingAudio:
    """Synthesise ``text`` to WAV, trying ``voices`` in order.

    ``cache`` is an optional caller-owned dict keyed by ``(text, voice-preference)``: the
    same greeting sentence recurs every morning and re-synthesising it costs a network
    round trip inside a wake sequence that is already on a clock. Nothing is cached across
    processes — see ``app.alarms.audio_store``'s note on ephemeral delivery state.
    """
    if not text or not text.strip():
        raise GreetingSynthesisFailed("refusing to synthesise an empty greeting")
    key = f"{'|'.join(voices)}::{text}"
    if cache is not None and key in cache:
        return cache[key]

    errors: list[str] = []
    for voice in voices:
        try:
            result = provider.synthesize(text, voice=voice, speed=1.0, fmt=GREETING_FORMAT)
        except Exception as exc:  # noqa: BLE001 - the next voice is the point of the loop
            errors.append(f"{voice}:{type(exc).__name__}")
            logger.warning(
                "greeting_voice_rejected", voice=voice, error=f"{type(exc).__name__}: {exc}"
            )
            continue
        audio = GreetingAudio(
            audio=bytes(result.audio),
            voice=voice,
            provider=getattr(result, "provider", getattr(provider, "name", "unknown")),
            text=text,
            duration_ms=int(getattr(result, "duration_ms", 0) or 0),
        )
        if cache is not None:
            cache[key] = audio
        return audio
    raise GreetingSynthesisFailed(
        "no configured TTS voice could speak the greeting: " + ", ".join(errors)
    )


#: The provider name the offline fake answers with. B13 req 267: NAMED here, because
#: "which provider spoke" is the only thing that distinguishes a greeting from a buzz.
FALLBACK_PROVIDER_NAME: Final[str] = "fake-tts-greeting"


def is_fallback_provider(provider: Any) -> bool:
    """Whether this provider produces a synthetic tone rather than speech (req 267/234).

    Asks the PROVIDER first (``synthetic_speech``), and only then the name.

    B20 req 234: the name test was the whole check, and the name it tested was the single
    one this module builds. Every other fake in the codebase produces the same 110 Hz sine
    - the registry's ``fake-tts``, the benchmark's ``fake-tts-a`` / ``fake-tts-b``, and any
    name a caller passes to ``FakeTTSProvider`` - so any delivery path handed one of those
    would have played a tone to the owner as speech, past a guard written to prevent
    exactly that. A tone is a property of what the provider IS; the name is only how the
    receipt refers to it, and it is kept as the second test for anything that answers a
    name without declaring itself.
    """
    if getattr(provider, "synthetic_speech", False):
        return True
    return getattr(provider, "name", "") == FALLBACK_PROVIDER_NAME


def build_greeting_tts(settings: Any) -> TTSProviderLike:
    """The provider the wake greeting speaks through (spec §3.7).

    ``OpenAITTSProvider`` when the owner's OpenAI key is configured (the same key the
    realtime session already uses), and the OFFLINE fake otherwise — never ``None``. A
    process with no key still produces a real WAV the companion really plays, so "the
    greeting path works" is provable on a machine with no credentials at all.

    **B13 req 267: the difference must be ANNOUNCED, not merely recorded.** What the fake
    produces is a 110 Hz sine wave. Played into a bedroom after an alarm it is not a
    greeting that sounds odd, it is a fault that sounds deliberate — and the row said
    nothing, so the owner's only way to find out was to hear it and wonder. The sequence
    now refuses to play it and says why (``greeting_failure="no_tts_key"``), which is the
    honest version of the same fallback: the path is exercised, the WAV is still produced
    and testable, and nobody is buzzed at.
    """
    from app.voice.providers import FakeTTSProvider, OpenAITTSProvider

    api_key = getattr(settings, "voice_openai_api_key", "") or getattr(
        settings, "openai_api_key", ""
    )
    if api_key:
        return OpenAITTSProvider(api_key, default_voice=GREETING_VOICE_PREFERENCE[-1])
    return FakeTTSProvider(name=FALLBACK_PROVIDER_NAME)


def normalize_greeting(text: str, session: Any | None = None) -> str:
    """Run a custom greeting through the narration normaliser with the owner's
    pronunciation map (spec §3.7). Never fails the caller: an unreadable pronunciation
    table must not silence a wake greeting."""
    try:
        from app.narration.normalizer import normalize

        pronunciation = None
        if session is not None:
            from app.narration.service import pronunciation_map

            pronunciation = pronunciation_map(session)
        return normalize(text, mode="narration", pronunciation=pronunciation)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("greeting_normalize_failed", error=f"{type(exc).__name__}: {exc}")
        return text


__all__ = [
    "FALLBACK_PROVIDER_NAME",
    "GREETING_FORMAT",
    "GREETING_LEVEL",
    "GREETING_MAX_SECONDS",
    "GREETING_VOICE_PREFERENCE",
    "GreetingAudio",
    "GreetingSynthesisFailed",
    "TTSProviderLike",
    "build_greeting_tts",
    "is_fallback_provider",
    "normalize_greeting",
    "synthesize_greeting",
]
