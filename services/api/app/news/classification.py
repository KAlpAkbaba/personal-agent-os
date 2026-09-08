"""Pure candidate classification: Shorts, promo/unrelated, main-bulletin markers.

No network, no browser, no database — every function here takes plain data and returns
a plain verdict, which is what makes ``tests/unit/test_news_resolver.py``'s fixtures
exact and deterministic (task brief: "these fixtures are what make the behaviour
testable every day"). Turkish first (diacritic-folded, so "ana haber" matches whether
or not the ASR/RSS text carried the dotted/dotless i correctly), English kept for
international/tech-style channels the owner may configure later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

#: Diacritic folding table (mirrors tests/voice_corpus/corpus.py's own
#: ``_strip_diacritics`` — the same fold, kept here too so this module has no import
#: dependency on a test module).
_FOLD_TABLE = str.maketrans("çğıöşüÇĞİÖŞÜâîû", "cgiosuCGIOSUaiu")


def _fold(text: str) -> str:
    return text.translate(_FOLD_TABLE).lower()


@dataclass(frozen=True, slots=True)
class VideoCandidate:
    """One upload a provider (fixture, YouTube feed, ...) reports (spec §2/§3).

    ``published_at`` is the REAL publish/upload timestamp (never a retrieval time,
    never search rank) — the resolver sorts on this field alone. ``duration_s`` and
    ``is_short`` are optional: a channel feed alone does not carry a duration, so both
    default to unknown (``None``) and the classifier falls back to text/URL markers;
    a provider that DOES know (a fixture, or a future duration-aware provider) sets
    them explicitly and that evidence wins.
    """

    video_id: str
    title: str
    published_at: datetime
    channel_id: str
    url: str
    description: str = ""
    duration_s: float | None = None
    is_short: bool | None = None


#: Strong, unambiguous Shorts markers when duration is unknown (spec §2: "A Shorts clip
#: must not silently satisfy 'the latest full bulletin'").
_SHORTS_TEXT_MARKERS: tuple[str, ...] = ("#shorts", "#short", "#kisa")
_SHORTS_URL_MARKERS: tuple[str, ...] = ("/shorts/",)
#: A Short is 60 seconds or less (YouTube's own definition at the time this was written).
_SHORTS_MAX_DURATION_S = 60.0

#: Promotional / not-a-bulletin markers (Turkish + English) — teasers, trailers,
#: highlight reels: content that is genuinely from the news channel but is not itself
#: a news bulletin.
PROMO_MARKERS: tuple[str, ...] = (
    "fragman",
    "tanitim",
    "teaser",
    "trailer",
    "promo",
    "reklam",
    "on izleme",
    "highlights",
    "ozet video",
)

#: Full-bulletin markers (Turkish + English) — "the latest MAIN news"/"the latest FULL
#: broadcast" (spec §2/§6's own use case, "Show Ana Haber").
BULLETIN_MARKERS: tuple[str, ...] = (
    "ana haber",
    "ana haber bulteni",
    "tam bulten",
    "haber bulteni",
    "aksam haberleri",
    "gece haberleri",
    "gunun ana haber bulteni",
    "main news",
    "full bulletin",
    "full broadcast",
    "evening news",
    "nightly news",
)


def is_short(candidate: VideoCandidate) -> bool:
    """Shorts detection precedence (spec §2): an explicit provider signal
    (``is_short``) wins; failing that, a known duration <= 60s; failing that, a
    ``#shorts``-style text/URL marker. Unknown stays ``False`` (never assumed)."""
    if candidate.is_short is not None:
        return candidate.is_short
    if candidate.duration_s is not None:
        return candidate.duration_s <= _SHORTS_MAX_DURATION_S
    haystack = _fold(f"{candidate.title} {candidate.description} {candidate.url}")
    return any(marker in haystack for marker in _SHORTS_URL_MARKERS) or any(
        _fold(marker) in haystack for marker in _SHORTS_TEXT_MARKERS
    )


def is_promo(candidate: VideoCandidate) -> bool:
    """A teaser/trailer/highlight reel — real channel content, not a bulletin."""
    haystack = _fold(f"{candidate.title} {candidate.description}")
    return any(_fold(marker) in haystack for marker in PROMO_MARKERS)


def matches_bulletin_markers(candidate: VideoCandidate) -> bool:
    """An explicit "ana haber"/"tam bülten"/... marker in the title or description."""
    haystack = _fold(f"{candidate.title} {candidate.description}")
    return any(_fold(marker) in haystack for marker in BULLETIN_MARKERS)


_STOPWORDS = frozenset(
    {"ve", "ile", "bir", "the", "a", "an", "of", "for", "on", "in", "at", "de", "da"}
)

#: Punctuation stripped before tokenising for near-duplicate comparison — a title
#: differing only by a dash, a colon or a year in parentheses must still compare as
#: the same story (M13 spec §2's own "normalised" token Jaccard).
_TITLE_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)


def _title_tokens(title: str) -> frozenset[str]:
    normalized = _TITLE_PUNCT_RE.sub(" ", _fold(title))
    return frozenset(t for t in normalized.split() if t and t not in _STOPWORDS)


def near_duplicate_titles(a: str, b: str, *, threshold: float = 0.9) -> bool:
    """Normalised token Jaccard similarity (M13 spec §2's own dedup rule, reused here
    verbatim): ``>= threshold`` counts as the same story republished/re-titled."""
    tokens_a, tokens_b = _title_tokens(a), _title_tokens(b)
    if not tokens_a or not tokens_b:
        return False
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    if union == 0:
        return False
    return (intersection / union) >= threshold


__all__ = [
    "BULLETIN_MARKERS",
    "PROMO_MARKERS",
    "VideoCandidate",
    "is_promo",
    "is_short",
    "matches_bulletin_markers",
    "near_duplicate_titles",
]
