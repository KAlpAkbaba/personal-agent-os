"""ADR-0200: the local semantic embedder behind the frozen ``Embedder`` protocol.

What these tests pin, and what each one would catch if it went missing:

* The index width is a contract. A model that is natively ``EMBEDDING_DIM`` wide serves
  as is; a model on the Matryoshka allowlist is truncated to the width and re-normalised;
  ANY other width is refused with its reason - never quietly padded, truncated or mixed
  into the index (two vector spaces in one column would make every similarity a lie).
* ``model_id`` names the model AND the width it was truncated to: a different model, or the
  same model at another width, is a different index (the re-index is what moves it).
* ``build_embedder`` never raises: fastembed missing, the model missing, a wrong width -
  each is a deterministic embedder WITH the reason on the report, so the health check
  can say ``semantic: false`` and why. ``auto`` never starts a model download on its own.
* The retention clock fills the active model's index in bounded batches; a second pass
  over a complete index writes nothing; a non-semantic embedder never fills anything.

The model itself is a fake here (a deterministic vector of the requested width): the
tests must never download a model or dial the network. The Turkish-quality check on the
real model is the owner's benchmark script (scripts/core/bench-memory-embedding.py) and
its verdict is READY_FOR_OWNER until it has been run - nothing here claims it.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.memory import providers
from app.memory.models import Base, Memory, MemoryEmbedding
from app.memory.providers import (
    DEFAULT_LOCAL_MODEL,
    MRL_TRUNCATABLE_MODELS,
    EmbeddingProviderError,
    LocalEmbedder,
    build_embedder,
)
from app.memory.runtime import MemoryRuntime
from app.memory.types import EMBEDDING_DIM, MemoryClass, MemoryStatus, WriteStage

# ------------------------------------------------------------------ a fake model


class _FakeModel:
    """Answers a vector of a fixed width whose first coordinates depend on the text, so
    two different texts get different (unnormalised) vectors and the same text always
    the same one - exactly what the embedder's normalisation and cache are tested on."""

    def __init__(self, width: int) -> None:
        self.width = width
        self.calls: list[str] = []

    def embed(self, documents: list[str], **_kwargs: Any) -> Iterable[list[float]]:
        for text in documents:
            self.calls.append(text)
            seed = sum(ord(ch) for ch in text) or 1
            yield [float((seed * (i + 1)) % 7 + 1) for i in range(self.width)]


def _factory(width: int, *, loads: list[tuple[str, str | None]] | None = None):
    def factory(model_name: str, cache_dir: str | None) -> _FakeModel:
        if loads is not None:
            loads.append((model_name, cache_dir))
        return _FakeModel(width)

    return factory


def _raising(exc: Exception):
    def factory(*_: Any) -> Any:
        raise exc

    return factory


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


# --------------------------------------------------------------- the width contract


def test_a_native_width_model_serves_as_is_normalised_and_cached() -> None:
    embedder = LocalEmbedder(model_name="some/native-256", model_factory=_factory(EMBEDDING_DIM))
    assert embedder.dim == EMBEDDING_DIM
    assert embedder.native_dim == EMBEDDING_DIM
    assert embedder.truncated is False
    assert embedder.model_id == "local-some/native-256"
    assert embedder.model_version == f"dim{EMBEDDING_DIM}"
    vector = embedder.embed("Sahip sabahları kahve içmeyi sever")
    assert len(vector) == EMBEDDING_DIM
    assert _norm(vector) == pytest.approx(1.0)
    again = embedder.embed("Sahip sabahları kahve içmeyi sever")
    assert again == vector
    assert embedder.calls == 1, "the same text is embedded once (LRU), like the OpenAI one"
    other = embedder.embed("Sunucuda disk alanı azaldı")
    assert other != vector


def test_a_matryoshka_model_is_truncated_to_the_width_and_the_model_id_says_so() -> None:
    model = next(iter(sorted(MRL_TRUNCATABLE_MODELS)))
    embedder = LocalEmbedder(model_name=model, model_factory=_factory(1024))
    assert embedder.native_dim == 1024 and embedder.truncated is True
    assert embedder.dim == EMBEDDING_DIM
    assert embedder.model_id == f"local-{model}@{EMBEDDING_DIM}"
    vector = embedder.embed("Aktivra benim kurduğum şirket")
    assert len(vector) == EMBEDDING_DIM
    assert _norm(vector) == pytest.approx(1.0), "re-normalised AFTER the truncation"


