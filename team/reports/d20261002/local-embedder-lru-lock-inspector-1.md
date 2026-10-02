**Inspector report — `local-embedder-lru-lock` @ `7a4c3f98` (pass 1)**

The lock itself is correct and holds against the real model, but the deterministic test proves less than the card asks: two partial-lock mutants that bring back the same `KeyError` pass it every time.

**Pass 1 — what I ran**
- **Diff:** the worker's commit touches only the three area files; `providers.py` is at sha256 `d1658024…` and the tree is clean after my runs.
- **Test file:** `test_memory_local_embedder.py` gave 22 passed, 3 runs out of 3; the stress test takes 3.9–4.6 s.
- **Lint:** `ruff check .` over `services/api` is clean and the two files are formatted. mypy is not installed in the venv: NOT_RUN.
- **Full API unit suite:** 14513 passed, 1 failed, 5 skipped, 1 xfailed in 54:40.
  - The failure is `test_qualification_evidence.py::test_every_proof_marked_row_points_at_something_that_exists`, on row 41.7 of `docs/QUALIFICATION.md`.
  - That row comes from the base commit `982dc4fb` (release record), not from this task. The lead should fix it on `team/nightly/lead`, otherwise the gate is red there.
- **Real model run (PROVEN_PROXY):** `potion-multilingual-128M`, one thread embedding the 1427 exemplars beside 6 request threads × 3000 mixed embeds.
  - Branch: 0 defects in 20 runs (10 at the default switch interval, 10 at 1 µs), 0 wrong vectors, cache at 512 and consistent.
  - Unlocked code: 0 of 10 at the default interval, 5 of 10 at 1 µs, each a `KeyError` on a request thread.
  - The real `configure_understanding` path is not on this branch: NOT_RUN; my loop makes the same `embed()` call per exemplar.
- **Cost (11 interleaved rounds, same process):**

| measure | before | after | paired difference (median) |
|---|---|---|---|
| cached `embed()` | 737 ns | 927 ns | +188 ns |
| uncached `embed()` | 213.6 µs | 211.3 µs | −5.6 µs (noise) |
| build, 1427 exemplars | 308 ms | 325 ms | +3.6 ms (noise; minimums 289 vs 286) |

  These agree with the worker's numbers.
- **PostgreSQL / host snapshot:** not applicable; no table, migration, store, compose file or cloud script is touched.

**Mutations (mine, each restored from a backup copy to `d1658024…`)**
- **Lock removed:** forced test RED 3/3. PROVEN_AUTOMATED.
- **Model call inside the lock:** "cached text returns meanwhile" and "both compute it" RED 2/2; the forced test also RED after its 20 s guard. PROVEN_AUTOMATED.
- **`popitem(last=True)`:** forced test RED 2/2. Eviction order is pinned only by this new test; no older test held it.
- **Hit's `move_to_end` moved outside the lock (lock covers `get` only):** survives. Forced test green 7/7; only the stress test caught it, 1 run of 7.
- **`popitem` moved outside the lock:** survives. Forced test green 7/7; stress caught it 2 runs of 7 (`KeyError('cümle 465')`).
- **New `move_to_end(key)` in the store path removed:** survives, 22 passed 2/2. The line is unpinned.

**Pass 2 — findings**
1. **The deterministic proof stops at "a lock is taken".** The forced test holds thread A inside `get`, which is inside the lock, not between `get` and `move_to_end`. The two surviving mutants above reproduce the task's exact bug, and their only guard is the stress loop, which passed by luck 11 of 14 times. The card forbids exactly that.
2. **An untested line in the store path.** `self._cache.move_to_end(key)` is new behaviour (a duplicate compute refreshes the entry's place) with no test.
3. **`OpenAIEmbedder` has the same unlocked LRU** (`providers.py:109-119`) and is shared by request threads through `asyncio.to_thread`. The worker flagged it; it is outside this card and needs its own.
4. **The card's failure scenario is narrower than stated.** The build thread only misses (1427 distinct exemplars), so in my runs the unlocked `KeyError` always landed on request threads (build thread died 0 of 40). The fix is still right.
5. **Nothing else found.** No secrets, contract drift or privacy leak; nothing in `app/` copies or pickles an embedder; the lock is never held across a call that could re-enter; rollback is a one-commit revert.

**Evidence classes**
- Lock correctness against no-lock and model-inside-lock: PROVEN_AUTOMATED.
- Atomicity of each locked section: stress only, so not proven deterministically.
- Real-model concurrency: PROVEN_PROXY.
- mypy and the real startup thread path: NOT_RUN.

RETURN (1. make the forced tests deterministic RED for both partial-lock mutants — hold A at `move_to_end` while B stores and evicts, and hold B at `popitem` after its insert while A hits the oldest entry — and show each RED with sha256; 2. pin the store path's `move_to_end(key)` with a test or remove the line)
