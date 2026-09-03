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

#: Verbatim from BROWSER_CAPABILITIES.md §6 (English then Turkish), in the
#: order the contract lists them. Each entry is a regex pattern (some are
#: plain substrings — a plain string is also a valid, literal regex).
MARKERS: tuple[str, ...] = (
    r"ignore (all|previous|prior) instructions",
    r"system prompt",
    r"reveal|print your (instructions|prompt|secrets)",
    r"execute|run (the|this) command",
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
    return sum(len(pattern.findall(text)) for pattern in _COMPILED)


__all__ = ["MARKERS", "count_injection_markers"]
