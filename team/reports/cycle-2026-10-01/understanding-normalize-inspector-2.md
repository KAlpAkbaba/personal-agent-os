**Inspector report: understanding-normalize (ADR-0224 layer 1), commit af22cac6**

**Scope.** The task commit touches exactly the 5 area files. `git diff main...HEAD` also shows lead files (queue, scripts, docs). They come from the base, not from this commit. Clean tree before and after my work.

**Pass 1: runs**
- `test_understanding_normalize.py`: 78 passed, 0 failed, 0.48 s.
- `test_owner_utterance_corpus.py`: 2747 passed, 0 failed, 589 s. The suite is 100 %.
- ruff on `app/voice/understanding` and the test file: all checks passed.
- I did not run the full `quality-gate.ps1`. This is a worker branch, not the integration branch.

**Mutations** (backup copy, sha256 `727279829ae7d163` before and after, restored each time). I ran them without `-x`, so every failing test shows.

| # | Mutation | Result |
|---|---|---|
| M1 | Known-stem guard forced to `True` | 53 failed. This includes `test_istedigim_keeps_its_surface_not_is`, `test_unutma_keeps_its_surface`, the trial sentence, and `kaydedin`/`istedim`/`ofisüm` staying whole. |
| M2 | Confusion lookup `if tok in table` → `if False` | 2 failed: the trial sentence and `test_default_reads_through_protocol_file`. |
| M3 (mine) | Re-attach round-trip check removed | 2 failed: `ofisüm` left whole, and the explicit empty confusion table. |
| M4 (mine) | `mı/mi` particle join removed | 7 failed: the `açar mısın`, `açabilir misin` and `kapatabilir misin` cases. |

The worker's report left one gap: it never showed that the `istediğim` test goes RED. It does (M1). I did not verify the worker's test-first claim. The worker says it was not re-derived.

**Pass 2: adversarial probes** (ran them directly)
- Words that share a prefix with a known stem stay whole: `açık`, `açıkla`, `veri`, `verim`, `yazı`, `bakım`, `kurdu`, `okul`, `arama`, `silme`, `silinir`, `unutmayın`, `istedim`, `istediğin`, `ofisüm`.
- Owner-relevant cases come out right: `unutma` and `istediğim` stay whole, and `bilgisayarımdan` → `bilgisayar` + (poss1sg, abl).
- Empty and whitespace input return empty tuples. `mısın` on its own stays whole.
- Real behaviour worth knowing:
  - `unutun` and `unutur` do lemmatise to `unut`, the delete word. Only the negative forms are protected. That is consistent with the design, but layer 3 (the threshold policy) must not treat a bare `unut` stem as safe to delete.
  - `işten` → `iş` is legitimate. The `iş` alias is a noun stem.
- No secrets or paths in the code, and no files outside the area. No contract drift, since nothing touches other modules. The only ones that touch the function are `intents.normalize_transcript`, called read-only and unchanged, and the new protocol file.
- Memory and CPU are fine: the suffix tables are built once at import and the confusion file is loaded once.
- Privacy: the confusion file holds one owner sentence from a trial, stored in the protocol file. That is a product decision, not a leak.

**Finding for the lead (not a RETURN).**
- Until `stt-confusions.json` is in `BUNDLED` in `app/protocol_files.py`, `normalize()` with no `confusions=` argument raises `KeyError: stt-confusions.json is not a bundled protocol file`. I reproduced it. The tests hide this with a monkeypatch. Nothing calls `normalize()` on this branch, so there is no runtime impact today, and the worker flagged the registration. It must be done at merge, before the next task wires the function into the router.
- Also at merge:
  - add the file to the falsification test list;
  - number the ADR and move it into `docs/DECISIONS.md`;
  - update HANDOFF and BUILD_STATE.
- No new dependency, so no THIRD_PARTY record.

**Evidence class: PROVEN_AUTOMATED.** The layer is not wired into the router, so nothing real could be run. There is no PROVEN_REAL claim. The rollback path is to remove the 5 files, since nothing imports them.

APPROVE