def test_a_model_of_another_width_off_the_allowlist_is_refused_with_its_reason() -> None:
    assert "some/other-model" not in MRL_TRUNCATABLE_MODELS
    with pytest.raises(EmbeddingProviderError) as excinfo:
        LocalEmbedder(model_name="some/other-model", model_factory=_factory(384))
    message = str(excinfo.value)
    assert "384" in message and str(EMBEDDING_DIM) in message
    assert "allowlist" in message


def test_a_narrower_model_is_refused_even_when_it_is_on_the_allowlist() -> None:
    """Truncation only ever SHORTENS: a 128-wide answer cannot become a 256-wide row."""
    model = next(iter(sorted(MRL_TRUNCATABLE_MODELS)))
    with pytest.raises(EmbeddingProviderError):
        LocalEmbedder(model_name=model, model_factory=_factory(128))


def test_a_model_that_changes_width_mid_flight_is_an_error_not_a_bad_row() -> None:
    class _Drifting(_FakeModel):
        def embed(self, documents: list[str], **kw: Any) -> Iterable[list[float]]:
            self.width = EMBEDDING_DIM if not self.calls else EMBEDDING_DIM + 1
            yield from super().embed(documents, **kw)

    embedder = LocalEmbedder(model_name="x/drift", model_factory=lambda *_: _Drifting(0))
    with pytest.raises(EmbeddingProviderError):
        embedder.embed("ikinci çağrı genişliği değiştirir")


# ---------------------------------------------------------- selection and fallbacks


def test_local_is_selected_by_configuration_with_the_model_and_cache_dir_it_was_given(
    tmp_path,
) -> None:
    loads: list[tuple[str, str | None]] = []
    settings = Settings(
        _env_file=None,
        memory_embedding_provider="local",
        memory_local_embedding_model="some/native-256",
        memory_local_embedding_cache_dir=str(tmp_path),
    )
    embedder, report = build_embedder(settings, model_factory=_factory(EMBEDDING_DIM, loads=loads))
    assert isinstance(embedder, LocalEmbedder)
    assert report.active == "local" and report.semantic is True
    assert report.requested == "local" and report.fallback_reason is None
    assert report.model_id == embedder.model_id == "local-some/native-256"
    assert loads == [("some/native-256", str(tmp_path))]


def test_the_default_local_model_is_the_native_width_one() -> None:
    settings = Settings(_env_file=None, memory_embedding_provider="local")
    assert settings.memory_local_embedding_model == DEFAULT_LOCAL_MODEL
    assert DEFAULT_LOCAL_MODEL not in MRL_TRUNCATABLE_MODELS, (
        "the default serves at its native width: no truncation, no migration"
    )


@pytest.mark.parametrize(
    ("factory", "reason_part"),
    [
        (_factory(384), "allowlist"),
        (_raising(ImportError("No module named fastembed")), "fastembed"),
        (_raising(FileNotFoundError("no model dir")), "could not be loaded"),
    ],
)
def test_every_local_failure_is_deterministic_with_the_reason_and_never_raises(
    factory, reason_part
) -> None:
    settings = Settings(_env_file=None, memory_embedding_provider="local")
    embedder, report = build_embedder(settings, model_factory=factory)
    assert report.requested == "local"
    assert report.active == "deterministic" and report.semantic is False
    assert reason_part in (report.fallback_reason or "")
    assert embedder.model_id == report.model_id == "deterministic-ngram"


def test_the_load_error_names_the_exception_class_and_never_its_text() -> None:
    def factory(*_: Any) -> Any:
        raise RuntimeError("secret-looking path C:/Users/alpak/token=abc")

    _e, report = build_embedder(
        Settings(_env_file=None, memory_embedding_provider="local"), model_factory=factory
    )
    assert "RuntimeError" in (report.fallback_reason or "")
    assert "token=abc" not in (report.fallback_reason or "")


