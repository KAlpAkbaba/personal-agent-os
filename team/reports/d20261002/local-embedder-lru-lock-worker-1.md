## Şu an üzerinde çalışılan
`local-embedder-lru-lock` · alan: `services/api/app/memory/providers.py`, `services/api/tests/unit/test_memory_local_embedder.py`, `team/plans/local-embedder-lru-lock-adr.md` · makine: ev bilgisayarı (Windows), worktree `worker-local-embedder-lru-lock` · durum: bitti, itildi.

## Report
- **sha:** `7a4c3f985577afeaf8310195a69748a875a6c141`, pushed to `origin/team/d20261002/worker-local-embedder-lru-lock`; worktree clean.
- **Files:** 3, all inside the area (`providers.py` +21/−6, test file +293, ADR draft +64).
- **Change:** one `threading.Lock` on `LocalEmbedder` guards the cache and `calls` in two short sections: lookup + refresh, and store + evict. The model call and the vector copy are outside it. Two threads that miss on the same text both compute it: same vector, `calls` +2, one cache entry (stated in the ADR). Size 512, eviction order and hit/miss semantics are unchanged.

**The interleaving the test forces** (events only, no sleeps), on a full cache:
- B `embed(new)` misses and is held inside the gated fake model.
- A `embed(oldest)` hits and is held between `get` and `move_to_end`.
- B is released: unlocked, it stores and evicts the entry A just read, and A raises `KeyError`. Locked, B waits at the lock until A has refreshed the entry.
- The blocking model alone cannot force this, because the window sits between two cache operations; so the test also holds the cache's `get`.

**RED → GREEN (PROVEN_AUTOMATED)**
- HEAD `providers.py` (sha256 `2e087252…`): `test_an_eviction_cannot_land_between_a_hit_and_the_refresh_of_its_place` failed 3/3 with `KeyError('metin 0')`.
- Fixed (sha256 `d1658024…`): 22 passed, three runs in a row. All memory + understanding unit files: 441 passed. ruff check and format clean.
- Stress (8 threads × 2000, 700 texts, switch interval 1 µs): green on the fix. On HEAD it failed 7 of 10 runs, so it is a second opinion only.
- The "cached text returns meanwhile" and "both compute it" tests pass on HEAD (there is no lock to hold); they exist for the mutation below.

**Mutation RED (PROVEN_AUTOMATED)** — each restored from a backup copy to sha256 `d1658024…`:
- Lock removed → the forced test RED (`KeyError`).
- Only the lookup unlocked → same RED.
- Only the store unlocked → same RED.
- Model call inside the lock → "cached text returns meanwhile" RED, "both compute it" RED, and the forced test RED after its 20 s hang guard.

**Latency** (real `potion-multilingual-128M`, interleaved A/B in one process, 15 rounds × 2 runs; other workers' suites were loading the machine, so noise exceeds the difference):

| measure | before | after | per-round difference (median) |
|---|---|---|---|
| cached `embed()` | 842–1200 ns | 1100–1471 ns | +233 … +299 ns |
| uncached `embed()` | 416–516 µs | 373–551 µs | +6 … +18 µs, inside noise |
| build, 1427 exemplars | 407–749 ms | 442–668 ms | −16 … +18 ms, inside noise |

The shipped file holds 1427 exemplars, not ~1430. Solo-process baseline before the change (two runs): cached 762–771 ns, uncached 266–276 µs, build 417–447 ms. A solo-process run after the change was not taken on a quiet machine: NOT_RUN.

**Not done / NOT_RUN**
- mypy: not installed in the venv.
- Full unit suite and the gate: not run, only the 18 memory + understanding files.
- The real `configure_understanding` thread path: `startup.py` is not on this branch (it lives on `gate/d20261002-2`), so the build was timed through `combine.configure_default_engine` with that branch's `exemplars.json`.

**Open risks**
- `OpenAIEmbedder` carries the same unlocked LRU in the same file. I left it alone because the card names `LocalEmbedder` only; request threads share it, so the same window exists there. It needs its own card.
- The stress test adds about 4–7 s to the unit suite.
- Under the model-inside-lock mutation the forced test takes 20 s to go RED (hang guard).
- `LocalEmbedder` now holds a `Lock`, so it cannot be deep-copied or pickled; I found no code in `app/memory` or `app/voice/understanding` that does.
