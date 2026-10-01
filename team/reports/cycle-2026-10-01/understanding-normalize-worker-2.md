**Verdict: APPROVE.** The work is finished and committed. I re-ran every proof in this session.

**sha:** af22cac6a2d66fc7cb68781f9f0a72497f648514 on `team/cycle-2026-10-01/worker-understanding-normalize`. The worktree is clean. The push to the remote was not re-run here (NOT_RUN).

**Files changed by the task commit, 5, all inside the area:**
- `services/api/app/voice/understanding/__init__.py`
- `services/api/app/voice/understanding/normalize.py`
- `packages/protocol/stt-confusions.json`
- `services/api/tests/unit/test_understanding_normalize.py`
- `team/plans/understanding-normalize-adr.md`

**What it does:**
- `normalize(text)` returns `Normalized(text, tokens, lemmas, applied_confusions)`.
- `lemma_tokens(text)` returns the stem tuple.
- The suffix stripper is closed and table-driven. A suffix is dropped only when the remainder is a known stem (`_is_known_stem`).
- `normalize_transcript` in `app/voice/intents.py` and the rule tables are untouched.

**Tests:**
- The file `test_understanding_normalize.py` covers the trial sentence, the four 'aç' forms, the `istediğim` and `unutma` guards, `bilgisayarımdan` and the unknown-stem case.
- It also covers the schema check on the confusion file and 40+ surface→stem cases.
- I did not re-run the test-first step; the RED→GREEN history was not re-derived this session.
- Run this session (evidence PROVEN_AUTOMATED):
  - `test_understanding_normalize.py` plus `test_owner_utterance_corpus.py`: **2825 passed, 0 failed**, 589 s.
  - ruff on `app/voice/understanding` and the test file: all checks passed.

**Mutation proof** (backup copy, sha256 `727279829ae7d163…` before and after, tree clean afterwards):
- With the known-stem guard forced to `True`, `test_trial_sentence_normalises_to_stems_with_the_confusion_recorded` fails (1 failed).
- With the confusion lookup replaced by `if False:`, the same test fails (1 failed).
- Both ran with `-x`, so they stopped at that first failure. I did not look at whether the `istediğim`→'iş' test specifically also goes RED. It is covered by the test file, not shown by this run.

**Not done:** nothing was left out. A fresh `team/plans/…-adr.md` was not needed; the existing one is unchanged.

**For the lead at merge:**
- Register `stt-confusions.json` in `BUNDLED` in `app/protocol_files.py`.
- Add the file to the falsification test list.
- Number the ADR in `team/plans/understanding-normalize-adr.md` and move it into `docs/DECISIONS.md`.
- Write `docs/HANDOFF.md` and `state/BUILD_STATE.json`. The worker never touches them.
- No new dependency, so no THIRD_PARTY record is needed.

**Open risks:**
- The noun stem list is small and drawn from the corpus, so unknown nouns stay whole by design. The next tasks widen it.
- The confusion file has one seed entry, `ofisü` → `ofis`.
- The branch diff against main also shows lead files (`team/queue.json`, `scripts/tests/team-cycle.tests.ps1` and others). They are not part of my commit.
