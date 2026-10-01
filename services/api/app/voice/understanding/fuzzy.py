"""Fuzzy string similarity over the folded Turkish form (ADR-0224 layer 2).

Pure Python, no dependency (the integrator's record: rapidfuzz rejected for now). The
distance is optimal-string-alignment Damerau-Levenshtein: insert, delete, substitute and
an adjacent transposition each cost one. ``fold`` is local on purpose - layer 1 may not be
merged, and this module must not depend on it.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from typing import Final

#: The most the length difference may be before two strings are not compared at all: a pair
#: that far apart cannot reach the floor a caller uses, and skipping the table is the cost cut.
MAX_LENGTH_GAP: Final = 3

_FOLD_TABLE: Final = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def fold(text: str) -> str:
    """Turkish-aware casefold: I is dotless (ı) and İ is dotted (i) BEFORE lowering, then
    the diacritics go, so "Ofisü" and "ofis" differ by one letter and "ISIK" is "isik"."""
    text = unicodedata.normalize("NFKC", text).replace("I", "ı").replace("İ", "i")
    return text.lower().translate(_FOLD_TABLE)


def distance(a: str, b: str) -> int:
    """Optimal-string-alignment distance between two strings as given (no folding)."""
    la, lb = len(a), len(b)
    if not la:
        return lb
    if not lb:
        return la
    prev2: list[int] = []
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            best = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                best = min(best, prev2[j - 2] + 1)
            cur[j] = best
        prev2, prev = prev, cur
    return prev[lb]


@lru_cache(maxsize=65536)
def similarity(a: str, b: str) -> float:
    """``1 - distance / longest`` over the folded forms, in [0, 1]; 0.0 when either is empty
    or the lengths differ by more than ``MAX_LENGTH_GAP``."""
    fa, fb = fold(a), fold(b)
    if not fa or not fb or abs(len(fa) - len(fb)) > MAX_LENGTH_GAP:
        return 0.0
    return 1.0 - distance(fa, fb) / max(len(fa), len(fb))
