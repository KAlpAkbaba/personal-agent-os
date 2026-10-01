**Inspector report — answer-mode-intent-precision (round 2)**

**Pass 1 — run**
- Targeted suites (answer-mode, extraction incl. assistant-lines, realtime-sessions, b31, memory-extraction): 112 passed, 0 failed.
- Corpus suites (`test_voice_corpus*`, owner-utterance corpus, extraction every-mode): 2794 passed, 0 failed, 648 s. This includes `r.lang.1-3`.
- `ruff check app/voice app/memory`: clean.
- Tree is clean after my mutations. sha256 before and after match for all three files:
  - `extraction.py` `a4f37945…`
  - `intents.py` `868e3f0a…`
  - `tools.py` `a6d1f897…`

**My own mutations** (different from the worker's, restored from backup copy, not `git checkout`)
1. **English speaker prefix dropped** (`assistant` removed from the regex): RED. 1 failed, 4 passed (the `| Owner: … | Assistant: …` case).
2. **`_is_standing_sentence` forced to False**, which disables all three guards: RED. 4 failed, 6 passed. This covers the trial sentence, the İngilizce and Almanca variants, and the neighbour test.
3. **`tools.py` model-argument fallback restored**: RED. 1 failed, 53 passed (`test_answer_mode_refuses_a_turn_without_a_level_word`).

**Real behaviour probe** (`resolve_intent`, venv confirmed on this worktree's files)
- Trial sentence → `none/query`, no level, no exec_shape. Same for "…İngilizce oku", "Artık hep Türkçe konuş", "Bundan sonra raporları Türkçe anlat" and "Her zaman Türkçe cevap ver".
- Neighbours keep their intent: "Bundan sonra teknik anlat" → `research_answer_mode` technical; "Teknik modu kapat" → `research_answer_mode` executive; "Araştırmayı oku" → `research_open` detail; "Bundan sonra kısa anlat" → `research_answer_mode` executive.
- The EXEC_START regression I expect was blocked in round 1 is fixed. The trial sentence no longer becomes an action.

**Pass 2 — break it**
- **Contract and area:** the diff touches only the task area plus `corpus.py`, which the card and the return note require. Secrets and paths: none. Device and browser contracts: untouched. The branch also carries the lead's team-infrastructure commits (queue, cycle scripts), which came from the base, not from this worker.
- **Privacy:** extraction now files fewer lines. No new text reaches audit or logs.
- **Rollback:** a plain revert. The change is stateless.
- **Non-blocking findings:**
  1. "Bundan sonra özeti Türkçe anlat" still resolves to `research_answer_mode` executive. "özet" is read as a level word. It is defensible, but it is a language sentence that could still set the durable register. A card for a language-preference intent should cover it.
  2. "Bundan sonra araştırma raporlarını teknik oku" resolves to the one-off `technical` control, not the durable register. This is recorded as deliberate in ADR item 5 and pinned by a test.
  3. Extraction edge cases are open, as the worker said: `Assistant :` with a space before the colon, and a `|` inside an owner sentence. Neither is exercised by the trial.
  4. The ADR has no number yet. The lead assigns one at integration.
- The owner's durable level is still `detail` in production. That is the owner's decision, already raised by the lead.

**Not run:** the full `quality-gate.ps1` (worker branch, not the integration branch) and the Docker-dependent integration suites. A real device run needs the owner.

**Evidence classes**
- Rules (a), (b), (c): PROVEN_AUTOMATED. Unit, corpus, three mutations RED, and an `resolve_intent` probe.
- Owner repeats the trial sentence after release: READY_FOR_OWNER.

APPROVE
