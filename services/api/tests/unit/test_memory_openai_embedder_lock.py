"""``OpenAIEmbedder``'s LRU under concurrent threads (ADR-0256, the OpenAI half).

The memory runtime holds ONE embedder and every request thread embeds with it; when the
owner's embedder setting is the OpenAI provider, that object is ``OpenAIEmbedder``. Its
512-entry LRU had the same unlocked read / refresh / store / evict as ``LocalEmbedder``
before ADR-0256. The forced tests below reuse that file's interleaving machinery (the held
cache, the observed lock, the thread-per-call) with the provider's HTTP client replaced by
an ``httpx.MockTransport`` stand-in: NO network, no key leaves the process.
"""

from __future__ import annotations

import json
import random
import sys
import threading
from collections.abc import Iterable

import httpx

from app.memory.providers import CACHE_SIZE, OpenAIEmbedder
from app.memory.types import EMBEDDING_DIM
from tests.unit.test_memory_local_embedder import (
    _HANG_GUARD_S,
    _Call,
    _HeldCache,
    _join,
    _observe_lock,
    _text_vector,
)


class _GatedApi:
    """The embeddings endpoint as an ``httpx`` transport handler. The answer for the
    texts in ``held`` waits for ``release``: the test decides how many threads are inside
    the provider call (the network), and when they leave it."""

    def __init__(self, *, held: Iterable[str] = (), expect: int = 1) -> None:
        self.held = set(held)
        self.expect = expect
        self.inside = 0
        self.requests = 0
        self.first_inside = threading.Event()
        self.all_inside = threading.Event()
        self.release = threading.Event()
        self._guard = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        text = body["input"]
        with self._guard:
            self.requests += 1
        if text in self.held:
            with self._guard:
                self.inside += 1
                self.first_inside.set()
                if self.inside >= self.expect:
                    self.all_inside.set()
            if not self.release.wait(_HANG_GUARD_S):
                raise TimeoutError("the test never released the API")
        vector = _text_vector(text, body["dimensions"])
        return httpx.Response(200, json={"data": [{"embedding": vector}]})


def _gated(api: _GatedApi) -> OpenAIEmbedder:
    return OpenAIEmbedder(api_key="sk-test-not-a-key", transport=httpx.MockTransport(api))


def _single_threaded(texts: Iterable[str]) -> dict[str, list[float]]:
    """What one thread alone gets for each text - the answer every thread must get."""
    embedder = _gated(_GatedApi())
    expected = {text: embedder.embed(text) for text in texts}
    assert len({tuple(v) for v in expected.values()}) == len(expected), "one vector per text"
    return expected


def _full(embedder: OpenAIEmbedder) -> list[str]:
    texts = [f"metin {i}" for i in range(CACHE_SIZE)]
    for text in texts:
        embedder.embed(text)
    assert len(embedder._cache) == CACHE_SIZE and next(iter(embedder._cache)) == texts[0]
    return texts


