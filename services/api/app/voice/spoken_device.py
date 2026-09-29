"""The machine a sentence NAMES, kept out of what the sentence is ABOUT (ADR-0212).

"Ofis bilgisayarında yapay zeka son gelişmeleri araştır" names a device and asks for a
research. The device phrase is an instruction to the system, not part of the subject; it
rode into the router's extractors, and from there into a search box (2026-09-29: the query
"ofis bilgisayarında Yapay Zeka son gelişmeler"), a media search, a document search, a file
pattern, an image prompt and a remembered fact.

The router is not taught about devices - it reads words, and its routes were tuned against
sentences with them in - so the sentence is read TWICE only when it names a device: as heard,
and without the device phrase. The cleaned reading is taken only when it routes to the SAME
intent: removing words can improve what a subject field holds and can never change what the
owner asked for. Otherwise the sentence is read as heard, exactly as before this module.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.devices.aliases import extract_aliases, strip_device_phrases


def resolve_without_device_phrase(
    text: str, resolve: Callable[[str], Any]
) -> tuple[Any, str, tuple[str, ...]]:
    """``(resolved intent, the text its subject fields were read from, alias words named)``.

    ``resolve`` is the caller's own ``resolve_intent`` with the live-state arguments bound; it
    is called once for a sentence that names no device (the very call there always was) and
    twice for one that does."""
    original = resolve(text)
    named = extract_aliases(text) if isinstance(text, str) else ()
    if not named:
        return original, text, ()
    cleaned, _ = strip_device_phrases(text)
    if not cleaned.strip():
        return original, text, named
    alternative = resolve(cleaned)
    if alternative.intent == original.intent:
        return alternative, cleaned, named
    return original, text, named
