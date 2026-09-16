"""Semantic document search (B37 req 149): the indexed documents' blocks ranked by MEANING
as well as by words, through the memory subsystem's embedder.

Before B37 a question over a document was answered by token overlap alone
(``app.documents.retrieval.top_k``), and there was no search ACROSS the indexed documents.
This module scores a block by both: the lexical overlap the answer path already trusts,
and the cosine similarity between the question and the block's text under the ONE
embedder the memory runtime serves - the real provider when the owner configured it,
the deterministic n-gram hash otherwise (which is why the report says ``semantic``
honestly: a hash is not meaning).

Every score carries its components, so the Cockpit and a test can see WHY a block ranked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from app.documents.retrieval import _block_text_for_index, content_words, score_block
from app.memory.embedding import Embedder, cosine_similarity

#: The lexical score is small integers (words matched); the cosine is in [0, 1]. A block
#: that matches no word can still rank on meaning when the similarity is clear.
W_LEXICAL: Final = 1.0
W_SEMANTIC: Final = 4.0
MIN_SEMANTIC_ALONE: Final = 0.35
MAX_BLOCKS_PER_DOCUMENT: Final = 400
PREVIEW_CHARS: Final = 160


@dataclass(slots=True)
class SemanticHit:
    doc_id: str
    file_id: str
    name: str
    path: str
    kind: str
    ref: str
    preview: str
    score: float
    components: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "file_id": self.file_id,
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "ref": self.ref,
            "preview": self.preview,
            "score": round(self.score, 4),
            "components": {k: round(v, 4) for k, v in self.components.items()},
        }


def block_text(block: dict[str, Any]) -> str:
    return _block_text_for_index(block)


def score_blocks(
    question: str, blocks: list[dict[str, Any]], embedder: Embedder | None
) -> list[tuple[dict[str, Any], float, dict[str, float]]]:
    """(block, score, components) for every block that scores at all, best first."""
    words = content_words(question)
    query_vec = embedder.embed(question) if embedder is not None else None
    out: list[tuple[dict[str, Any], float, dict[str, float]]] = []
    for block in blocks[:MAX_BLOCKS_PER_DOCUMENT]:
        lexical = float(score_block(words, block))
        semantic = 0.0
        if query_vec is not None:
            text = block_text(block)
            if text.strip():
                semantic = max(0.0, cosine_similarity(query_vec, embedder.embed(text)))
        if lexical <= 0.0 and semantic < MIN_SEMANTIC_ALONE:
            continue
        components = {"lexical": W_LEXICAL * lexical, "semantic": W_SEMANTIC * semantic}
        out.append((block, sum(components.values()), components))
    out.sort(key=lambda item: (-item[1], str(item[0].get("ref"))))
    return out


def search_rows(
    question: str,
    rows: list[Any],
    embedder: Embedder | None,
    *,
    k: int = 5,
) -> list[SemanticHit]:
    """The best ``k`` blocks across ``rows`` (``DocumentIndexRow``-shaped: doc_id, file_id,
    name, path, kind, blocks)."""
    hits: list[SemanticHit] = []
    for row in rows:
        blocks = list(getattr(row, "blocks", None) or [])
        # The document's own name and title are content an owner asks by ("rapor"),
        # scored as one more block with its own ref.
        label = " ".join(str(x) for x in (getattr(row, "name", ""), getattr(row, "title", "")) if x)
        if label.strip():
            blocks = [{"ref": "@name", "text": label}, *blocks]
        for block, score, components in score_blocks(question, blocks, embedder)[:3]:
            text = block_text(block)
            hits.append(
                SemanticHit(
                    doc_id=str(row.doc_id),
                    file_id=str(row.file_id),
                    name=str(row.name),
                    path=str(row.path),
                    kind=str(row.kind),
                    ref=str(block.get("ref") or ""),
                    preview=text[:PREVIEW_CHARS],
                    score=score,
                    components=components,
                )
            )
    hits.sort(key=lambda h: (-h.score, h.name, h.ref))
    return hits[: max(1, k)]


__all__ = ["MIN_SEMANTIC_ALONE", "SemanticHit", "block_text", "score_blocks", "search_rows"]
