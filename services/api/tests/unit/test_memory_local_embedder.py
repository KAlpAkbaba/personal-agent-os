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
* The cache is shared by threads (the index build of ADR-0245 beside the request threads):
  an eviction can never land between a hit and the refresh of its place, and the model
  call is never made under the cache lock.

The model itself is a fake here (a deterministic vector of the requested width): the
tests must never download a model or dial the network. The Turkish-quality check on the
real model is the owner's benchmark script (scripts/core/bench-memory-embedding.py) and
its verdict is READY_FOR_OWNER until it has been run - nothing here claims it.
"""

from __future__ import annotations

import math
import random
import sys
import threading
import uuid
import zlib
from collections import OrderedDict
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
    CACHE_SIZE,
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


# ------------------------------------------------- one cache, several threads
#
# ADR-0245 builds the understanding index on a daemon thread through the memory runtime's
# embedder while request threads embed with the same object. The three forced tests below
# decide the interleaving themselves (events, never a sleep): each one fails or passes the
# same way on every run. The stress run after them is the second opinion, never the proof.

#: A hang guard, not an assertion: no wait below is expected to last this long.
_HANG_GUARD_S = 20.0


def _text_vector(text: str, width: int) -> list[float]:
    """Every bit of the text's checksum is in the vector, so two texts never share one."""
    seed = zlib.crc32(text.encode("utf-8"))
    return [float(((seed >> (i % 32)) & 0xFF) + 1 + i) for i in range(width)]


class _GatedModel:
    """A model whose answer for the texts in ``held`` waits for ``release``: the test
    decides how many threads are inside the model call, and when they leave it."""

    def __init__(self, width: int, *, held: Iterable[str] = (), expect: int = 1) -> None:
        self.width = width
        self.held = frozenset(held)
        self.expect = expect
        self.inside = 0
        self.first_inside = threading.Event()
        self.all_inside = threading.Event()
        self.release = threading.Event()
        self._guard = threading.Lock()

    def embed(self, documents: list[str], **_kwargs: Any) -> Iterable[list[float]]:
        vectors = []
        for text in documents:
            if text in self.held:
                with self._guard:
                    self.inside += 1
                    self.first_inside.set()
                    if self.inside >= self.expect:
                        self.all_inside.set()
                if not self.release.wait(_HANG_GUARD_S):
                    raise TimeoutError("the test never released the model")
            vectors.append(_text_vector(text, self.width))
        return vectors


def _gated(model: _GatedModel) -> LocalEmbedder:
    return LocalEmbedder(model_name="some/native-256", model_factory=lambda *_: model)


def _single_threaded(texts: Iterable[str]) -> dict[str, list[float]]:
    """What one thread alone gets for each text - the answer every thread must get."""
    embedder = _gated(_GatedModel(EMBEDDING_DIM))
    expected = {text: embedder.embed(text) for text in texts}
    assert len({tuple(v) for v in expected.values()}) == len(expected), "one vector per text"
    return expected


class _Call(threading.Thread):
    """One ``embed(text)`` on its own thread; ``stopped`` is set when it has returned."""

    def __init__(
        self, embedder: LocalEmbedder, text: str, *, stopped: threading.Event | None = None
    ) -> None:
        super().__init__(daemon=True)
        self.embedder = embedder
        self.text = text
        self.stopped = stopped
        self.returned = False
        self.result: list[float] | None = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self.embedder.embed(self.text)
        except BaseException as exc:  # noqa: BLE001 - the test reads it
            self.error = exc
        finally:
            self.returned = True
            if self.stopped is not None:
                self.stopped.set()


class _ObservedLock:
    """The embedder's own cache lock, with ``waiting`` set the moment a thread finds it
    taken - so "this thread is blocked at the lock" is an event, not a guess after a sleep."""

    def __init__(self, inner: Any, waiting: threading.Event) -> None:
        self._inner = inner
        self.waiting = waiting
        self.waited = False

    def __enter__(self) -> _ObservedLock:
        if not self._inner.acquire(blocking=False):
            self.waited = True
            self.waiting.set()
            self._inner.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._inner.release()


