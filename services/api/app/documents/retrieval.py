"""Deterministic block retrieval (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3).

The retriever chooses the ``refs`` an answer cites; the cognitive backend, when
configured, only rewrites prose around the SAME refs (ADR-0083 decision 2) — nothing here
ever calls a model. Two passes, in order:

1. **Literal reference matching** — an ordinal + a place noun ("üçüncü sayfa", "dördüncü
   slayt") or a named key/heading/symbol the question mentions resolves directly to the
   block that owns it, without needing token overlap at all. Numbers and cell/page
   references are matched literally (spec §3).
2. **Turkish casefold + diacritics-insensitive token overlap** — every other question is
   scored by how many of its content words appear in a block's own text (plus its
   ``title``/``sheet`` when the block carries one — a heading's own name is content too).
   An EXACT normalized-word match counts for more than a shared-prefix match (Turkish
   suffixes vary: "ödeme" / "ödemesi") so that a proper noun in the question ("Kerem")
   cannot be out-scored by an unrelated row that only shares a column-header's word
   stem ("Şehir" / "şehirde") — a genuine ambiguity the CSV oracle question exercises.

``I`` -> ``ı`` and ``İ`` -> ``i`` are both accepted (the same casefold M19's intent
resolver needed), so "İzmir" and "izmir" — and an ASR's plain-ASCII "Izmir" — all match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.narration.commands import _ORDINAL_WORDS

#: Ordinal word -> 1-based index ("üçüncü" -> 3), reused rather than re-typed (the same
#: table app.voice.intents.resolve_intent's own item navigation already keys on).
_ORDINALS: dict[str, int] = dict(_ORDINAL_WORDS)

MIN_PREFIX_OVERLAP = 4

#: Turkish function words a content-word overlap score must ignore — otherwise every
#: question sharing "ne"/"var"/"kaç" with an unrelated block would out-score genuine
#: matches. Deliberately small: this is a stoplist for RETRIEVAL scoring, not a second
#: intent-routing vocabulary (that stays in app.voice.intents).
_STOPWORDS: frozenset[str] = frozenset(
    {
        "ne",
        "nedir",
        "kaç",
        "kac",
        "var",
        "yok",
        "mı",
        "mi",
        "mu",
        "mü",
        "nasıl",
        "nasil",
        "kim",
        "hangi",
        "hangisi",
        "bu",
        "bunun",
        "bunu",
        "şu",
        "su",
        "onun",
        "onu",
        "için",
        "icin",
        "ile",
        "de",
        "da",
        "ki",
        "bir",
        "ve",
        "ya",
        "mı?",
        "midir",
        "nedir?",
        "kaçıncı",
        "kacinci",
        "kaçında",
        "kacinda",
        "kaçtır",
        "kactir",
        "diye",
        "ama",
        "gibi",
    }
)

_WORD_RE = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)


def _casefold(text: str) -> str:
    """Turkish casefold: ``İ`` -> ``i``, ``I`` -> ``ı`` (str.lower gets the ASCII ``I``
    wrong for Turkish — the same reason app.voice.intents.turkish_casefold exists)."""
    return text.replace("İ", "i").replace("I", "ı").lower()


#: Public alias — a natural-Turkish-spelling display form still needs this casefold
#: without the diacritics-stripping :func:`normalize_word` also applies (used by
#: ``app.documents.answers.common_points`` so a spoken common point reads "bütçe", not
#: the ASCII-folded internal matching key "butce").
turkish_casefold = _casefold

_DIACRITICS_TABLE = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")


def strip_diacritics(text: str) -> str:
    return text.translate(_DIACRITICS_TABLE)


def normalize_word(word: str) -> str:
    """The comparison key for one token: Turkish-casefolded, diacritics stripped."""
    return strip_diacritics(_casefold(word))


def tokenize(text: str) -> list[str]:
    """Raw words (letters or digit runs), in order, unfiltered."""
    if not text:
        return []
    return _WORD_RE.findall(text)


def is_stopword(normalized: str) -> bool:
    """Whether an already-:func:`normalize_word`-d token is a retrieval stopword."""
    return normalized in _STOPWORDS


def content_words(text: str) -> list[str]:
    """Normalized tokens with stopwords and single characters dropped — what a QUESTION's
    overlap score is computed from."""
    out = []
    for tok in tokenize(text):
        norm = normalize_word(tok)
        if len(norm) < 2 or norm in _STOPWORDS or tok.isdigit():
            continue
        out.append(norm)
    return out


def shares_prefix(a: str, b: str) -> bool:
    """Whether two ALREADY-NORMALIZED words count as the same content word: equal, or
    sharing a prefix of at least :data:`MIN_PREFIX_OVERLAP` characters (Turkish suffixes
    vary: "ödeme" / "ödemesi")."""
    if a == b:
        return True
    shortest = min(len(a), len(b))
    if shortest < MIN_PREFIX_OVERLAP:
        return False
    return a[:MIN_PREFIX_OVERLAP] == b[:MIN_PREFIX_OVERLAP]


#: Back-compat alias for call sites that predate the public name.
_shares_prefix = shares_prefix


def _block_text_for_index(block: dict[str, Any]) -> str:
    """The text a block is scored on: its own text, plus a ``title``/``sheet`` name when
    present — a heading's or a slide's own name is content an owner may ask by (spec §3:
    "sheet:Ozet!A5:B5" -> "Ozet sayfası"; "h2:Kararlar" -> "Kararlar bölümü")."""
    parts = [str(block.get("text") or "")]
    for key in ("title", "sheet"):
        value = block.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def score_block(question_words: list[str], block: dict[str, Any]) -> int:
    """A higher score for an EXACT normalized-word match than a shared-prefix-only match:
    a stemmed overlap ("şehirde" / "şehir") is real evidence, but weaker than an exact hit
    ("Kerem" / "Kerem") — without this weighting, a question naming a proper noun that
    also happens to share a stem with an unrelated row's column header (a CSV's own
    header row, itself just another block/ref) can tie a genuine match and lose the tie
    to whichever block sorts first."""
    block_words = {normalize_word(w) for w in tokenize(_block_text_for_index(block))}
    score = 0
    for qw in question_words:
        if qw in block_words:
            score += 2
        elif any(_shares_prefix(qw, bw) for bw in block_words):
            score += 1
    return score


# ------------------------------------------------------------- literal references

#: Place nouns -> the ref prefix they build, per doc kind. A docx paragraph and a pdf page
#: happen to share the "p<n>" prefix (spec §2) — kept separate here by KIND, never guessed.
_PLACE_NOUNS_BY_KIND: dict[str, tuple[tuple[str, ...], str]] = {
    "pdf": (("sayfa",), "p"),
    "docx": (("paragraf",), "p"),
    "txt": (("paragraf",), "p"),
    "pptx": (("slayt", "slayd"), "s"),
    "csv": (("satır", "satir"), "r"),
}


def _ordinal_in(tokens: list[str]) -> int | None:
    for tok in tokens:
        norm = normalize_word(tok)
        if norm in _ORDINALS:
            return _ORDINALS[norm]
        for word, idx in _ORDINALS.items():
            if len(word) > 3 and norm.startswith(normalize_word(word)):
                return idx
    return None


def literal_ref(question: str, *, kind: str, structure: dict[str, Any]) -> str | None:
    """A ref the question names directly, without needing overlap scoring at all."""
    tokens = tokenize(question)
    normalized = [normalize_word(t) for t in tokens]

    # 1. ordinal + place noun ("üçüncü sayfa" -> p3, "dördüncü slayt" -> s4).
    place = _PLACE_NOUNS_BY_KIND.get(kind)
    if place is not None:
        nouns, prefix = place
        if any(any(n.startswith(normalize_word(w)) for w in nouns) for n in normalized):
            ordinal = _ordinal_in(tokens)
            if ordinal is not None:
                return f"{prefix}{ordinal}"

    # 2. a JSON top-level key the question names ("ses" -> "$.ses").
    if kind == "json":
        for key in structure.get("keys") or []:
            key_norm = normalize_word(str(key))
            if any(_shares_prefix(key_norm, n) for n in normalized):
                return f"$.{key}"

    # 3. an md heading title the question names ("Kararlar" -> "h2:Kararlar").
    if kind == "md":
        for heading in structure.get("headings") or []:
            heading_words = [normalize_word(w) for w in tokenize(str(heading))]
            if heading_words and all(
                any(_shares_prefix(hw, qn) for qn in normalized) for hw in heading_words
            ):
                # The level is not in structure.headings; the caller resolves the exact
                # "h<level>:<title>" ref from the doc's own blocks (this only names WHICH
                # heading, by title — literal_ref_title below carries that).
                return f"@heading:{heading}"

    # 4. a slide title the question names ("Riskler" -> resolved via title, not ordinal).
    if kind == "pptx":
        for slide in structure.get("slides") or []:
            title = str(slide.get("title") or "")
            title_words = [normalize_word(w) for w in tokenize(title)]
            if title_words and all(
                any(_shares_prefix(tw, qn) for qn in normalized) for tw in title_words
            ):
                return f"s{slide.get('index')}"
    return None


#: B37: a block no question word matched still ranks when its meaning is this close.
SEMANTIC_ALONE_FLOOR = 0.35


@dataclass(frozen=True, slots=True)
class ScoredBlock:
    block: dict[str, Any]
    score: int
    ref: str


def top_k(
    blocks: list[dict[str, Any]],
    question: str,
    *,
    kind: str = "",
    structure: dict[str, Any] | None = None,
    k: int = 3,
    embedder: Any | None = None,
) -> list[ScoredBlock]:
    """The best ``k`` blocks for ``question``, most relevant first.

    A literal reference (an ordinal + place noun, a named key/heading/slide) always wins:
    it is returned alone, with a sentinel score high enough to be unambiguous. Otherwise
    every block is scored by token overlap and the non-zero-scoring ones are returned,
    highest first, ties broken by the block's own order (stable sort — never randomised).
    """
    by_ref = {str(b.get("ref")): b for b in blocks}
    ref = literal_ref(question, kind=kind, structure=structure or {})
    if ref is not None:
        if ref.startswith("@heading:"):
            title = ref[len("@heading:") :]
            for b in blocks:
                if b.get("kind") == "section" and str(b.get("title")) == title:
                    return [ScoredBlock(block=b, score=1000, ref=str(b.get("ref")))]
        elif ref in by_ref:
            return [ScoredBlock(block=by_ref[ref], score=1000, ref=ref)]

    words = content_words(question)
    bonus = _semantic_bonus(blocks, question, embedder)
    scored = [
        ScoredBlock(
            block=b,
            score=_with_bonus(score_block(words, b), bonus.get(i, 0.0)),
            ref=str(b.get("ref")),
        )
        for i, b in enumerate(blocks)
    ]
    scored = [s for s in scored if s.score > 0]
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[: max(1, k)]


def _semantic_bonus(
    blocks: list[dict[str, Any]], question: str, embedder: Any | None
) -> dict[int, float]:
    """B37 req 149: the cosine similarity (0..1) of each block to the question under
    the memory subsystem's embedder; empty without one."""
    if embedder is None:
        return {}
    from app.memory.embedding import cosine_similarity

    query_vec = embedder.embed(question)
    out: dict[int, float] = {}
    for i, block in enumerate(blocks):
        text = _block_text_for_index(block)
        if text.strip():
            out[i] = max(0.0, cosine_similarity(query_vec, embedder.embed(text)))
    return out


def _with_bonus(lexical: int, similarity: float) -> int:
    """Meaning beside words: the cosine becomes a small integer so the literal-reference
    sentinel and the exact-word weighting keep their order; a block no word matched still
    ranks on a clear similarity."""
    if lexical > 0 or similarity >= SEMANTIC_ALONE_FLOOR:
        return lexical + int(round(similarity * 10))
    return lexical


__all__ = [
    "MIN_PREFIX_OVERLAP",
    "ScoredBlock",
    "content_words",
    "is_stopword",
    "literal_ref",
    "normalize_word",
    "score_block",
    "shares_prefix",
    "strip_diacritics",
    "tokenize",
    "top_k",
    "turkish_casefold",
]
