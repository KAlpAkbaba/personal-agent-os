**Inspector report: answer-mode-intent-precision** (branch sha 0fa841a9)

**Pass 1 — run it**
- The worker's test set (new tests, extraction, b31, intents, corpus, owner-utterance corpus, realtime sessions) gave 3039 passed, 0 failed, in 581 s. I ran it from the worktree's own `.venv`, and `app` imported from the worktree.
- My own mutation was different from the worker's: I removed the `intent != "research_answer_mode"` clause in `tools.py`. That was RED (1 failed, 7 passed). I restored the file and the sha256 matches (`a6d1f897…`). The tree is clean.
- I did not run the full `quality-gate.ps1`; this is a worker branch, not the integration branch.
- No real run was possible without the owner.

**Pass 2 — break it**
1. **Regression (blocking).** I ran the trial sentence "Bundan sonra araştırma raporlarını her zaman Türkçe oku" through `resolve_intent` at HEAD. It resolves to `Intent.EXEC_START`:
   - `capability='executive.start'`, `klass='action'`, `exec_shape='research_report'`, `matched='araştırma + rapor'`.
   - Before the fix, `RESEARCH_OPEN` shadowed this branch. The new guard lets the sentence fall through to it.
   - So a standing language preference now resolves to an action-class intent, where before it was a read. That could launch a research-report mission from a preference sentence.
   - The worker's report says "düz yola düşer (Intent.NONE)", which is false for this sentence. "Artık araştırmayı Türkçe oku" does give `none`.
2. **Tests pass for the wrong reason.** `test_a_standing_language_sentence_is_neither_a_read_nor_a_level` only asserts `not in (RESEARCH_OPEN, RESEARCH_ANSWER_MODE)` plus `answer_level is None`. It passes while the sentence is `EXEC_START`. The worker said the goal was "preference/acknowledgement path", and the test never checks that.
3. **Other sentences from the same probe:**
   - "Bundan sonra raporları ayrıntılı oku" resolves to intent `detail`, with `answer_level` None.
   - "Bundan sonra araştırmaları teknik oku" resolves to `technical`, with `answer_level` None.
   - Both are routed somewhere other than `RESEARCH_ANSWER_MODE`, so a standing "…ayrıntılı oku" no longer sets the register.
   - This is plausibly intended (the read verb is not the answer-mode phrase), but the ADR doesn't say so.
4. **Extraction (c).** I read the diff and the tests and have no complaint. The `Assistant :` variant with a space before the colon, and a `|` inside the owner's sentence, are edge cases the worker already noted. I did not run them: my probe crashed on `extract_from_summary`'s signature (`session, embedder, summary`).
5. **No secrets, paths or contract drift.** Only files in the task area were touched. The branch diff against `main` lists 21 files because `main` is behind the branch base; the worker's commit itself is 6 files.
6. **Corpus row missing.** The three sentences are not in `tests/voice_corpus/corpus.py`. The worker flagged this: the file is outside their area. The card required it, so it is still open.
7. **Stricter than the card.** `research_answer_mode` now also refuses the `research_open` turn record's `detail`. That is correct and deliberate.

**Evidence classes**
- Extraction skip (c): PROVEN_AUTOMATED.
- Tool refusal (b): PROVEN_AUTOMATED.
- Router (a): NOT_RUN — the claim that the sentence lands on a safe path is false for the trial sentence.
- PROVEN_REAL: owner repeats the sentence after the release.

**Required to clear the RETURN**
- Stop the trial sentence falling into `EXEC_START`: give the standing-marker plus language-word sentence a safe non-action resolution, or exclude it from the `exec_start` match, and read the `EXEC_START` precedence at `intents.py` ~9311–9330 to choose.
- Tighten the test to assert the exact intent, and assert `klass != "action"` for the three language sentences.
- Add the three sentences to the corpus, or get the lead's explicit OK to defer that.
- Write the `detail`/`technical` behaviour for the "…oku" sentences into the ADR.

RETURN (trial sentence now resolves to `EXEC_START`, an action-class intent that can start a research-report mission, and the test does not catch it; tighten the test; corpus rows still missing)
