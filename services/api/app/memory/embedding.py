"""Embedding seam for memory retrieval (frozen foundation).

`Embedder` is the provider-neutral interface (constitution: embedding
providers must be replaceable). `DeterministicEmbedder` is the offline,
seeded implementation used by tests and the local eval corpus: it hashes
character n-grams into a fixed-dimension L2-normalized vector, giving stable,
lexically-meaningful cosine similarity with zero network and zero model
downloads. It is NOT a semantic model — production wires a real embedding
provider behind this same interface, and `memory_embeddings` rows carry
(model_id, model_version, dim) so a re-embedding migration can rebuild the
index for a new model without touching canonical memory rows.
"""

import hashlib
import math
import re
import unicodedata
from typing import Protocol, runtime_checkable

from app.memory.types import EMBEDDING_DIM


@runtime_checkable
class Embedder(Protocol):
    model_id: str
    model_version: str
    dim: int

    def embed(self, text: str) -> list[float]:
        """Return a length-`dim` L2-normalized vector for `text`."""
        ...


_WORD = re.compile(r"[a-z0-9çğıöşü]+")


def _normalize_text(text: str) -> str:
    # Casefold with Turkish-aware lowering of dotted/dotless I, strip accents
    # variance via NFKC, collapse whitespace.
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("I", "ı").replace("İ", "i").lower()
    return text


class DeterministicEmbedder:
    """Seeded lexical hash embedder (offline, deterministic, test/eval-grade)."""

    model_id = "deterministic-ngram"
    model_version = "1"
    dim = EMBEDDING_DIM

    def __init__(self, *, ngram: int = 3, seed: str = "pagentos-memory-v1") -> None:
        self._ngram = ngram
        self._seed = seed

    def _features(self, text: str) -> list[str]:
        words = _WORD.findall(_normalize_text(text))
        feats: list[str] = []
        for word in words:
            feats.append(f"w:{word}")
            padded = f"^{word}$"
            for i in range(len(padded) - self._ngram + 1):
                feats.append(f"g:{padded[i : i + self._ngram]}")
        return feats

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for feat in self._features(text):
            digest = hashlib.sha256(f"{self._seed}|{feat}".encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0:
            # Deterministic non-zero fallback for empty/degenerate input.
            vec[0] = 1.0
            return vec
        return [x / norm for x in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"dim mismatch: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
