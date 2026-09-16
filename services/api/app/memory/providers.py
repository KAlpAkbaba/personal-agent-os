"""Embedding providers and their selection (B37 req 51, 53).

``DeterministicEmbedder`` (app.memory.embedding) is the frozen offline fallback: a seeded
lexical hash, no network, no model - and NOT semantic. This module adds the real one behind
the same ``Embedder`` protocol and the ONE place that decides which serves:

* ``OpenAIEmbedder`` - ``text-embedding-3-small`` asked for exactly ``EMBEDDING_DIM``
  dimensions (the pgvector column is fixed at that width), L2-normalised, the key from
  settings (``openai_api_key``, falling back to the voice key the owner already installed)
  and never written to a log or an error; a short in-process LRU so the same text is not
  billed twice in one process.
* ``build_embedder(settings)`` - ``memory_embedding_provider``: ``deterministic`` |
  ``openai`` | ``auto`` (OpenAI when a key is configured, otherwise deterministic). Every
  fallback carries its REASON in the report the health check publishes, so "semantic"
  is never claimed by a process that is hashing n-grams.

A provider change is a new ``model_id``: ``memory_embeddings`` rows are per model, so the
old index stays until ``lifecycle.reindex`` rebuilds the new one (req 54) - retrieval for
a model with no rows degrades to the keyword/structured candidates, never to a crash.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from app.config import Settings
from app.logging import get_logger
from app.memory.embedding import DeterministicEmbedder, Embedder
from app.memory.types import EMBEDDING_DIM

logger = get_logger("app.memory.providers")

PROVIDER_DETERMINISTIC: Final = "deterministic"
PROVIDER_OPENAI: Final = "openai"
PROVIDER_AUTO: Final = "auto"
PROVIDERS: Final[tuple[str, ...]] = (PROVIDER_DETERMINISTIC, PROVIDER_OPENAI, PROVIDER_AUTO)

OPENAI_EMBEDDINGS_URL: Final = "https://api.openai.com/v1/embeddings"
DEFAULT_OPENAI_MODEL: Final = "text-embedding-3-small"
MAX_INPUT_CHARS: Final = 8000
CACHE_SIZE: Final = 512


class EmbeddingProviderError(Exception):
    """The provider could not answer. The message never carries the key or the text."""


@dataclass(slots=True)
class OpenAIEmbedder:
    api_key: str
    model: str = DEFAULT_OPENAI_MODEL
    timeout_s: float = 20.0
    transport: httpx.BaseTransport | None = None
    dim: int = EMBEDDING_DIM
    #: What ``memory_embeddings.model_id`` records: the provider and the model, so a
    #: model change is a re-index and never a silent mix of vector spaces.
    model_id: str = field(init=False)
    model_version: str = field(init=False)
    _cache: OrderedDict[str, list[float]] = field(default_factory=OrderedDict, init=False)
    calls: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not self.api_key:
            raise EmbeddingProviderError("OpenAI embedding provider needs an API key")
        self.model_id = f"openai-{self.model}"
        self.model_version = f"dim{self.dim}"

    def embed(self, text: str) -> list[float]:
        key = text.strip()[:MAX_INPUT_CHARS]
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return list(cached)
        vector = self._fetch(key or " ")
        self._cache[key] = vector
        if len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return list(vector)

    def _fetch(self, text: str) -> list[float]:
        body = {"model": self.model, "input": text, "dimensions": self.dim}
        headers = {"authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self.transport) as client:
                response = client.post(OPENAI_EMBEDDINGS_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise EmbeddingProviderError(
                f"OpenAI embeddings unreachable: {type(exc).__name__}"
            ) from None
        if response.status_code != 200:
            raise EmbeddingProviderError(f"OpenAI embeddings answered {response.status_code}")
        try:
            data: dict[str, Any] = response.json()
            raw = data["data"][0]["embedding"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise EmbeddingProviderError("OpenAI embeddings answered an unexpected shape") from None
        vector = [float(x) for x in raw]
        if len(vector) != self.dim:
            raise EmbeddingProviderError(
                f"OpenAI embeddings answered {len(vector)} dimensions, expected {self.dim}"
            )
        self.calls += 1
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector] if norm > 0.0 else vector


@dataclass(frozen=True, slots=True)
class EmbedderReport:
    requested: str
    active: str
    model_id: str
    model_version: str
    dim: int
    semantic: bool
    fallback_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "active": self.active,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "dim": self.dim,
            "semantic": self.semantic,
            "fallback_reason": self.fallback_reason,
        }


def _openai_key(settings: Settings, *, requested: str) -> str:
    """``auto`` reads only the DEDICATED key: a memory write must never start billing the
    voice key behind the owner's back (and the corpus harness installs a fake voice key for
    the realtime fake). An explicit ``openai`` also accepts the voice key the owner already
    installed, the way synthesis and vision do."""
    dedicated = str(getattr(settings, "openai_api_key", "") or "")
    if dedicated or requested != PROVIDER_OPENAI:
        return dedicated
    return str(getattr(settings, "voice_openai_api_key", "") or "")


def build_embedder(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> tuple[Embedder, EmbedderReport]:
    """The ONE selection (req 53). Never raises: a provider that cannot be built is a
    deterministic embedder with the reason on the report."""
    requested = str(getattr(settings, "memory_embedding_provider", "") or PROVIDER_AUTO)
    model = str(getattr(settings, "memory_embedding_model", "") or DEFAULT_OPENAI_MODEL)
    if requested not in PROVIDERS:
        return _deterministic(requested, f"unknown provider {requested!r}")
    if requested == PROVIDER_DETERMINISTIC:
        return _deterministic(requested, None)
    key = _openai_key(settings, requested=requested)
    if not key:
        reason = (
            "no OpenAI key configured (PAGENTOS_OPENAI_API_KEY)"
            if requested == PROVIDER_OPENAI
            else "auto: no dedicated OpenAI key configured, deterministic serves"
        )
        return _deterministic(requested, reason)
    try:
        embedder = OpenAIEmbedder(api_key=key, model=model, transport=transport)
    except EmbeddingProviderError as exc:
        return _deterministic(requested, str(exc))
    report = EmbedderReport(
        requested=requested,
        active=PROVIDER_OPENAI,
        model_id=embedder.model_id,
        model_version=embedder.model_version,
        dim=embedder.dim,
        semantic=True,
    )
    logger.info("memory_embedder_selected", provider=PROVIDER_OPENAI, model=model)
    return embedder, report


def _deterministic(requested: str, reason: str | None) -> tuple[Embedder, EmbedderReport]:
    embedder = DeterministicEmbedder()
    report = EmbedderReport(
        requested=requested,
        active=PROVIDER_DETERMINISTIC,
        model_id=embedder.model_id,
        model_version=embedder.model_version,
        dim=embedder.dim,
        semantic=False,
        fallback_reason=reason,
    )
    if reason:
        logger.info("memory_embedder_fallback", requested=requested, reason=reason)
    return embedder, report


__all__ = [
    "DEFAULT_OPENAI_MODEL",
    "PROVIDERS",
    "PROVIDER_AUTO",
    "PROVIDER_DETERMINISTIC",
    "PROVIDER_OPENAI",
    "EmbedderReport",
    "EmbeddingProviderError",
    "OpenAIEmbedder",
    "build_embedder",
]
