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

import hashlib
import math
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
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
        # model card: MRL 512/384/256/128
        # https://huggingface.co/ibm-granite/granite-embedding-311m-multilingual-r2
        "ibm-granite/granite-embedding-311m-multilingual-r2",
        "ibm-granite/granite-embedding-311m-multilingual-r2-int8",
    }
)


@dataclass(frozen=True, slots=True)
class PinnedFile:
    """One file the loader reads, as its bytes were at the pinned revision."""

    path: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class CustomOnnxModel:
    """An ONNX sentence-embedding model fastembed does not ship, pinned to one revision.
    ``pooling`` is what the model card prescribes (``cls`` | ``mean``), never a guess."""

    name: str
    hf_repo: str
    revision: str
    model_file: str
    pooling: str
    native_dim: int
    normalize: bool
    files: tuple[PinnedFile, ...]
    pad_id: int = 0


_GRANITE_R2: Final = "ibm-granite/granite-embedding-311m-multilingual-r2"
_GRANITE_R2_REVISION: Final = "44399559930365213510b1ee2eb15ded83374f0e"
_GRANITE_R2_SHARED: Final[tuple[PinnedFile, ...]] = (
    PinnedFile(
        "tokenizer.json",
        "0087c868b33bad550a78a08d19798cfd7f713cde4f020803b8f51f405503e15f",
        33384821,
    ),
    PinnedFile(
        "tokenizer_config.json",
        "7947bdf0378520e69ca412b8c4dacd1cffa8aef099f851fdd5c65aa27c6b36a0",
        1155500,
    ),
    PinnedFile(
        "special_tokens_map.json",
        "cb9e60dcf4d8d314315cb3e761fe4c2e664fda8dbf66d7815372b2639e381182",
        694,
    ),
    PinnedFile(
        "config.json", "e1e3fc842a8e0537e25d6e4c93879698b92ae96722e8c162bef334b57978a3b0", 1191
    ),
)

#: The CLOSED registry of models loaded from a pinned Hugging Face revision instead of
#: fastembed's own list (memory-embedding-granite-measure: MEASURED, not adopted - the
#: default stays ``DEFAULT_LOCAL_MODEL``). Every file is checked against its size and
#: sha256 before the first embedding. Pooling: the card's ``1_Pooling/config.json``
#: (``pooling_mode_cls_token: true``) and its Transformers snippet ("uses CLS Pooling").
CUSTOM_ONNX_MODELS: Final[Mapping[str, CustomOnnxModel]] = MappingProxyType(
    {
        spec.name: spec
        for spec in (
            CustomOnnxModel(
                name=_GRANITE_R2,
                hf_repo=_GRANITE_R2,
                revision=_GRANITE_R2_REVISION,
                model_file="onnx/model.onnx",
                pooling="cls",
                native_dim=768,
                normalize=True,
                files=(
                    PinnedFile(
                        "onnx/model.onnx",
                        "75f9f258bf5013f5fe8a4dad61dd0fd16ac0cbaa7a106e3d3f41c2d04a42d541",
                        1247170481,
                    ),
                    *_GRANITE_R2_SHARED,
                ),
            ),
            CustomOnnxModel(
                name=f"{_GRANITE_R2}-int8",
                hf_repo=_GRANITE_R2,
                revision=_GRANITE_R2_REVISION,
                model_file="onnx/model_quint8_avx2.onnx",
                pooling="cls",
                native_dim=768,
                normalize=True,
                files=(
                    PinnedFile(
                        "onnx/model_quint8_avx2.onnx",
                        "f1fdd44e7e1ac51f12ab7957c7bd092e064d596c288513bf9d326842f669edee",
                        313421909,
                    ),
                    *_GRANITE_R2_SHARED,
                ),
            ),
        )
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
    #: Guards ``_cache`` and ``calls`` exactly as ``LocalEmbedder._lock`` does (ADR-0256):
    #: request threads share the memory runtime's one embedder. Never held across the
    #: request to the API.
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )
    calls: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not self.api_key:
            raise EmbeddingProviderError("OpenAI embedding provider needs an API key")
        self.model_id = f"openai-{self.model}"
        self.model_version = f"dim{self.dim}"

    def embed(self, text: str) -> list[float]:
        key = text.strip()[:MAX_INPUT_CHARS]
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
        if cached is not None:
            return list(cached)
        # The network call is OUTSIDE the lock: a cached text is answered while another
        # thread waits on the API. Two threads that miss on the same text both pay for it -
        # fractions of a micro-dollar, stored once (ADR-0256 addendum).
        vector = self._fetch(key or " ")
        with self._lock:
            self.calls += 1
            self._cache[key] = vector
            self._cache.move_to_end(key)
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
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector] if norm > 0.0 else vector


#: What ``LocalEmbedder`` needs from a loaded model: ``embed(texts)`` yielding one float
#: sequence per text. ``fastembed.TextEmbedding`` satisfies it; tests inject a fake.
class _EmbeddingModel(Protocol):
    def embed(self, documents: list[str], **kwargs: Any) -> Iterable[Any]: ...


ModelFactory = Callable[[str, str | None], _EmbeddingModel]


def _fastembed_factory(
    model_name: str, cache_dir: str | None, *, threads: int | None = None
) -> _EmbeddingModel:
    """Load the ONNX model through fastembed. Imported lazily so that the API process
    starts (and every other provider works) even where fastembed is not installed.
    A ``CUSTOM_ONNX_MODELS`` name is fetched at its pinned revision and verified first."""
    spec = CUSTOM_ONNX_MODELS.get(model_name)
    if spec is not None:
        return _load_custom(spec, cache_dir, threads)
    from fastembed import TextEmbedding  # noqa: PLC0415 - optional at import time

    kwargs: dict[str, Any] = {"model_name": model_name}
    if cache_dir:
        kwargs["cache_dir"] = cache_dir
    if threads is not None:
        kwargs["threads"] = threads
    return TextEmbedding(**kwargs)


