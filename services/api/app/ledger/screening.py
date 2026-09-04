"""One screen for text that can end up spoken to the owner.

The Self Explanation engine reads ledger events, lessons, opportunities and goals
aloud, largely verbatim. Any of those rows can carry text that originated outside
the system - a captured page, an exception message, an incident's evidence blob -
so instruction-shaped content in a database row is the same class of risk as
instruction-shaped content in a fetched page.

This module exists because the screen used to live inside the Pydantic validators of
``POST /v1/ledger/events``. That covered exactly one ingestion path. Incidents written
by ``app.selfhealing``, lessons compiled by ``app.experience`` and events written by
internal callers of ``ledger.service.record`` never passed through it, and their text
reached ``app/explain`` - and the realtime voice provider - unscreened (independent
security review, 2026-09-05).

Two entry points, deliberately different:

* :func:`screen_text` REFUSES - used at an API boundary, where the right answer is to
  reject the request and keep the ledger clean;
* :func:`safe_evidence_text` REPLACES - used where text is about to be spoken. A
  refusal there must not delete the record or drop the answer: the owner is told a
  stored value was refused, and the evidence reference still points at it so it can be
  read rather than listened to.
"""

from __future__ import annotations

import re
from typing import Any, Final

from app.research.injection import is_assistant_directed, is_injection_suspected

#: Phrasings the shared browser marker set (packages/protocol, frozen with the installed
#: worker) does not cover but a spoken briefing must still refuse.
SPOKEN_TEXT_MARKERS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore (?:all |any |the )?(?:previous|prior|earlier|above) (?:instructions|rules)",
        r"disregard (?:all |any |the )?(?:previous|prior|earlier|above) (?:instructions|rules)",
        r"(?:önceki|onceki|yukarıdaki|yukaridaki) talimat",
        r"talimatlar[ıi] (?:yok say|unut|görmezden gel|gormezden gel)",
        r"you are now",
        r"sahibine (?:söyle|soyle) ki",
    )
)

#: Instruction-shaped phrasings, for text that is about to be SPOKEN. This is a
#: deliberately narrower set than the browser marker list ``is_injection_suspected``
#: uses. That list is tuned for fetched web pages and includes bare words such as
#: "install", which is a perfectly ordinary word in this system's own operational
#: vocabulary ("installed version", "installed worker"). Applying it to stored
#: evidence text replaced true sentences with a refusal notice - the system lying by
#: omission about its own work, which is worse than the risk it was guarding against.
#: A refusal at an API boundary is loud and recoverable; a refusal mid-sentence is
#: silent, so the speech screen must be high-precision (2026-09-05).
SPEECH_UNSAFE_MARKERS: Final[tuple[re.Pattern[str], ...]] = SPOKEN_TEXT_MARKERS + tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bsystem prompt\b",
        r"\byour (?:instructions|system prompt|rules)\b",
        r"\bnew instructions?\b",
        r"\bact as\b.{0,20}\b(?:assistant|ai|model)\b",
        r"sen art[ıi]k\b",
        r"(?:şifreyi|sifreyi|parolay[ıi]|anahtar[ıi]) (?:göster|goster|söyle|soyle|yaz)",
        r"(?:komutu|şu komutu|su komutu) (?:çalıştır|calistir)",
        r"\brun (?:this|the following) command\b",
    )
)

#: What the owner hears in place of a refused stored value. It says what happened
#: rather than pretending the record is empty.
REFUSED_TEXT_TR: Final[str] = "bu kaydın metni güvenlik taramasında reddedildi"

#: Nothing spoken from a single stored value is longer than this. A lesson's root
#: cause once embedded a whole ``evidence_json`` repr; a briefing is a spoken answer,
#: not a document.
MAX_EVIDENCE_CHARS: Final[int] = 400


def is_unsafe_to_speak(value: str) -> bool:
    """True when this text must not be read aloud verbatim.

    High-precision by design - see :data:`SPEECH_UNSAFE_MARKERS`.
    """
    return any(marker.search(value) for marker in SPEECH_UNSAFE_MARKERS)


def is_unsafe_to_accept(value: str) -> bool:
    """True when this text must not be accepted at an API boundary at all.

    Strictly broader than :func:`is_unsafe_to_speak`: at an ingress the failure mode
    is a rejected request, which the caller sees and can fix.
    """
    return (
        is_injection_suspected(value) or is_assistant_directed(value) or is_unsafe_to_speak(value)
    )


def screen_text(value: str, *, where: str) -> str:
    """Return ``value`` unchanged, or raise ``ValueError`` - for API boundaries."""
    if is_unsafe_to_accept(value):
        raise ValueError(f"{where} carries instruction-shaped text and was refused")
    return value


def screen_nested(value: Any, *, where: str, depth: int = 0) -> None:
    """Screen every string inside a nested structure, refusing on the first hit."""
    if depth > 8:
        raise ValueError(f"{where} nests too deeply")
    if isinstance(value, str):
        screen_text(value, where=where)
    elif isinstance(value, dict):
        for inner in value.values():
            screen_nested(inner, where=where, depth=depth + 1)
    elif isinstance(value, list | tuple):
        for inner in value:
            screen_nested(inner, where=where, depth=depth + 1)


def safe_evidence_text(value: Any, *, max_len: int = MAX_EVIDENCE_CHARS) -> str:
    """A stored value reduced to something safe to say: flat, bounded, screened.

    Never raises: a poisoned or oversized row must degrade one sentence of the
    answer, not fail the owner's question.
    """
    text = " ".join(str(value if value is not None else "").split())
    if not text:
        return ""
    if is_unsafe_to_speak(text):
        return REFUSED_TEXT_TR
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


__all__ = [
    "MAX_EVIDENCE_CHARS",
    "SPEECH_UNSAFE_MARKERS",
    "REFUSED_TEXT_TR",
    "SPOKEN_TEXT_MARKERS",
    "is_unsafe_to_accept",
    "is_unsafe_to_speak",
    "safe_evidence_text",
    "screen_nested",
    "screen_text",
]