def test_an_eviction_cannot_land_between_a_hit_and_the_refresh_of_its_place() -> None:
    """On a FULL cache whose oldest entry is ``oldest``:

        B  embed(new)     lookup: miss | API ...(gated)........ | store, evict the oldest
        A  embed(oldest)               lookup: hit | (held AT move_to_end) ...... refresh

    A has read its entry and is stopped at ``move_to_end``; B is let out of the API.
    Unlocked (or with only the store locked), B evicts the entry A just read and A's
    refresh raises KeyError. Locked, lookup and refresh are one section: B waits at the
    lock until A has refreshed, and then evicts the entry that really is the oldest."""
    new = "önbellekte olmayan metin"
    api = _GatedApi(held=[new])
    embedder = _gated(api)
    texts = _full(embedder)
    oldest, runner_up = texts[0], texts[1]
    expected = _single_threaded([oldest, new])

    b_stopped = threading.Event()  # B returned, or B is waiting at the lock
    observed = _observe_lock(embedder, b_stopped)  # type: ignore[arg-type]
    cache = _HeldCache(embedder._cache, refresh_key=oldest, refresh_until=b_stopped)
    embedder._cache = cache

    b = _Call(embedder, new, stopped=b_stopped)  # type: ignore[arg-type]
    b.start()
    assert api.first_inside.wait(_HANG_GUARD_S), "B never reached the API"
    a = _Call(embedder, oldest)  # type: ignore[arg-type]
    a.start()
    assert cache.at_refresh.wait(_HANG_GUARD_S), "A never reached the refresh of its hit"
    assert not b_stopped.is_set(), "nobody waits at the lock while B is inside the API"
    api.release.set()
    _join(a, b)

    assert a.error is None, f"A's hit lost its entry to B's eviction: {a.error!r}"
    assert b.error is None
    assert a.result == expected[oldest] and b.result == expected[new]
    assert observed is not None and observed.waited, "B was never kept out of the cache"
    assert len(embedder._cache) == CACHE_SIZE
    assert list(embedder._cache)[-2:] == [oldest, new], "A's hit made its entry the newest"
    assert runner_up not in embedder._cache, "the entry evicted is the least recently used"
    assert embedder.calls == CACHE_SIZE + 1 == api.requests


def test_a_hit_cannot_land_between_a_store_and_its_eviction() -> None:
    """On a FULL cache whose oldest entry is ``oldest``:

        B  embed(new)     lookup: miss | API | store new | (held AT popitem) ... evict
        A  embed(oldest)                                   lookup: hit | (held AT move_to_end)

    B has inserted its entry and is stopped before it evicts; A asks for the oldest entry.
    Unlocked (or with only the hit locked), A hits, B evicts that very entry, and A's
    refresh raises KeyError. Locked, store and eviction are one section: A waits at the
    lock, B evicts ``oldest`` whole, and A finds it gone - a miss, fetched again."""
    new = "önbellekte olmayan metin"
    api = _GatedApi()
    embedder = _gated(api)
    texts = _full(embedder)
    oldest, runner_up = texts[0], texts[1]
    expected = _single_threaded([oldest, new])

    a_stopped = threading.Event()  # A waits at the lock, or A is held at its refresh
    observed = _observe_lock(embedder, a_stopped)  # type: ignore[arg-type]
    popped = threading.Event()
    cache = _HeldCache(
        embedder._cache,
        refresh_key=oldest,
        refresh_until=popped,
        at_refresh=a_stopped,
        pop_until=a_stopped,
        popped=popped,
    )
    embedder._cache = cache

    b = _Call(embedder, new)  # type: ignore[arg-type]
    b.start()
    assert cache.at_pop.wait(_HANG_GUARD_S), "B never reached its eviction"
    assert new in cache and len(cache) == CACHE_SIZE + 1, "B is between its store and evict"
    a = _Call(embedder, oldest)  # type: ignore[arg-type]
    a.start()
    _join(a, b)

    assert a.error is None, f"A's hit lost its entry to B's eviction: {a.error!r}"
    assert b.error is None
    assert a.result == expected[oldest] and b.result == expected[new]
    assert observed is not None and observed.waited, "A was never kept out of the cache"
    assert len(embedder._cache) == CACHE_SIZE
    assert list(embedder._cache)[-2:] == [new, oldest], "A missed, and stored it again"
    assert runner_up not in embedder._cache, "A's store evicted the next least recently used"
    assert embedder.calls == CACHE_SIZE + 2 == api.requests


