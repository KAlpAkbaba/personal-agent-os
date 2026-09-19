"""Where on a window a piece of NAMED TEXT is, from the device's own OCR (ADR-0176).

Owner decision 2026-09-19: "Bunun yerine local OCR yapsak sorun daha hızlı düzelir mi ve
maddiyatı düşer mi?" - yes to all three. ``screen.ocr`` runs Windows' recogniser on the PC at
full resolution and sends back words and boxes (a few KB): nothing is paid per look, the
picture never leaves the machine, and the 1 MiB broker frame that forced a photographic page
down to 644x354 - where no title can be read - stops mattering.

This module is the pure half: given the recognised lines and what the owner said, WHICH box
is meant. It never talks to a device and never raises for "not found".

Matching is forgiving in exactly the ways a spoken name differs from a printed title:

* Turkish letters and case are folded on both sides ("üçkağıtçı" / "ÜÇ KAĞITÇI").
* Spaces do not count: the recogniser of SPEECH writes "üçkağıtçı", the page prints
  "Üç Kağıtçı" - compared squashed, they are the same nine letters.
* A spoken word may carry a suffix the title does not, or the reverse ("tatili" / "tatil").
* A title wrapped over two lines is one title.

And it is strict where a wrong click is likely: text in the top band of a browser window is
the address bar and the site's own search box - after "search first", the owner's words are
printed THERE too, and clicking them opens nothing. A match lower on the page always wins
over one in that band.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

#: The part of a window, from the top, that is browser chrome and the site's search box.
TOP_BAND_FRACTION = 0.16
#: How much of the spoken name a line must carry to be the one meant.
MIN_COVERAGE = 0.75
#: A spoken word and a printed one are the same word when one begins with the other and the
#: shared stem is at least this long ("tatili"/"tatil", never "en"/"eniyi").
MIN_STEM = 4

_FOLD = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "I": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ü": "u",
        "Ü": "u",
        "ö": "o",
        "Ö": "o",
        "ç": "c",
        "Ç": "c",
        "â": "a",
        "î": "i",
        "û": "u",
    }
)


@dataclass(frozen=True, slots=True)
class OcrHit:
    """The point to click (image pixels), the text that matched, and how well."""

    x: int
    y: int
    text: str
    score: float
    in_top_band: bool


def fold(text: str) -> str:
    """Lower-case, Turkish letters and accents folded, everything but letters/digits a space."""
    folded = unicodedata.normalize("NFKD", text.translate(_FOLD)).lower()
    kept = "".join(ch if ch.isalnum() else " " for ch in folded if not unicodedata.combining(ch))
    return " ".join(kept.split())


def _same_word(spoken: str, printed: str) -> bool:
    if spoken == printed:
        return True
    shorter, longer = sorted((spoken, printed), key=len)
    return len(shorter) >= MIN_STEM and longer.startswith(shorter)


def _coverage(target_tokens: list[str], line_tokens: list[str]) -> float:
    if not target_tokens:
        return 0.0
    hit = sum(1 for t in target_tokens if any(_same_word(t, w) for w in line_tokens))
    return hit / len(target_tokens)


def _box(item: dict[str, Any]) -> tuple[int, int, int, int] | None:
    try:
        x, y = int(item["x"]), int(item["y"])
        w, h = int(item["width"]), int(item["height"])
    except (KeyError, TypeError, ValueError):
        return None
    return (x, y, w, h) if w > 0 and h > 0 else None


def _union(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    right = max(b[0] + b[2] for b in boxes)
    bottom = max(b[1] + b[3] for b in boxes)
    return left, top, right - left, bottom - top


def _matched_words_box(
    line: dict[str, Any], target_tokens: list[str], squashed_target: str
) -> tuple[int, int, int, int] | None:
    """The box of the WORDS that matched, so a long line is clicked on the name and not on
    whatever else shares the line ("... | 4K ULTRA HD | KEMAL SUNAL")."""
    boxes: list[tuple[int, int, int, int]] = []
    for word in line.get("words") or ():
        if not isinstance(word, dict):
            continue
        folded = fold(str(word.get("text") or "")).replace(" ", "")
        if not folded:
            continue
        in_squash = len(folded) >= 2 and folded in squashed_target
        if in_squash or any(_same_word(t, folded) for t in target_tokens):
            box = _box(word)
            if box is not None:
                boxes.append(box)
    return _union(boxes) if boxes else None


def _candidates(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every line, plus every pair of neighbouring lines read as one (a wrapped title)."""
    out = [line for line in lines if isinstance(line, dict) and _box(line) is not None]
    singles = list(out)
    for upper, lower in zip(singles, singles[1:], strict=False):
        ub, lb = _box(upper), _box(lower)
        if ub is None or lb is None:
            continue
        gap = lb[1] - (ub[1] + ub[3])
        aligned = abs(lb[0] - ub[0]) <= max(ub[3], lb[3]) * 2
        if aligned and -ub[3] // 2 <= gap <= max(ub[3], lb[3]) * 1.5:
            left, top, width, height = _union([ub, lb])
            out.append(
                {
                    "text": f"{upper.get('text', '')} {lower.get('text', '')}",
                    "x": left,
                    "y": top,
                    "width": width,
                    "height": height,
                    "words": [*(upper.get("words") or ()), *(lower.get("words") or ())],
                    "_pair": True,
                }
            )
    return out


def find_text(lines: list[dict[str, Any]], name: str, *, image_height: int) -> OcrHit | None:
    """The best place ``name`` is printed, or None when no line carries enough of it."""
    target = fold(name)
    target_tokens = [t for t in target.split() if t]
    squashed_target = target.replace(" ", "")
    if len(squashed_target) < 2:
        return None
    band = max(0, int(image_height * TOP_BAND_FRACTION))
    hits: list[OcrHit] = []
    for line in _candidates(lines):
        folded = fold(str(line.get("text") or ""))
        if not folded:
            continue
        if squashed_target in folded.replace(" ", ""):
            score = 1.0
        else:
            score = _coverage(target_tokens, folded.split())
            if score < MIN_COVERAGE:
                continue
        box = _matched_words_box(line, target_tokens, squashed_target) or _box(line)
        if box is None:
            continue
        # A pair is never better than the single line that already says it all.
        if line.get("_pair"):
            score -= 0.01
        x, y = box[0] + box[2] // 2, box[1] + box[3] // 2
        hits.append(OcrHit(x, y, str(line.get("text") or "")[:120], score, y < band))
    if not hits:
        return None
    # Below the band first, then the fuller match, then the one higher on the page.
    hits.sort(key=lambda h: (h.in_top_band, -h.score, h.y, h.x))
    return hits[0]


__all__ = ["MIN_COVERAGE", "TOP_BAND_FRACTION", "OcrHit", "find_text", "fold"]
