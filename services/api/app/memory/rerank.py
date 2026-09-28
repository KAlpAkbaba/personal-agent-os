"""A local semantic reranker over ``hybrid_search`` (ADR-0206).

The embedder (ADR-0200) answers "which memories are about this"; a cross-encoder reads the
query and ONE memory together and answers "does this memory answer it". The first is a
vector lookup over the whole table, the second is a model call per pair - so the reranker
only ever sees the top ``memory_rerank_top_k`` candidates ``hybrid_search`` already chose.

Same discipline as the embedder, for the same reasons:

* ``Reranker`` is a protocol; ``memory_rerank_provider`` is ``none`` | ``local``.
  ``none`` is the default and means NO reranker object exists: ``hybrid_search`` takes the
  path it has always taken, and a test pins that the result is identical.
* ``LocalReranker`` runs a cross-encoder ON THIS HOST through ``fastembed`` (ONNX, CPU, no
  torch, no key). The API process never downloads a model: it loads with
  ``local_files_only`` from the directory the release script prefetched into, and a model
  that is not there is ``none`` with the reason on the report the health check publishes.
  "Reranked" is never claimed by a process that is not reranking.
* ``build_reranker`` never raises. A reranker that fails while scoring is caught where it
  is called: the turn keeps the ordering it would have had without one.

Scores are squashed to 0..1 with a logistic, so the rerank term has the same range as the
cosine term it stands in for and the documented weights keep meaning what they say.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from app.config import Settings
from app.logging import get_logger

logger = get_logger("app.memory.rerank")

PROVIDER_NONE: Final = "none"
PROVIDER_LOCAL: Final = "local"
PROVIDERS: Final[tuple[str, ...]] = (PROVIDER_NONE, PROVIDER_LOCAL)

DEFAULT_TOP_K: Final = 20
MAX_TOP_K: Final = 50
MAX_PAIR_CHARS: Final = 2000

#: The int8 export of BAAI/bge-reranker-v2-m3 (Apache-2.0): multilingual, Turkish included.
#: See docs/DECISIONS.md ADR-0206 for the measurement that chose it.
DEFAULT_LOCAL_MODEL: Final = "onnx-community/bge-reranker-v2-m3-ONNX#int8"


@dataclass(frozen=True, slots=True)
class CustomModel:
    """A cross-encoder fastembed does not list, loaded from its ONNX export."""

    hf: str
    model_file: str
    additional_files: tuple[str, ...]
    licence: str
    size_gb: float


#: ``<huggingface repository>#<variant>`` names this module knows how to load. A name that
#: is neither here nor in fastembed's own list is refused with its reason - the reranker is
#: never a model somebody typed into an environment file and nobody measured.
CUSTOM_MODELS: Final[dict[str, CustomModel]] = {
    "onnx-community/bge-reranker-v2-m3-ONNX": CustomModel(
        hf="onnx-community/bge-reranker-v2-m3-ONNX",
        model_file="onnx/model.onnx",
        additional_files=("onnx/model.onnx_data",),
        licence="apache-2.0",
        size_gb=2.27,
    ),
    "onnx-community/bge-reranker-v2-m3-ONNX#int8": CustomModel(
        hf="onnx-community/bge-reranker-v2-m3-ONNX",
        model_file="onnx/model_int8.onnx",
        additional_files=(),
        licence="apache-2.0",
        size_gb=0.57,
    ),
    "jinaai/jina-reranker-v2-base-multilingual#int8": CustomModel(
        hf="jinaai/jina-reranker-v2-base-multilingual",
        model_file="onnx/model_int8.onnx",
        additional_files=(),
        licence="cc-by-nc-4.0",
        size_gb=0.28,
    ),
}


class RerankProviderError(Exception):
    """The reranker could not be built or could not answer. Never carries the text."""


class Reranker(Protocol):
    model_id: str

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        """One score in 0..1 per document, in the order given; higher answers better."""
        ...


class _CrossEncoder(Protocol):
    def rerank(self, query: str, documents: Iterable[str]) -> Iterable[float]: ...


#: (model name, cache dir, local_files_only) -> the loaded cross-encoder.
RerankModelFactory = Callable[[str, str | None, bool], _CrossEncoder]


def _fastembed_factory(model_name: str, cache_dir: str | None, local_only: bool) -> _CrossEncoder:
    from fastembed.common.model_description import ModelSource
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    custom = CUSTOM_MODELS.get(model_name)
    if custom is not None:
        listed = {str(m["model"]) for m in TextCrossEncoder.list_supported_models()}
        if model_name not in listed:
            TextCrossEncoder.add_custom_model(
                model=model_name,
                sources=ModelSource(hf=custom.hf),
                model_file=custom.model_file,
                additional_files=list(custom.additional_files),
                license=custom.licence,
                size_in_gb=custom.size_gb,
            )
    model: _CrossEncoder = TextCrossEncoder(
        model_name=model_name, cache_dir=cache_dir, local_files_only=local_only
    )
    return model


def squash(raw: float) -> float:
    """A cross-encoder's logit as 0..1. Monotonic, so the model's own order is kept."""
    if raw >= 0.0:
        return 1.0 / (1.0 + math.exp(-raw))
    grown = math.exp(raw)
    return grown / (1.0 + grown)


@dataclass(slots=True)
class LocalReranker:
    model_name: str = DEFAULT_LOCAL_MODEL
    cache_dir: str | None = None
    #: The API process loads only what the release script already fetched. The prefetch
    #: itself is the one caller that passes False.
    local_files_only: bool = True
    model_factory: RerankModelFactory = _fastembed_factory
    model_id: str = field(init=False)
    _model: _CrossEncoder = field(init=False, repr=False)
    calls: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not self.model_name:
            raise RerankProviderError("local rerank provider needs a model name")
        try:
            self._model = self.model_factory(self.model_name, self.cache_dir, self.local_files_only)
        except ImportError:
            raise RerankProviderError("local rerank provider needs the fastembed package") from None
        except Exception as exc:  # noqa: BLE001 - the reason travels, the text never does
            raise RerankProviderError(
                f"local rerank model {self.model_name!r} could not be loaded: {type(exc).__name__}"
            ) from None
        self.model_id = f"local-{self.model_name}"
        # A model that loads and cannot score is not a reranker; found here, not in a turn.
        if len(self.score("probe", ["probe"])) != 1:
            raise RerankProviderError(
                f"local rerank model {self.model_name!r} answered no score for one pair"
            )

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        texts = [str(d or " ")[:MAX_PAIR_CHARS] for d in documents]
        try:
            raw = [float(s) for s in self._model.rerank(query[:MAX_PAIR_CHARS], texts)]
        except Exception as exc:  # noqa: BLE001
            raise RerankProviderError(
                f"local rerank model {self.model_name!r} failed: {type(exc).__name__}"
            ) from None
        if len(raw) != len(texts):
            raise RerankProviderError(
                f"local rerank model answered {len(raw)} scores for {len(texts)} pairs"
            )
        self.calls += 1
        return [squash(s) for s in raw]


@dataclass(frozen=True, slots=True)
class RerankerReport:
    requested: str
    active: str
    model_id: str | None
    top_k: int
    fallback_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "provider": self.active,
            "active": self.active != PROVIDER_NONE,
            "model_id": self.model_id,
            "top_k": self.top_k,
            "fallback_reason": self.fallback_reason,
        }


def rerank_top_k(settings: Settings) -> int:
    """The number of candidates a turn pays for, kept inside 1..MAX_TOP_K."""
    try:
        wanted = int(getattr(settings, "memory_rerank_top_k", DEFAULT_TOP_K) or DEFAULT_TOP_K)
    except (TypeError, ValueError):
        wanted = DEFAULT_TOP_K
    return max(1, min(wanted, MAX_TOP_K))


def build_reranker(
    settings: Settings, *, model_factory: RerankModelFactory | None = None
) -> tuple[Reranker | None, RerankerReport]:
    """The ONE selection. Never raises: a reranker that cannot be built is no reranker,
    with the reason on the report."""
    requested = str(getattr(settings, "memory_rerank_provider", "") or PROVIDER_NONE)
    top_k = rerank_top_k(settings)
    if requested == PROVIDER_NONE:
        return None, RerankerReport(requested, PROVIDER_NONE, None, top_k)
    if requested not in PROVIDERS:
        return _none(requested, top_k, f"unknown provider {requested!r}")
    model = str(getattr(settings, "memory_rerank_model", "") or DEFAULT_LOCAL_MODEL)
    cache_dir = str(getattr(settings, "memory_rerank_cache_dir", "") or "") or None
    try:
        local = LocalReranker(
            model_name=model,
            cache_dir=cache_dir,
            **({"model_factory": model_factory} if model_factory is not None else {}),
        )
    except RerankProviderError as exc:
        return _none(requested, top_k, str(exc))
    logger.info("memory_reranker_selected", provider=PROVIDER_LOCAL, model=model, top_k=top_k)
    return local, RerankerReport(requested, PROVIDER_LOCAL, local.model_id, top_k)


def _none(requested: str, top_k: int, reason: str) -> tuple[None, RerankerReport]:
    logger.info("memory_reranker_fallback", requested=requested, reason=reason)
    return None, RerankerReport(requested, PROVIDER_NONE, None, top_k, fallback_reason=reason)


__all__ = [
    "CUSTOM_MODELS",
    "DEFAULT_LOCAL_MODEL",
    "DEFAULT_TOP_K",
    "MAX_TOP_K",
    "PROVIDERS",
    "PROVIDER_LOCAL",
    "PROVIDER_NONE",
    "CustomModel",
    "LocalReranker",
    "RerankModelFactory",
    "RerankProviderError",
    "Reranker",
    "RerankerReport",
    "build_reranker",
    "rerank_top_k",
    "squash",
]
