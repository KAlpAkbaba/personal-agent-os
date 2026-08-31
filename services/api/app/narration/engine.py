"""Narration planner: Markdown artifact -> stable semantic segments -> ordered
narration chunks, with an ahead-of-playback planner, a chunk cache, and a TTS
provider seam (VOICE_SPEC §3, API_AND_PROTOCOLS §7).

The cursor is the source of truth and is stable across regenerated audio: IDs are
derived only from document structure (section/paragraph/sentence), never from
voice settings or audio bytes. Audio is produced lazily through an *injected*
``Synthesizer`` (a Protocol); tests pass a deterministic fake. No real TTS
provider is implemented here — that seam is owned by the voice provider engineer.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from app.narration import tables
from app.narration.normalizer import normalize

# ------------------------------------------------------------------- cursor


@dataclass(frozen=True)
class Cursor:
    """Semantic narration position (API_AND_PROTOCOLS §7)."""

    section_id: str
    paragraph_id: str
    sentence_index: int
    char_offset: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "section_id": self.section_id,
            "paragraph_id": self.paragraph_id,
            "sentence_index": self.sentence_index,
            "char_offset": self.char_offset,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object] | None) -> Cursor | None:
        if not data or "section_id" not in data:
            return None
        return cls(
            section_id=str(data["section_id"]),
            paragraph_id=str(data["paragraph_id"]),
            sentence_index=int(data.get("sentence_index", 0)),  # type: ignore[arg-type]
            char_offset=int(data.get("char_offset", 0)),  # type: ignore[arg-type]
        )


# ------------------------------------------------------------------- settings


@dataclass(frozen=True)
class VoiceSettings:
    voice: str = "default"
    speed: float = 1.0
    locale: str = "tr-TR"

    def cache_token(self) -> str:
        return f"{self.locale}|{self.voice}|{self.speed:.3f}"


# ------------------------------------------------------------------- segments

PARAGRAPH_TEXT = "text"
PARAGRAPH_HEADING = "heading"
PARAGRAPH_TABLE = "table"
PARAGRAPH_CODE = "code"
PARAGRAPH_LIST = "list"


@dataclass
class Sentence:
    index: int
    text: str  # normalized (spoken) text


@dataclass
class Paragraph:
    id: str
    section_id: str
    kind: str
    raw: str
    sentences: list[Sentence] = field(default_factory=list)


@dataclass
class Section:
    id: str
    index: int
    title: str
    level: int
    paragraph_ids: list[str] = field(default_factory=list)


@dataclass
class Chunk:
    """One ordered unit of narration (a sentence, or a whole table/code block)."""

    chunk_id: str
    cursor: Cursor
    kind: str
    text: str


@dataclass
class NarrationPlan:
    artifact_id: str
    version: int
    sections: list[Section]
    paragraphs: dict[str, Paragraph]
    chunks: list[Chunk]

    def index_of(self, cursor: Cursor | None) -> int:
        """First chunk index at/after ``cursor``; 0 if cursor is None."""
        if cursor is None:
            return 0
        for i, ch in enumerate(self.chunks):
            c = ch.cursor
            if (
                c.section_id == cursor.section_id
                and c.paragraph_id == cursor.paragraph_id
                and c.sentence_index >= cursor.sentence_index
            ):
                return i
        # cursor past this section: find first chunk whose paragraph is after
        for i, ch in enumerate(self.chunks):
            if ch.cursor.paragraph_id == cursor.paragraph_id:
                return i
        return 0

    def chunk_at(self, cursor: Cursor | None) -> Chunk | None:
        if not self.chunks:
            return None
        return self.chunks[self.index_of(cursor)]

    def next_cursor(self, cursor: Cursor | None) -> Cursor | None:
        idx = self.index_of(cursor)
        if idx + 1 < len(self.chunks):
            return self.chunks[idx + 1].cursor
        return None

    def paragraph_start_cursor(self, paragraph_id: str) -> Cursor | None:
        for ch in self.chunks:
            if ch.cursor.paragraph_id == paragraph_id:
                return ch.cursor
        return None

    def next_section_cursor(self, cursor: Cursor | None) -> Cursor | None:
        current_section = cursor.section_id if cursor else None
        seen_current = current_section is None
        for ch in self.chunks:
            if ch.cursor.section_id == current_section:
                seen_current = True
                continue
            if seen_current and ch.cursor.section_id != current_section:
                return ch.cursor
        return None


# --------------------------------------------------------------- segmentation

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    return [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]


def _blocks(body: str) -> Iterable[tuple[str, str]]:
    """Yield (kind, raw_block) in document order.

    Splits the Markdown body into headings, fenced code blocks, tables, list
    groups and text paragraphs. Blank lines separate text paragraphs.
    """
    lines = body.replace("\r\n", "\n").split("\n")
    i = 0
    n = len(lines)
    buf: list[str] = []

    def flush_text() -> Iterable[tuple[str, str]]:
        nonlocal buf
        if buf:
            joined = "\n".join(buf).strip()
            if joined:
                yield (PARAGRAPH_TEXT, joined)
            buf = []

    while i < n:
        line = lines[i]
        # fenced code block
        if line.lstrip().startswith("```"):
            yield from flush_text()
            fence = line.strip()[:3]
            start = i
            i += 1
            while i < n and not lines[i].strip().startswith(fence):
                i += 1
            block = "\n".join(lines[start : i + 1])
            i += 1
            yield (PARAGRAPH_CODE, block)
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            yield from flush_text()
            yield (PARAGRAPH_HEADING, line.strip())
            i += 1
            continue
        # table: a line with '|' followed by a separator row
        if "|" in line and i + 1 < n and re.search(r"\|?\s*:?-{2,}:?\s*\|", lines[i + 1]):
            yield from flush_text()
            start = i
            while i < n and "|" in lines[i]:
                i += 1
            yield (PARAGRAPH_TABLE, "\n".join(lines[start:i]))
            continue
        if _LIST_RE.match(line):
            yield from flush_text()
            start = i
            while i < n and (
                _LIST_RE.match(lines[i])
                or (lines[i].strip() and lines[i].startswith(" "))
            ):
                i += 1
            yield (PARAGRAPH_LIST, "\n".join(lines[start:i]))
            continue
        if not line.strip():
            yield from flush_text()
            i += 1
            continue
        buf.append(line)
        i += 1
    yield from flush_text()


def build_plan(
    body: str,
    *,
    artifact_id: str,
    version: int = 1,
    mode: str = "narration",
    pronunciation: dict[str, str] | None = None,
    read_headings: bool = True,
    literal_tables: bool = False,
) -> NarrationPlan:
    """Segment a canonical Markdown ``body`` into a :class:`NarrationPlan`.

    Section IDs are ``s1..sN`` in document order; paragraph IDs are ``p1..pM``
    document-wide (unique); sentence_index is 0-based within a paragraph. These
    IDs depend only on structure, so they are stable across re-synthesis.
    """
    sections: list[Section] = []
    paragraphs: dict[str, Paragraph] = {}
    chunks: list[Chunk] = []

    section_counter = 0
    paragraph_counter = 0

    # Everything before the first heading lives in an implicit section s1.
    current_section = Section(id="s1", index=0, title="", level=0)
    section_counter = 1
    sections.append(current_section)

    def add_paragraph(kind: str, raw: str, sentence_texts: list[str]) -> None:
        nonlocal paragraph_counter
        paragraph_counter += 1
        pid = f"p{paragraph_counter}"
        para = Paragraph(id=pid, section_id=current_section.id, kind=kind, raw=raw)
        for idx, st in enumerate(sentence_texts):
            para.sentences.append(Sentence(index=idx, text=st))
            cursor = Cursor(section_id=current_section.id, paragraph_id=pid, sentence_index=idx)
            chunks.append(
                Chunk(
                    chunk_id=f"{current_section.id}:{pid}:{idx}",
                    cursor=cursor,
                    kind=kind,
                    text=st,
                )
            )
        paragraphs[pid] = para
        current_section.paragraph_ids.append(pid)

    for kind, raw in _blocks(body):
        if kind == PARAGRAPH_HEADING:
            m = _HEADING_RE.match(raw)
            level = len(m.group(1)) if m else 1
            title = m.group(2).strip() if m else raw
            section_counter += 1
            current_section = Section(
                id=f"s{section_counter}", index=section_counter - 1, title=title, level=level
            )
            sections.append(current_section)
            if read_headings:
                spoken = normalize(title, mode=mode, pronunciation=pronunciation)
                add_paragraph(PARAGRAPH_HEADING, raw, [spoken] if spoken else [])
            continue
        if kind == PARAGRAPH_TABLE:
            spoken = tables.narrate_table(
                raw, literal=literal_tables, mode=mode, pronunciation=pronunciation
            ).text
            add_paragraph(PARAGRAPH_TABLE, raw, [spoken] if spoken else [])
            continue
        if kind == PARAGRAPH_CODE:
            spoken = tables.narrate_code_or_log(raw)
            add_paragraph(PARAGRAPH_CODE, raw, [spoken] if spoken else [])
            continue
        if kind == PARAGRAPH_LIST:
            # Read each list item as its own sentence-chunk.
            items = []
            for ln in raw.splitlines():
                stripped = _LIST_RE.sub("", ln).strip()
                if stripped:
                    items.append(normalize(stripped, mode=mode, pronunciation=pronunciation))
            add_paragraph(PARAGRAPH_LIST, raw, items)
            continue
        # text paragraph
        spoken = normalize(raw, mode=mode, pronunciation=pronunciation)
        add_paragraph(PARAGRAPH_TEXT, raw, split_sentences(spoken))

    # Drop empty implicit s1 if it has no paragraphs.
    if not sections[0].paragraph_ids:
        sections = [s for s in sections if s.paragraph_ids or s is not sections[0]]

    return NarrationPlan(
        artifact_id=artifact_id,
        version=version,
        sections=sections,
        paragraphs=paragraphs,
        chunks=chunks,
    )


# ---------------------------------------------------------------- TTS seam


class Synthesizer(Protocol):
    """The TTS provider seam. Implementations live in ``app/voice``; this module
    only ever calls this method and never imports a concrete provider."""

    def synthesize(self, text: str, settings: VoiceSettings) -> bytes: ...


@dataclass
class SynthResult:
    chunk_id: str
    audio: bytes
    cache_key: str
    from_cache: bool


# ---------------------------------------------------------- planner + cache


class ChunkCache:
    """In-memory cache of synthesized audio keyed by
    (artifact_id, version, chunk_id, voice cache token)."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    @staticmethod
    def key(artifact_id: str, version: int, chunk_id: str, settings: VoiceSettings) -> str:
        raw = f"{artifact_id}|{version}|{chunk_id}|{settings.cache_token()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    def put(self, key: str, audio: bytes) -> None:
        self._store[key] = audio

    def discard(self, key: str) -> None:
        self._store.pop(key, None)

    def __len__(self) -> int:
        return len(self._store)

    def keys(self) -> set[str]:
        return set(self._store)


