# Inspector report: `local-embedder-lru-lock` (round 3), branch @ `fe7bfb12`

**Scope**
- The diff touches only the three files in the card's area. No DB, migration, infra or `scripts/cloud` changes, so the Postgres/host-snapshot rule does not apply.
- Production change in `providers.py`, two locked sections:
  - lookup, plus `move_to_end` when it hits;
  - `calls`, store, `move_to_end`, and `popitem` when the cache is full.
- The model call and the `list()` copy are outside the lock. Stored vectors are never mutated, so copying outside the lock is safe.
- I read it as correct. The comment says outright that two threads may compute the same text.

**Pass 1: run it**
- `test_memory_local_embedder.py`: 24 passed in 3 of 3 clean runs (8.1–8.8 s). The import is the worktree's own `providers.py` (checked `__file__`); sha256 is `d1658024…`, matching the worker.
- Neighbouring suites (`test_memory*.py` + `test_understanding*.py`): **443 passed** in 224 s.
- `ruff check`: clean. `ruff format --check`: clean.
- mypy (ran via `uv tool run mypy` 2.4.0; the worker had it NOT_RUN): 1 error at `providers.py:322`, the `**model_factory` argument in `build_embedder`. The same error is on main (line 307), so it predates this task.
- My own mutations. Each was restored from a backup copy and the sha256 `d1658024…` confirmed every time; no `git checkout --`.

| mutant | result |
|---|---|
| M1: store and evict in two separate `with self._lock` sections | 6/6 green, 2 of 2 runs |
| M2: a fresh `threading.Lock()` per call (the lock excludes nothing) | RED 2 of 2: both forced eviction tests and the 8×2000 stress test fail |
| M3: only `_raw_embed` under the lock | RED: 4 tests |

- **M1 is an equivalent mutant, not a test gap.** Each section is atomic on its own. A hit that lands between the store and the evict just refreshes its entry, and the evict re-checks the length under the lock. No KeyError and no wrong vector is possible.
- **M2 failed for the real reason.** The forced tests raise `AssertionError: A's hit lost its entry to B's eviction: KeyError('metin 0')`, the real error, not a test-harness artefact.
- **M3** reddens "cached returns meanwhile", "both compute", "computed twice" and the hit/refresh test. Its sha `88b609ee…` is identical to the worker's own model-inside-lock mutant, so this confirms that row of their table.
- Latency spot-check, measured independently (fake model, main vs branch, two interleaved runs):

| | main | branch |
|---|---|---|
| cached `embed()` | 749 / 639 ns | 891 / 911 ns |
| uncached `embed()` | 59.2 / 59.3 µs | 58.9 / 65.9 µs |

  - Cached calls cost about +150 to +270 ns. That agrees with the ADR draft's real-model table (+233…+299 ns). The build-time difference is within noise, as the ADR says.

**Pass 2: try to break it**
- **Forced tests are deterministic.** The interleaving is set by events, with no sleeps. `_ObservedLock` turns "this thread waits at the lock" into an event. The 20 s waits are hang guards, not assertions.
- **No pass for the wrong reason.**
  - The no-lock case is handled: `_observe_lock` returns None, so `observed.waited` must fail.
  - The vectors are crc32-distinct, and `_single_threaded` asserts that, so "returned another text's vector" is detectable.
- **Acceptance items are all met:**
  - the forced interleaving test;
  - a cached text returns while another thread is inside the model;
  - LRU size and eviction order unchanged (the existing tests are green);
  - the 8×2000 stress test;
  - latency numbers quoted;
  - both required mutations RED.
- **Secrets, KVKK, resources:** no secrets, paths or personal data in code or logs. Memory and CPU on the CPX32 are unaffected: one lock per embedder.
- **Rollback:** reverting one commit, no schema involved.
- **Pickle/deepcopy claim:** the ADR says the lock makes `LocalEmbedder` unpicklable. Grepping `app/` finds no deepcopy or pickle of an embedder, so the claim holds.
- **Open, outside this task's area:** `OpenAIEmbedder` has the same unlocked LRU. The ADR records it; the lead should open a separate card.

**Evidence class:** PROVEN_AUTOMATED for lock correctness, the model call staying outside the lock, and LRU semantics. Latency is measured on this machine (PROVEN_PROXY).

The working tree is clean, my scratch files are removed, and nothing is left running.

APPROVE
