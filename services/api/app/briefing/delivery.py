"""B15 req 281: the morning briefing, read aloud with no browser open.

`BriefingService.build` has had exactly one caller since it was written — the voice tool.
So the whole morning experience required the owner to be awake, at a machine, with a
browser open and a live voice session running, which is the opposite of what a morning
briefing is for. This module is the other caller: the wake sequence's, on the alarm path.

**Why it is a sequence of clips and not one WAV.** The roadmap's test plan says "one WAV
contains every section". It cannot, and the number that decides it was measured rather than
assumed: `desktop.play_audio` **truncates** at `GreetingPlayer.MaxSeconds` (20 s) — it does
not refuse — and a representative full briefing (greeting, date, weather, system status,
overnight work, research, calendar, news) is about 33 seconds of Turkish speech. One WAV
would mean the owner hears the greeting, the date, the weather and half the system status,
and never learns the rest existed, with no error anywhere to say so. Silent truncation of
the thing the owner asked to be told is the exact failure family this repository keeps
finding, so the briefing is split on SENTENCE boundaries into clips the device will play
whole, and each one is its own command.

**A part that fails stops the briefing and says how far it got.** Not because continuing is
technically hard, but because a briefing with a hole in it is worse than a short one: the
owner cannot tell which part they missed. The receipt names the parts played and the part
that failed.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.logging import get_logger

logger = get_logger("app.briefing.delivery")

#: The device truncates at this, in `GreetingPlayer.MaxSeconds`. Restated here because the
#: Cloud Core has to decide how to split BEFORE it sends anything, and
#: `test_the_briefing_clip_bound_is_the_device_s_own` reads the C# source and fails if the
#: two ever disagree — the same "read the other side's own file" discipline
#: `test_desktop_capability_mirror` and `test_device_fakes_match_the_device` already use.
DEVICE_MAX_CLIP_SECONDS = 20

#: What one clip is allowed to be. Under the device's own bound with room to spare, because
#: the split is an ESTIMATE from character count and being wrong in the direction of a
#: truncated clip is the failure this whole module exists to prevent.
MAX_CLIP_SECONDS = 15

#: Turkish narration, measured against the greeting sentences this system already speaks.
#: Deliberately pessimistic: over-estimating the duration makes clips shorter and the split
#: safer, while under-estimating it puts a sentence past the device's truncation point.
CHARS_PER_SECOND = 13.0

MAX_CLIP_CHARS = int(MAX_CLIP_SECONDS * CHARS_PER_SECOND)

#: A briefing longer than this is not read out. Eight clips is about two minutes of someone
#: talking at you before you have had coffee; past that the owner is better served by the
#: inbox, and an unbounded loop of device commands at 07:15 is not something to discover in
#: production.
MAX_CLIPS = 8

#: Sentence boundary: a full stop, question or exclamation mark followed by whitespace.
#: Turkish abbreviations are not special-cased - a split in the wrong place costs a slightly
#: odd pause, while a missed split costs a truncated sentence.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def estimated_seconds(text: str) -> float:
    """How long this will take to say, from its length. An estimate, and named as one."""
    return len(text) / CHARS_PER_SECOND


def split_into_clips(text: str, *, max_chars: int = MAX_CLIP_CHARS) -> list[str]:
    """Split on sentence boundaries into pieces the device will play whole.

    Never mid-sentence: a clip that ends halfway through "bugün hava" is worse than two
    clips. A single sentence longer than the bound is emitted ALONE and over the bound
    rather than cut - the device will truncate it, and that is visible in the receipt,
    whereas a sentence this module chopped would be a lie it told itself.
    """
    stripped = text.strip()
    if not stripped:
        return []
    clips: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(stripped):
        piece = sentence.strip()
        if not piece:
            continue
        if not current:
            current = piece
            continue
        if len(current) + 1 + len(piece) <= max_chars:
            current = f"{current} {piece}"
        else:
            clips.append(current)
            current = piece
    if current:
        clips.append(current)
    return clips


@dataclass(slots=True)
class BriefingDelivery:
    """What happened when the briefing was read out."""

    #: The clips that actually played, in order.
    spoken: list[str] = field(default_factory=list)
    #: How many clips the briefing was split into.
    clips: int = 0
    #: Why it stopped early, or "" when every clip played.
    failure: str = ""
    #: The whole briefing text, whether or not all of it was spoken.
    text: str = ""

    @property
    def ok(self) -> bool:
        return self.clips > 0 and len(self.spoken) == self.clips and not self.failure

    def as_dict(self) -> dict[str, Any]:
        return {
            "clips": self.clips,
            "spoken": len(self.spoken),
            "failure": self.failure,
            "complete": self.ok,
        }


def speak_briefing(
    session: Session,
    *,
    briefing_service: Any,
    settings: Any,
    live: dict[str, Any],
    tts: Any,
    audio_store: Any,
    audio_origin: str,
    play: Any,
    normalise: Any = None,
    now: datetime | None = None,
    device_id: uuid.UUID | None = None,
) -> BriefingDelivery:
    """Build the briefing and read it out, clip by clip, over ``play``.

    ``play(text, *, audio_id, url, sha256, size_bytes, max_seconds) -> bool`` is the one
    thing this module cannot do itself: dispatch a device command. Injected rather than
    imported so the alarm sequence owns the device port and this module owns the briefing,
    which is also what lets a test drive the whole path with no device at all.

    Every failure is a `BriefingDelivery` with a reason, never an exception: this runs on
    the wake path, and a briefing that raises would be an alarm that stopped ringing
    because the weather service was slow.
    """
    delivery = BriefingDelivery()
    try:
        answer = briefing_service.build(
            session, settings=settings, live=live, device_id=device_id, now=now
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("briefing_build_failed", error=f"{type(exc).__name__}: {exc}")
        delivery.failure = f"build_failed:{type(exc).__name__}"
        return delivery

    text = str(answer.get("speech") or "").strip()
    if not text:
        delivery.failure = "empty_briefing"
        return delivery

    if normalise is not None:
        try:
            # The briefing's sentences carry clock times, temperatures and percentages that
            # no normaliser has touched - "saat 08:45", "21°C", "yüzde 20". The alarm's own
            # custom greeting has been normalised since M18.3 and the briefing never was,
            # because nothing spoke it aloud. Now something does.
            text = str(normalise(text)) or text
        except Exception as exc:  # noqa: BLE001 - an unreadable pronunciation table must
            # not silence a briefing, the same rule `normalize_greeting` already follows.
            logger.warning("briefing_normalise_failed", error=f"{type(exc).__name__}: {exc}")

    delivery.text = text
    clips = split_into_clips(text)
    if len(clips) > MAX_CLIPS:
        logger.info("briefing_truncated_to_clip_budget", clips=len(clips), budget=MAX_CLIPS)
        clips = clips[:MAX_CLIPS]
    delivery.clips = len(clips)

    for index, clip in enumerate(clips, start=1):
        try:
            audio = tts_synthesise(tts, clip)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            delivery.failure = f"synthesis_failed:{type(exc).__name__}"
            logger.warning("briefing_clip_synthesis_failed", clip=index, error=str(exc)[:200])
            return delivery
        if audio is None:
            delivery.failure = "no_tts_key"
            return delivery
        try:
            handle = audio_store.put(audio, now=now)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            delivery.failure = f"audio_store_failed:{type(exc).__name__}"
            logger.warning("briefing_clip_store_failed", clip=index, error=str(exc)[:200])
            return delivery

        played = play(
            clip,
            audio_id=f"briefing-{index}",
            url=f"{audio_origin}{handle.path()}",
            sha256=handle.sha256,
            size_bytes=handle.size_bytes,
            max_seconds=DEVICE_MAX_CLIP_SECONDS,
        )
        if not played:
            # Stop here and say how far we got. A briefing with a hole in it is worse than
            # a short one: the owner cannot tell which part they missed.
            delivery.failure = f"clip_not_played:{index}"
            logger.warning("briefing_clip_not_played", clip=index, of=len(clips))
            return delivery
        delivery.spoken.append(clip)

    return delivery


def tts_synthesise(tts: Any, text: str) -> bytes | None:
    """One clip's audio, or ``None`` when this deployment has no real voice.

    ``None`` rather than a raise, and rather than the offline fake's 110 Hz sine: B13 req
    267 established that a synthetic tone is not offered to the owner AS speech, and a
    briefing is even less the place to start.
    """
    from app.alarms.greeting_audio import is_fallback_provider, synthesize_greeting

    if tts is None or is_fallback_provider(tts):
        return None
    return synthesize_greeting(text, provider=tts).audio


__all__ = [
    "CHARS_PER_SECOND",
    "DEVICE_MAX_CLIP_SECONDS",
    "MAX_CLIPS",
    "MAX_CLIP_CHARS",
    "MAX_CLIP_SECONDS",
    "BriefingDelivery",
    "estimated_seconds",
    "speak_briefing",
    "split_into_clips",
    "tts_synthesise",
]