def _observe_lock(embedder: LocalEmbedder, waiting: threading.Event) -> _ObservedLock | None:
    """Wrap the embedder's cache lock. An embedder with no lock has nothing to observe -
    and is the defect itself: the thread the test expects to wait runs to its end."""
    inner = getattr(embedder, "_lock", None)
    if inner is None:
        return None
    observed = _ObservedLock(inner, waiting)
    embedder._lock = observed  # type: ignore[assignment]
    return observed


class _HeldLookupCache(OrderedDict):  # type: ignore[type-arg]
    """The embedder's cache, with the FIRST lookup of ``hold_key`` kept from returning
    until ``until`` is set: the caller has read the entry and has not refreshed its place
    in the order yet - the one point where another thread's eviction can reach it."""

    hold_key: str
    until: threading.Event
    holding: threading.Event

    def get(self, key: Any, default: Any = None) -> Any:
        value = super().get(key, default)
        if key == self.hold_key and not self.holding.is_set():
            self.holding.set()
            self.until.wait(_HANG_GUARD_S)
        return value


def _join(*calls: _Call) -> None:
    for call in calls:
        call.join(_HANG_GUARD_S)
    assert not any(call.is_alive() for call in calls), "an embed() never returned"


def test_an_eviction_cannot_land_between_a_hit_and_the_refresh_of_its_place() -> None:
    """The interleaving this test forces, on a FULL cache whose oldest entry is ``oldest``:

        B  embed(new)     lookup: miss | model ...(gated)........ | store, evict the oldest
        A  embed(oldest)                    lookup: hit ...(held)............ refresh its place

    B is let out of the model while A is held between reading the entry and refreshing it.
    Unlocked, B stores and evicts - the entry it evicts is the one A just read - and A's
    refresh raises KeyError: on the index-build thread that is "engine not configured" for
    the life of the process. Locked, B waits at the lock until A has refreshed the entry,
    and what B then evicts is the entry that really is the oldest."""
    texts = [f"metin {i}" for i in range(CACHE_SIZE)]
    oldest, runner_up, new = texts[0], texts[1], "önbellekte olmayan metin"
    expected = _single_threaded([oldest, new])
    model = _GatedModel(EMBEDDING_DIM, held=[new])
    embedder = _gated(model)
    for text in texts:
        embedder.embed(text)
    assert len(embedder._cache) == CACHE_SIZE and next(iter(embedder._cache)) == oldest

    b_stopped = threading.Event()  # B returned, or B is waiting at the lock
    observed = _observe_lock(embedder, b_stopped)
    cache = _HeldLookupCache(embedder._cache)
    cache.hold_key, cache.until, cache.holding = oldest, b_stopped, threading.Event()
    embedder._cache = cache

    b = _Call(embedder, new, stopped=b_stopped)
    b.start()
    assert model.first_inside.wait(_HANG_GUARD_S), "B never reached the model"
    a = _Call(embedder, oldest)
    a.start()
    assert cache.holding.wait(_HANG_GUARD_S), "A never reached the lookup"
    assert not b_stopped.is_set(), "nobody waits at the lock while B is inside the model"
    model.release.set()
    _join(a, b)

    assert a.error is None, f"A's hit lost its entry to B's eviction: {a.error!r}"
    assert b.error is None
    assert a.result == expected[oldest] and b.result == expected[new]
    assert observed is not None and observed.waited, "B was never kept out of the cache"
    assert len(embedder._cache) == CACHE_SIZE
    assert list(embedder._cache)[-2:] == [oldest, new], "A's hit made its entry the newest"
    assert runner_up not in embedder._cache, "the entry evicted is the least recently used"
    assert embedder.calls == CACHE_SIZE + 1