def test_a_cached_text_returns_while_another_thread_is_inside_the_api() -> None:
    """The provider call (the network, up to ``timeout_s``) is NOT made under the cache
    lock: a cached text is answered while another thread waits on the API."""
    cached, slow = "önbellekteki metin", "yavaş metin"
    expected = _single_threaded([cached, slow])
    api = _GatedApi(held=[slow])
    embedder = _gated(api)
    embedder.embed(cached)

    c_stopped = threading.Event()  # C returned, or C is waiting at the lock
    observed = _observe_lock(embedder, c_stopped)  # type: ignore[arg-type]
    b = _Call(embedder, slow)  # type: ignore[arg-type]
    b.start()
    assert api.first_inside.wait(_HANG_GUARD_S), "B never reached the API"
    c = _Call(embedder, cached, stopped=c_stopped)  # type: ignore[arg-type]
    c.start()
    assert c_stopped.wait(_HANG_GUARD_S)
    returned_meanwhile = c.returned and not b.returned
    waited = observed is not None and observed.waited
    api.release.set()
    _join(b, c)

    assert not waited, "the cached text waited for the lock B holds inside the API"
    assert returned_meanwhile, "the cached text did not return while B was inside the API"
    assert (b.error, c.error) == (None, None)
    assert c.result == expected[cached] and b.result == expected[slow]
    assert embedder.calls == 2 == api.requests and list(embedder._cache) == [cached, slow]


def test_two_threads_asking_for_the_same_uncached_text_may_both_call_the_api() -> None:
    """Neither waits for the other's request; both get the vector; ONE cache entry. The
    second paid request is the accepted price (ADR-0256 addendum: fractions of a micro-dollar
    per sentence) of never holding the lock across the network."""
    text = "iki iş parçacığının aynı anda sorduğu metin"
    expected = _single_threaded([text])
    api = _GatedApi(held=[text], expect=2)
    embedder = _gated(api)
    observed = _observe_lock(embedder, api.all_inside)  # type: ignore[arg-type]

    first = _Call(embedder, text)  # type: ignore[arg-type]
    first.start()
    assert api.first_inside.wait(_HANG_GUARD_S)
    second = _Call(embedder, text)  # type: ignore[arg-type]
    second.start()
    assert api.all_inside.wait(_HANG_GUARD_S)  # both inside, or the second at the lock
    inside = api.inside
    waited = observed is not None and observed.waited
    api.release.set()
    _join(first, second)

    assert not waited and inside == 2, "the second thread waited for the first one's request"
    assert (first.error, second.error) == (None, None)
    assert first.result == second.result == expected[text]
    assert first.result is not second.result, "each caller owns its list"
    assert embedder.calls == 2 == api.requests and list(embedder._cache) == [text]


def test_a_failed_request_stores_nothing_and_counts_nothing() -> None:
    """The store section is reached only with a vector: an API error leaves the cache
    and ``calls`` as they were, and the next ask tries again."""
    answers = iter([httpx.Response(503), None])

    def api(_request: httpx.Request) -> httpx.Response:
        answer = next(answers)
        if answer is not None:
            return answer
        vector = _text_vector("x", EMBEDDING_DIM)
        return httpx.Response(200, json={"data": [{"embedding": vector}]})

    embedder = OpenAIEmbedder(api_key="sk-test-not-a-key", transport=httpx.MockTransport(api))
    try:
        embedder.embed("x")
    except Exception as exc:  # noqa: BLE001 - the test reads it
        assert "503" in str(exc)
    else:
        raise AssertionError("a 503 was answered as a vector")
    assert embedder.calls == 0 and len(embedder._cache) == 0
    assert len(embedder.embed("x")) == EMBEDDING_DIM
    assert embedder.calls == 1 and list(embedder._cache) == ["x"]


def test_eight_threads_of_mixed_embeds_keep_the_size_bound_and_the_right_vectors() -> None:
    """8 threads x 200 embeds over more texts than the cache holds (hits, misses and
    evictions all the time), the interpreter switching threads as often as it can. The
    forced tests above are the proof; this one says the bound holds under load."""
    threads_n, embeds_n = 8, 200
    pool = [f"cümle {i}" for i in range(CACHE_SIZE + 188)]
    expected = _single_threaded(pool)
    embedder = _gated(_GatedApi())
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
    assert len(embedder._cache) == CACHE_SIZE, "more texts than the cache holds, bound kept"
    assert all(embedder._cache[text] == expected[text] for text in embedder._cache)