def test_auto_never_starts_a_model_download_on_its_own() -> None:
    """``auto`` keeps its B37 meaning (OpenAI with a dedicated key, else deterministic):
    turning the local model on is the owner's explicit choice, because it is a download
    and half a gigabyte of RAM in the process."""
    loads: list[tuple[str, str | None]] = []
    _e, report = build_embedder(
        Settings(_env_file=None, memory_embedding_provider="auto", openai_api_key=""),
        model_factory=_factory(EMBEDDING_DIM, loads=loads),
    )
    assert report.active == "deterministic"
    assert loads == []


def test_the_runtime_health_check_reports_the_local_provider_as_semantic() -> None:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    runtime = MemoryRuntime(
        Settings(_env_file=None, memory_embedding_provider="local"),
        engine=engine,
        model_factory=_factory(EMBEDDING_DIM),
    )
    health = runtime.health_check()
    assert health["status"] == "ok"
    embedder = health["embedder"]
    assert embedder["provider"] == "local" and embedder["semantic"] is True
    assert embedder["fallback_reason"] is None
    assert embedder["model_id"] == f"local-{DEFAULT_LOCAL_MODEL}"


# ------------------------------------------------------- the index fills itself


def _memory(session: Session, text: str) -> Memory:
    row = Memory(
        id=uuid.uuid4(),
        memory_class=MemoryClass.PREFERENCE.value,
        key=f"k-{uuid.uuid4().hex[:6]}",
        text=text,
        value_json={},
        stage=WriteStage.DURABLE.value,
        status=MemoryStatus.ACTIVE.value,
        explicit=True,
        pinned=False,
        confidence=1.0,
        evidence_count=1,
    )
    session.add(row)
    session.commit()
    return row


def _runtime(provider: str, **kwargs: Any) -> MemoryRuntime:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return MemoryRuntime(
        Settings(_env_file=None, memory_embedding_provider=provider, **kwargs),
        engine=engine,
        model_factory=_factory(EMBEDDING_DIM),
    )


def test_the_sweep_fills_the_active_models_index_in_batches_and_then_writes_nothing() -> None:
    runtime = _runtime("local", memory_index_fill_batch=2)
    with runtime.session() as session:
        for text in ("bir", "iki", "üç"):
            _memory(session, text)
    assert runtime.fill_index() == 2, "one pass is bounded by the batch"
    assert runtime.fill_index() == 1, "the next pass takes the rest"
    assert runtime.fill_index() == 0, "a complete index is left alone"
    with runtime.session() as session:
        rows = session.query(MemoryEmbedding).all()
        assert len(rows) == 3
        assert {r.model_id for r in rows} == {runtime.embedder.model_id}
        assert all(r.dim == EMBEDDING_DIM and len(r.embedding) == EMBEDDING_DIM for r in rows)
    coverage = runtime.embedding_status()["coverage"]
    assert coverage["model_id"] == runtime.embedder.model_id


def test_a_non_semantic_embedder_never_fills_the_index() -> None:
    runtime = _runtime("deterministic")
    with runtime.session() as session:
        _memory(session, "hash rows would be thrown away by the next real model")
    assert runtime.fill_index() == 0
    with runtime.session() as session:
        assert session.query(MemoryEmbedding).count() == 0


def test_the_sweep_can_be_turned_off_by_the_owner() -> None:
    runtime = _runtime("local", memory_index_fill_enabled=False)
    with runtime.session() as session:
        _memory(session, "kapalı")
    assert runtime.fill_index() == 0


def test_the_sweep_is_registered_on_the_real_application_object() -> None:
    """A sweep nothing runs is the defect this repository keeps finding (2026-09-11):
    the name must be on the sweeper the real ``create_app`` builds."""
    from app.main import create_app

    app = create_app(Settings(_env_file=None))
    assert "memory_index" in app.state.retention_sweeper.names


def test_the_provider_list_and_the_exports_carry_local() -> None:
    assert "local" in providers.PROVIDERS
    assert {"LocalEmbedder", "PROVIDER_LOCAL", "DEFAULT_LOCAL_MODEL"} <= set(providers.__all__)
