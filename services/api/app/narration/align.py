"""Where did the speech stop? Align a spoken transcript with the narration plan.

The realtime provider speaks the text a tool handed it, and the client reports the
assistant transcript spoken so far when the owner interrupts (M16 spec §3.2). This
module turns that transcript into a semantic cursor: the last sentence that was fully
spoken, counted from the cursor the speech started at. "Devam et" then resumes at the
next sentence - the same semantic point, not a replayed audio offset.

Matching is forgiving on purpose: the provider's transcript is its own rendering of the
text (numbers may come back as words, punctuation and casing vary), so sentences are
compared after Turkish-safe folding, with a token-overlap threshold rather than
equality. The transcript is used and dropped; nothing here stores it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.narration.engine import PARAGRAPH_HEADING, Cursor, NarrationPlan

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

#: A sentence counts as spoken when at least this share of its tokens appears in the
#: transcript window. Low enough to survive the provider's paraphrase of numbers, high
#: enough that a sentence that was barely started does not count as finished.
MATCH_THRESHOLD = 0.6
#: Below this many tokens a sentence is matched by exact folded prefix instead of
#: overlap ("Bilginize." must not be "found" inside unrelated speech).
_SHORT_SENTENCE_TOKENS = 3


def _fold(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(_fold(text))


@dataclass(frozen=True, slots=True)
class Alignment:
    """The result: the cursor to resume from and how sure the match is."""

    cursor: Cursor | None
    spoken_chunks: int
    matched_ratio: float
    complete: bool  # the whole section from the start cursor was spoken

    def as_dict(self) -> dict[str, object]:
        return {
            "cursor": self.cursor.as_dict() if self.cursor else None,
            "spoken_chunks": self.spoken_chunks,
            "matched_ratio": round(self.matched_ratio, 3),
            "complete": self.complete,
        }


def _sentence_spoken(
    sentence_tokens: list[str], transcript_tokens: list[str], position: int
) -> tuple[bool, int]:
    """Whether ``sentence_tokens`` appear (in order, loosely) in the transcript at or
    after ``position``. Returns (spoken, new_position)."""
    if not sentence_tokens:
        return True, position
    window = transcript_tokens[position:]
    if not window:
        return False, position
    if len(sentence_tokens) < _SHORT_SENTENCE_TOKENS:
        n = len(sentence_tokens)
        for offset in range(0, max(1, len(window) - n + 1)):
            if window[offset : offset + n] == sentence_tokens:
                return True, position + offset + n
        return False, position
    # order-preserving greedy match
    cursor = 0
    hits = 0
    last_hit = 0
    for tok in sentence_tokens:
        while cursor < len(window) and window[cursor] != tok:
            cursor += 1
        if cursor < len(window):
            hits += 1
            cursor += 1
            last_hit = cursor
        else:
            cursor = last_hit  # do not run past the window for one missing token
    ratio = hits / len(sentence_tokens)
    if ratio >= MATCH_THRESHOLD:
        return True, position + last_hit
    return False, position


def align(plan: NarrationPlan, start: Cursor | None, spoken: str) -> Alignment:
    """Cursor of the first sentence NOT yet spoken, starting from ``start``.

    The section the speech started in bounds the search (the tool hands out one
    section at a time). If nothing matched, the cursor stays at ``start``: the owner
    interrupted before the first sentence ended, and "devam" repeats it.
    """
    if not plan.chunks:
        return Alignment(cursor=None, spoken_chunks=0, matched_ratio=0.0, complete=False)
    start_index = plan.index_of(start)
    section_id = plan.chunks[start_index].cursor.section_id
    transcript_tokens = _tokens(spoken)
    position = 0
    spoken_chunks = 0
    considered = 0
    resume: Cursor | None = plan.chunks[start_index].cursor
    complete = True
    for ch in plan.chunks[start_index:]:
        if ch.cursor.section_id != section_id:
            break
        para = plan.paragraphs.get(ch.cursor.paragraph_id)
        if para is not None and para.kind == PARAGRAPH_HEADING:
            continue
        considered += 1
        ok, position = _sentence_spoken(_tokens(ch.text), transcript_tokens, position)
        if not ok:
            resume = ch.cursor
            complete = False
            break
        spoken_chunks += 1
        resume = plan.next_cursor(ch.cursor)
    ratio = spoken_chunks / considered if considered else 0.0
    if complete and resume is not None and resume.section_id != section_id:
        # the whole section was spoken; resuming continues into the next one
        pass
    return Alignment(
        cursor=resume, spoken_chunks=spoken_chunks, matched_ratio=ratio, complete=complete
    )


__all__ = ["MATCH_THRESHOLD", "Alignment", "align"]