class NarrationEngine:
    """Drives ahead-of-playback synthesis with a chunk cache and cancellation of
    unused future chunks. Deterministic given a deterministic ``Synthesizer``."""

    def __init__(
        self,
        synthesizer: Synthesizer,
        *,
        lookahead: int = 3,
        cache: ChunkCache | None = None,
    ) -> None:
        self._synth = synthesizer
        self.lookahead = lookahead
        self.cache = cache or ChunkCache()
        # chunk_id -> cache_key currently "planned"/materialized ahead of play
        self._planned: dict[str, str] = {}

    def plan_window(self, plan: NarrationPlan, cursor: Cursor | None) -> list[Chunk]:
        """The chunks that should be pre-generated now: the current chunk plus
        ``lookahead`` following chunks."""
        start = plan.index_of(cursor)
        return plan.chunks[start : start + self.lookahead + 1]

    def ensure_ahead(
        self, plan: NarrationPlan, cursor: Cursor | None, settings: VoiceSettings
    ) -> list[SynthResult]:
        """Synthesize (or cache-hit) every chunk in the current window and cancel
        any previously-planned future chunk that is no longer needed."""
        window = self.plan_window(plan, cursor)
        wanted_keys: dict[str, str] = {}
        results: list[SynthResult] = []
        for ch in window:
            key = ChunkCache.key(plan.artifact_id, plan.version, ch.chunk_id, settings)
            wanted_keys[ch.chunk_id] = key
            cached = self.cache.get(key)
            if cached is not None:
                results.append(SynthResult(ch.chunk_id, cached, key, from_cache=True))
            else:
                audio = self._synth.synthesize(ch.text, settings)
                self.cache.put(key, audio)
                results.append(SynthResult(ch.chunk_id, audio, key, from_cache=False))
        self.cancel_unused(wanted_keys)
        self._planned = wanted_keys
        return results

    def cancel_unused(self, wanted: dict[str, str]) -> list[str]:
        """Evict cache entries for chunks that were planned before but are not in
        the new window. Returns the list of cancelled chunk_ids."""
        cancelled: list[str] = []
        for chunk_id, key in list(self._planned.items()):
            if chunk_id not in wanted:
                self.cache.discard(key)
                cancelled.append(chunk_id)
        return cancelled

    def synthesize_chunk(
        self, plan: NarrationPlan, chunk: Chunk, settings: VoiceSettings
    ) -> SynthResult:
        key = ChunkCache.key(plan.artifact_id, plan.version, chunk.chunk_id, settings)
        cached = self.cache.get(key)
        if cached is not None:
            return SynthResult(chunk.chunk_id, cached, key, from_cache=True)
        audio = self._synth.synthesize(chunk.text, settings)
        self.cache.put(key, audio)
        return SynthResult(chunk.chunk_id, audio, key, from_cache=False)


__all__ = [
    "Cursor",
    "VoiceSettings",
    "Section",
    "Paragraph",
    "Sentence",
    "Chunk",
    "NarrationPlan",
    "build_plan",
    "split_sentences",
    "Synthesizer",
    "SynthResult",
    "ChunkCache",
    "NarrationEngine",
    "PARAGRAPH_TEXT",
    "PARAGRAPH_HEADING",
    "PARAGRAPH_TABLE",
    "PARAGRAPH_CODE",
    "PARAGRAPH_LIST",
]
