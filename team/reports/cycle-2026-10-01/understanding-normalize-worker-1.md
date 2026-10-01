**Report: understanding-normalize** (ADR-0224 layer 1)

**Şu an üzerinde çalışılan:** understanding-normalize / `services/api/app/voice/understanding/` + `packages/protocol/stt-confusions.json` / machine: dev PC, worktree `worker-understanding-normalize`.

**sha:** `af22cac6a2d66fc7cb68781f9f0a72497f648514`. Pushed; the worktree is clean.

**Files changed:** 5, all inside the area.
- `app/voice/understanding/__init__.py`
- `app/voice/understanding/normalize.py`
- `packages/protocol/stt-confusions.json`
- `tests/unit/test_understanding_normalize.py`
- `team/plans/understanding-normalize-adr.md`

**What it does** (`normalize`, `lemma_tokens`, `load_confusions`, `Lemma`, `Normalized`)
- The stripper is closed and table-driven. A suffix chain is dropped only when the ending is in a generated table, re-attaching the chain gives the token back (harmony check), and the remaining stem is known (`_is_known_stem`).
- Verbs: a 50-stem table with explicit aorist endings. Forms covered: bare, `-ın`, `-ınız`, `-sana`, aorist, `-abilir`. A `mısın`/`misin`/`misiniz` particle after an aorist or `-abilir` form is absorbed into one lemma (suffixes `aor`/`pot`, `q`).
- Nouns: a 50-noun list from the corpus vocabulary, plus app alias words and device aliases. Suffixes: plural, possessive, case.
- `normalize_transcript` is untouched. No rule tables were changed.

**Tests:** 78 in `test_understanding_normalize.py`.
- RED first: run with `normalize.py` moved aside, the test file failed at import (`ImportError`, collection error).
- GREEN: 78/78 afterwards. Ruff check passes; ruff format applied to my files.
- Covers the trial sentence → `('ofis','bilgisayar','hesap','makine','aç')` with `(ofisü→ofis)` recorded; the `açın`/`açınız`/`açsana`/`açar mısın`/`açabilir misin` family; `istediğim` and `unutma` kept whole; `bilgisayarımdan` → bilgisayar + (poss1sg, abl); unknown stems left whole; the file schema (required keys, no duplicates); 52 surface→stem cases.
- Owner Utterance Suite: 2747 passed, 0 failed (10:45), which is 100%. That run predates the ruff-format pass on my own files, which the corpus never imports.

**Mutation proof** (restored from the backup copy, `cmp` identical each time; sha256 before and after `de9070042799…`)
- Known-stem guard → `return True`: 53 RED. `istediğim` and `unutma` tests fail, and I checked them alone.
- Confusion lookup removed: 2 RED.
- Harmony re-attach check removed: 2 RED.
- `mi`-particle absorb removed: 7 RED.

**Evidence class:** PROVEN_AUTOMATED for all of the above. Nothing was run against a real device or the router, and nothing is wired into it yet.

**Deviations and risks**
- `raporlarını` is `pl + poss3sg + acc` (`lar-ı-nı`), not `pl + acc` as the card wrote; `pl + acc` would give `raporları`. `poss2sg + acc` reads equally well; I put 3sg first in the order.
- I wrote the module before the test, then proved RED by removing the module. That is weaker than a true RED-first run.
- `işte` and `işteki` are dropped as fillers by `normalize_transcript` before layer 1 sees them, so the `iş` stem is tested through `işi`.
- Unknown or stem-mutating verbs (`kaydet`, `git`) and ASCII-folded input (`ac`) are not lemmatised. Layer 2's fuzzy match is meant to cover those.
- The test monkeypatches `BUNDLED` and `BUNDLE_DIR` to read the shared JSON. Until the lead registers the file, the default call `normalize(text)` raises `KeyError` outside tests.
- No new dependency.

**For the lead at merge**
1. Add `"stt-confusions.json"` to `BUNDLED` in `app/protocol_files.py`.
2. Run `scripts/sync-protocol-bundle.py` so `app/protocol_bundle/stt-confusions.json` is byte-identical.
3. Register it in the falsification test list and the quality-gate/ci lists. `test_protocol_bundle.py` will then cover drift.
4. The monkeypatch fixture in my test can be dropped once (1) and (2) are done.
5. Number `team/plans/understanding-normalize-adr.md` and move it into `docs/DECISIONS.md`.
6. Write the HANDOFF block from this report; I did not touch `docs/HANDOFF.md`.