def test_a_cached_text_returns_while_another_thread_is_inside_the_model() -> None:
    """The model call is NOT made under the cache lock: one slow embedding (the index build
    has ~1430 of them) must not make every request thread wait for it."""
    cached, slow = "önbellekteki metin", "yavaş metin"
    expected = _single_threaded([cached, slow])
    model = _GatedModel(EMBEDDING_DIM, held=[slow])
    embedder = _gated(model)
    embedder.embed(cached)

    c_stopped = threading.Event()  # C returned, or C is waiting at the lock
    observed = _observe_lock(embedder, c_stopped)
    b = _Call(embedder, slow)
    b.start()
    assert model.first_inside.wait(_HANG_GUARD_S), "B never reached the model"
    c = _Call(embedder, cached, stopped=c_stopped)
    c.start()
    assert c_stopped.wait(_HANG_GUARD_S)
    returned_meanwhile = c.returned and not b.returned
    waited = observed is not None and observed.waited
    model.release.set()
    _join(b, c)

    assert not waited, "the cached text waited for the lock B holds inside the model"
    assert returned_meanwhile, "the cached text did not return while B was inside the model"
    assert (b.error, c.error) == (None, None)
    assert c.result == expected[cached] and b.result == expected[slow]
    assert embedder.calls == 2 and list(embedder._cache) == [cached, slow]


def test_two_threads_asking_for_the_same_uncached_text_may_both_compute_it() -> None:
    """Both are inside the model at once (neither waits for the other's embedding), both
    get the text's vector, and the cache ends with ONE entry for it. The second model call
    is the accepted price of not holding the lock across the model: same text, same vector."""
    text = "iki iş parçacığının aynı anda sorduğu metin"
    expected = _single_threaded([text])
    model = _GatedModel(EMBEDDING_DIM, held=[text], expect=2)
    embedder = _gated(model)
    observed = _observe_lock(embedder, model.all_inside)

    first = _Call(embedder, text)
    first.start()
    assert model.first_inside.wait(_HANG_GUARD_S)
    second = _Call(embedder, text)
    second.start()
    assert model.all_inside.wait(_HANG_GUARD_S)  # both inside, or the second at the lock
    inside = model.inside
    waited = observed is not None and observed.waited
    model.release.set()
    _join(first, second)

    assert not waited and inside == 2, "the second thread waited for the first one's model call"
    assert (first.error, second.error) == (None, None)
    assert first.result == second.result == expected[text]
    assert first.result is not second.result, "each caller owns its list"
    assert embedder.calls == 2 and list(embedder._cache) == [text]


def test_eight_threads_of_mixed_embeds_end_with_no_error_and_the_single_threaded_vectors() -> None:
    """The second opinion: 8 threads x 2000 embeds over more texts than the cache holds
    (hits, misses and evictions all the time), the interpreter switching threads as often
    as it can. Unlocked this fails only when a switch lands in the window - the forced
    tests above are the proof; this one says the lock holds under load as well."""
    threads_n, embeds_n = 8, 2000
    pool = [f"cümle {i}" for i in range(CACHE_SIZE + 188)]
    expected = _single_threaded(pool)
    embedder = _gated(_GatedModel(EMBEDDING_DIM))
    start = threading.Barrier(threads_n)
    errors: list[BaseException] = []
    wrong: list[str] = []
    done: list[int] = []

    def worker(seed: int) -> None:
        rng = random.Random(seed)
        try:
            start.wait(_HANG_GUARD_S)
            for _ in range(embeds_n):
                text = pool[rng.randrange(len(pool))]
                if embedder.embed(text) != expected[text]:
                    wrong.append(text)
            done.append(seed)
        except BaseException as exc:  # noqa: BLE001 - the test reads it
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,), daemon=True) for n in range(threads_n)]
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(_HANG_GUARD_S * 6)
    finally:
        sys.setswitchinterval(interval)

    assert errors == [], f"{len(errors)} embed() raised, first: {errors[0]!r}"
    assert wrong == [], "an embed() returned another text's vector"
    assert sorted(done) == list(range(threads_n)), "a thread never finished"
    assert len(embedder._cache) == CACHE_SIZE
    assert all(embedder._cache[text] == expected[text] for text in embedder._cache)