#: Names already given to ``TextEmbedding.add_custom_model`` in this process (it raises on
#: a second registration).
_REGISTERED_CUSTOM: set[str] = set()
_REGISTER_LOCK = threading.Lock()


def _verify_pinned(spec: CustomOnnxModel, root: Path) -> None:
    """Size and sha256 of every pinned file, BEFORE anything reads it as a model. The error
    names the file - never its content."""
    for pinned in spec.files:
        target = root / pinned.path
        if not target.is_file() or target.stat().st_size != pinned.size:
            raise EmbeddingProviderError(
                f"local embedding model {spec.name!r}: {pinned.path} is missing or its size "
                "does not match the pin, so its sha256 cannot match"
            )
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != pinned.sha256:
            raise EmbeddingProviderError(
                f"local embedding model {spec.name!r}: {pinned.path} sha256 does not match "
                f"the pin at revision {spec.revision}"
            )


def _load_custom(
    spec: CustomOnnxModel, cache_dir: str | None, threads: int | None
) -> _EmbeddingModel:
    from huggingface_hub import snapshot_download  # noqa: PLC0415 - optional at import time

    root = Path(
        snapshot_download(
            repo_id=spec.hf_repo,
            revision=spec.revision,
            allow_patterns=[pinned.path for pinned in spec.files],
            cache_dir=cache_dir,
        )
    )
    _verify_pinned(spec, root)
    try:
        from fastembed import TextEmbedding  # noqa: PLC0415
        from fastembed.common.model_description import (  # noqa: PLC0415
            ModelSource,
            PoolingType,
        )

        with _REGISTER_LOCK:
            if spec.name not in _REGISTERED_CUSTOM:
                TextEmbedding.add_custom_model(
                    model=spec.name,
                    pooling=PoolingType.CLS if spec.pooling == "cls" else PoolingType.MEAN,
                    normalization=spec.normalize,
                    sources=ModelSource(hf=spec.hf_repo),
                    dim=spec.native_dim,
                    model_file=spec.model_file,
                )
                _REGISTERED_CUSTOM.add(spec.name)
        kwargs: dict[str, Any] = {
            "model_name": spec.name,
            "cache_dir": cache_dir,
            "specific_model_path": str(root),
        }
        if threads is not None:
            kwargs["threads"] = threads
        return TextEmbedding(**kwargs)
    except Exception as exc:  # noqa: BLE001 - fastembed could not; onnxruntime directly
        logger.info("memory_embedder_custom_fallback", model=spec.name, reason=type(exc).__name__)
        return _OnnxRuntimeModel(spec, root, threads)


class _OnnxRuntimeModel:
    """The fallback for a ``CUSTOM_ONNX_MODELS`` entry fastembed cannot load: the verified
    files read by ``tokenizers`` + ``onnxruntime`` (CPU only), pooled as the registry says."""

    def __init__(self, spec: CustomOnnxModel, root: Path, threads: int | None = None) -> None:
        import onnxruntime  # noqa: PLC0415 - optional at import time
        from tokenizers import Tokenizer  # noqa: PLC0415

        options = onnxruntime.SessionOptions()
        if threads:
            options.intra_op_num_threads = threads
        self._spec = spec
        self._tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
        self._session = onnxruntime.InferenceSession(
            str(root / spec.model_file),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._inputs = {node.name for node in self._session.get_inputs()}

    def embed(self, documents: list[str], **_kwargs: Any) -> Iterable[Any]:
        import numpy as np  # noqa: PLC0415

        encodings = self._tokenizer.encode_batch(list(documents))
        width = max((len(e.ids) for e in encodings), default=0)
        ids = np.full((len(encodings), width), self._spec.pad_id, dtype=np.int64)
        mask = np.zeros((len(encodings), width), dtype=np.int64)
        for row, encoding in enumerate(encodings):
            ids[row, : len(encoding.ids)] = encoding.ids
            mask[row, : len(encoding.ids)] = encoding.attention_mask
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = np.asarray(self._session.run(None, feed)[0], dtype=np.float64)
        if self._spec.pooling == "cls":
            pooled = hidden[:, 0]
        else:
            weights = mask[:, :, None].astype(np.float64)
            pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        if self._spec.normalize:
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            pooled = pooled / np.clip(norms, 1e-12, None)
        return [row.tolist() for row in pooled]


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
    #: Guards ``_cache`` and ``calls``: the understanding index is built on its own thread
    #: through this object while request threads embed with it (ADR-0245). Held for the
    #: cache's own operations only, never across the model call. A stored vector is never
    #: mutated (every caller gets a copy), so it is copied outside the lock.
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )
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
        except EmbeddingProviderError:
            raise  # a pinned file that does not match: the reason names the file
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
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
        if cached is not None:
            return list(cached)
        # The model runs OUTSIDE the lock: one slow embedding must not make every other
        # thread wait. Two threads that miss on the same text both compute it - the same
        # vector twice, stored once.
        raw = self._raw_embed(key or " ")
        if len(raw) != self.native_dim:
            raise EmbeddingProviderError(
                f"local embedding model answered {len(raw)} dimensions, expected {self.native_dim}"
            )
        vector = raw[: self.dim] if self.truncated else raw
        norm = math.sqrt(sum(x * x for x in vector))
        vector = [x / norm for x in vector] if norm > 0.0 else vector
        with self._lock:
            self.calls += 1
            self._cache[key] = vector
            self._cache.move_to_end(key)
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
    "CUSTOM_ONNX_MODELS",
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
