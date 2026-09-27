"""Embedding providers and their selection (B37 req 51, 53).

``DeterministicEmbedder`` (app.memory.embedding) is the frozen offline fallback: a seeded
lexical hash, no network, no model - and NOT semantic. This module adds the real one behind
the same ``Embedder`` protocol and the ONE place that decides which serves:

* ``OpenAIEmbedder`` - ``text-embedding-3-small`` asked for exactly ``EMBEDDING_DIM``
  dimensions (the pgvector column is fixed at that width), L2-normalised, the key from
  settings (``openai_api_key``, falling back to the voice key the owner already installed)
  and never written to a log or an error; a short in-process LRU so the same text is not
  billed twice in one process.
* ``LocalEmbedder`` (ADR-0200) - a sentence-embedding model run ON THIS HOST through
  ``fastembed`` (ONNX, CPU, no torch, no key, no network after the one model download).
  The model is a setting (``memory_local_embedding_model``); the vector must fit the
  frozen ``EMBEDDING_DIM`` column: a model that is natively that wide is used as is, a
  model on the Matryoshka allowlist (``MRL_TRUNCATABLE_MODELS``: trained so that a prefix
  of the vector is itself a valid embedding) is truncated to the width and re-normalised,
  and any other width is REFUSED with its reason - never silently mixed into the index.
* ``build_embedder(settings)`` - ``memory_embedding_provider``: ``deterministic`` |
  ``local`` | ``openai`` | ``auto`` (OpenAI when a dedicated key is configured, otherwise
  deterministic - ``auto`` never starts a model download on its own; ``local`` is the
  owner's explicit choice, set in the production environment by ADR-0200). Every
  fallback carries its REASON in the report the health check publishes, so "semantic"
  is never claimed by a process that is hashing n-grams.

A provider change is a new ``model_id``: ``memory_embeddings`` rows are per model, so the
old index stays until ``lifecycle.reindex`` rebuilds the new one (req 54) - retrieval for
a model with no rows degrades to the keyword/structured candidates, never to a crash.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import httpx

from app.config import Settings
from app.logging import get_logger
from app.memory.embedding import DeterministicEmbedder, Embedder
from app.memory.types import EMBEDDING_DIM

logger = get_logger("app.memory.providers")

PROVIDER_DETERMINISTIC: Final = "deterministic"
PROVIDER_LOCAL: Final = "local"
PROVIDER_OPENAI: Final = "openai"
PROVIDER_AUTO: Final = "auto"
PROVIDERS: Final[tuple[str, ...]] = (
    PROVIDER_DETERMINISTIC,
    PROVIDER_LOCAL,
    PROVIDER_OPENAI,
    PROVIDER_AUTO,
)

OPENAI_EMBEDDINGS_URL: Final = "https://api.openai.com/v1/embeddings"
DEFAULT_OPENAI_MODEL: Final = "text-embedding-3-small"
MAX_INPUT_CHARS: Final = 8000
CACHE_SIZE: Final = 512

#: ADR-0200: the default local model is natively ``EMBEDDING_DIM`` (256) wide, multilingual
#: (Turkish included), ~0.5 GB, and a static-embedding model - milliseconds per text on a
#: CPU with no GPU, so it fits the Cloud Core beside everything else it runs. MIT.
DEFAULT_LOCAL_MODEL: Final = "minishlab/potion-multilingual-128M"

#: Models whose training makes a PREFIX of the vector a valid embedding on its own
#: (Matryoshka Representation Learning), so truncating to ``EMBEDDING_DIM`` and
#: re-normalising is what their own documentation prescribes - never a guess. A model
#: that is not on this list and not natively ``EMBEDDING_DIM`` wide is refused: a
#: truncated non-MRL vector is a different, worse vector space, and the index must
#: never hold two. Extend this list only with the model card as the source.
MRL_TRUNCATABLE_MODELS: Final[frozenset[str]] = frozenset(
    {
        "Qwen/Qwen3-Embedding-0.6B",  # model card: user-defined 32..1024 dims
        "Qwen/Qwen3-Embedding-0.6B-Q",
        "google/embeddinggemma-300m",  # model card: MRL, truncate to 512/256/128
    }
)


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


#: What ``LocalEmbedder`` needs from a loaded model: ``embed(texts)`` yielding one float
#: sequence per text. ``fastembed.TextEmbedding`` satisfies it; tests inject a fake.
class _EmbeddingModel(Protocol):
    def embed(self, documents: list[str], **kwargs: Any) -> Iterable[Any]: ...


ModelFactory = Callable[[str, str | None], _EmbeddingModel]


def _fastembed_factory(model_name: str, cache_dir: str | None) -> _EmbeddingModel:
    """Load the ONNX model through fastembed. Imported lazily so that the API process
    starts (and every other provider works) even where fastembed is not installed."""
    from fastembed import TextEmbedding  # noqa: PLC0415 - optional at import time

    kwargs: dict[str, Any] = {"model_name": model_name}
    if cache_dir:
        kwargs["cache_dir"] = cache_dir
    return TextEmbedding(**kwargs)


@dataclass(slots=True)
class LocalEmbedder:
    """ADR-0200: a sentence-embedding model on this host behind the ``Embedder`` protocol.

    ``model_id`` is ``local-<model name>`` plus, when the model is truncated, the width it
    was truncated to - a different model, or the same model at a different width, is a
    different vector space and therefore a different ``memory_embeddings.model_id``
    (the re-index is what moves the index, never a silent mix).
    """

    model_name: str = DEFAULT_LOCAL_MODEL
    cache_dir: str | None = None
    dim: int = EMBEDDING_DIM
    model_factory: ModelFactory = _fastembed_factory
    model_id: str = field(init=False)
    model_version: str = field(init=False)
    native_dim: int = field(init=False)
    truncated: bool = field(init=False, default=False)
    _model: _EmbeddingModel = field(init=False, repr=False)
    _cache: OrderedDict[str, list[float]] = field(default_factory=OrderedDict, init=False)
    calls: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not self.model_name:
            raise EmbeddingProviderError("local embedding provider needs a model name")
        try:
            self._model = self.model_factory(self.model_name, self.cache_dir)
        except ImportError:
            raise EmbeddingProviderError(
                "local embedding provider needs the fastembed package"
            ) from None
        except Exception as exc:  # noqa: BLE001 - the reason travels, the text never does
            raise EmbeddingProviderError(
                f"local embedding model {self.model_name!r} could not be loaded: "
                f"{type(exc).__name__}"
            ) from None
        probe = self._raw_embed("probe")
        self.native_dim = len(probe)
        if self.native_dim == self.dim:
            self.truncated = False
        elif self.native_dim > self.dim and self.model_name in MRL_TRUNCATABLE_MODELS:
            self.truncated = True
        else:
            raise EmbeddingProviderError(
                f"local embedding model {self.model_name!r} answers {self.native_dim} "
                f"dimensions; the index is {self.dim} wide and the model is not on the "
                "Matryoshka allowlist, so it cannot be truncated"
            )
        suffix = f"@{self.dim}" if self.truncated else ""
        self.model_id = f"local-{self.model_name}{suffix}"
        self.model_version = f"dim{self.dim}"

    def _raw_embed(self, text: str) -> list[float]:
        try:
            vectors = list(self._model.embed([text]))
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingProviderError(
                f"local embedding model {self.model_name!r} failed: {type(exc).__name__}"
            ) from None
        if not vectors:
            raise EmbeddingProviderError("local embedding model answered no vector")
        return [float(x) for x in vectors[0]]

    def embed(self, text: str) -> list[float]:
        key = text.strip()[:MAX_INPUT_CHARS]
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return list(cached)
        raw = self._raw_embed(key or " ")
        if len(raw) != self.native_dim:
            raise EmbeddingProviderError(
                f"local embedding model answered {len(raw)} dimensions, expected {self.native_dim}"
            )
        vector = raw[: self.dim] if self.truncated else raw
        norm = math.sqrt(sum(x * x for x in vector))
        vector = [x / norm for x in vector] if norm > 0.0 else vector
        self.calls += 1
        self._cache[key] = vector
        if len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return list(vector)


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
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
    model_factory: ModelFactory | None = None,
) -> tuple[Embedder, EmbedderReport]:
    """The ONE selection (req 53). Never raises: a provider that cannot be built is a
    deterministic embedder with the reason on the report."""
    requested = str(getattr(settings, "memory_embedding_provider", "") or PROVIDER_AUTO)
    model = str(getattr(settings, "memory_embedding_model", "") or DEFAULT_OPENAI_MODEL)
    if requested not in PROVIDERS:
        return _deterministic(requested, f"unknown provider {requested!r}")
    if requested == PROVIDER_DETERMINISTIC:
        return _deterministic(requested, None)
    if requested == PROVIDER_LOCAL:
        local_model = str(
            getattr(settings, "memory_local_embedding_model", "") or DEFAULT_LOCAL_MODEL
        )
        cache_dir = str(getattr(settings, "memory_local_embedding_cache_dir", "") or "") or None
        try:
            local = LocalEmbedder(
                model_name=local_model,
                cache_dir=cache_dir,
                **({"model_factory": model_factory} if model_factory is not None else {}),
            )
        except EmbeddingProviderError as exc:
            return _deterministic(requested, str(exc))
        report = EmbedderReport(
            requested=requested,
            active=PROVIDER_LOCAL,
            model_id=local.model_id,
            model_version=local.model_version,
            dim=local.dim,
            semantic=True,
        )
        logger.info(
            "memory_embedder_selected",
            provider=PROVIDER_LOCAL,
            model=local_model,
            native_dim=local.native_dim,
            truncated=local.truncated,
        )
        return local, report
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
    "DEFAULT_LOCAL_MODEL",
    "DEFAULT_OPENAI_MODEL",
    "MRL_TRUNCATABLE_MODELS",
    "PROVIDERS",
    "PROVIDER_AUTO",
    "PROVIDER_DETERMINISTIC",
    "PROVIDER_LOCAL",
    "PROVIDER_OPENAI",
    "EmbedderReport",
    "EmbeddingProviderError",
    "LocalEmbedder",
    "OpenAIEmbedder",
    "build_embedder",
]
