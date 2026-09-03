"""Untrusted-content injection markers (M13, contract §6).

Page text is data, never instructions (CLAUDE.md browser rule + contract §6).
This module supplies the one thing the worker is allowed to do with page
text beyond quoting it verbatim: count how many instruction-like patterns it
contains, so Cloud Core can flag the evidence (``injection_suspected``) and
keep it as quoted, delimited data rather than folding it into a synthesis
prompt. The worker itself never acts on a match — no navigation, click,
download or config change is ever driven by page content (that guarantee
lives in the worker's dispatch loop: the only inputs to any operation are
command payloads).

``MARKERS`` is written **verbatim** from
``packages/protocol/BROWSER_CAPABILITIES.md`` §6 so that if/when
``packages/protocol/browser-injection-markers.json`` exists (authored by a
parallel M13 track from the same §6 list) the two stay byte-identical; see
``tests/unit/test_injection.py``.
"""

from __future__ import annotations

import re
import unicodedata

#: Verbatim from BROWSER_CAPABILITIES.md §6 (English then Turkish), in the
#: order the contract lists them. Each entry is a regex pattern (some are
#: plain substrings — a plain string is also a valid, literal regex).
MARKERS: tuple[str, ...] = (
    r"ignore (all|previous|prior) instructions",
    r"system prompt",
    r"(reveal|print) your (instructions|prompt|secrets)",
    r"(execute|run) (the|this) command",
    r"upload",
    r"install",
    r"change (the )?policy",
    r"you are now",
    r"as an ai",
    r"assistant:",
    r"önceki talimatları yok say",
    r"komutu çalıştır",
    r"şifreyi göster",
)

_COMPILED: tuple[re.Pattern[str], ...] = tuple(
    re.compile(marker, re.IGNORECASE) for marker in MARKERS
)

# Zero-width / joiner code points an adversarial page can drop into a phrase so a
# human still reads "ignore previous instructions" while a regex sees two words.
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")
_WHITESPACE = re.compile(r"\s+")


def normalize_for_markers(text: str) -> str:
    """Fold the cheap evasions before matching (contract §6, both sides).

    NFKC maps fullwidth/compatibility forms (``ｉｇｎｏｒｅ`` → ``ignore``, NBSP →
    space), zero-width characters are removed, and any whitespace run collapses
    to one space so line breaks/tabs inside a phrase do not split it. Homoglyphs
    from other scripts are deliberately not folded here (a confusables table is
    a larger, separate decision); the boundary itself is structural and does
    not depend on this detector.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text)
    folded = _ZERO_WIDTH.sub("", folded)
    return _WHITESPACE.sub(" ", folded)


def count_injection_markers(text: str) -> int:
    """Total number of marker-pattern matches found in ``text``.

    Sums every non-overlapping match of every pattern (not just how many
    distinct patterns matched) — a page repeating "ignore previous
    instructions" three times is a stronger signal than one that says it
    once, and the caller (``browser.fetch_evidence``/``browser.extract``)
    surfaces the raw count rather than a boolean so Cloud Core can apply its
    own threshold.
    """
    if not text:
        return 0
    folded = normalize_for_markers(text)
    return sum(len(pattern.findall(folded)) for pattern in _COMPILED)


__all__ = ["MARKERS", "count_injection_markers", "normalize_for_markers"]
