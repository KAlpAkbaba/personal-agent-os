**Report: answer-mode-intent-precision, second round**

sha `d01ed7308adfcb504d7fbe3ae097d39500eb03b8`, pushed, worktree clean.

**"Şu an üzerinde çalışılan" block:** task `answer-mode-intent-precision`, area voice intents/tools/extraction plus the corpus, machine MAIL worktree. I did not touch `HANDOFF.md`; the lead writes it from this report.

**Files changed this round:** 4. One is outside the card's area list: `tests/voice_corpus/corpus.py`. The card and your return note both required the corpus rows, so I added them.
- `app/voice/intents.py`
- `tests/unit/test_voice_intents_answer_mode.py`
- `tests/voice_corpus/corpus.py`
- `team/plans/answer-mode-intent-precision-adr.md`

**The EXEC_START regression (your blocking point)**
- **Fix:** `_executive_start_match` now returns None when the sentence is a standing sentence and carries a read verb. A standing "…oku" is never a mission start.
- **Result:** the trial sentence and its İngilizce variant now resolve to `Intent.NONE`, klass `query`. "Artık raporları hep Almanca anlat" resolves the same way.
- **Path:** NONE is the ordinary model/ack path. No language-preference intent exists, so I added no phrase table.
- **RED proof:** before the guard, the trial sentence resolved to `exec_start`, klass `action`, `exec_shape` research_report. This was my own probe output, matching the inspector's.

**Tests tightened**
- The language-sentence test now asserts `intent is Intent.NONE`, `klass != "action"`, `answer_level is None` and `exec_shape is None`. Before, it only excluded two intents.
- New test for the inspector's point 3. "Bundan sonra raporları ayrıntılı oku" resolves to `DETAIL` and "…araştırmaları teknik oku" to `TECHNICAL`, both with `klass != "action"`. They are narration controls, not the durable register.
- ADR item 5 records that decision. To set the durable register the owner says "bundan sonra ayrıntılı anlat", because the answer-mode phrase needs anlat/konuş/cevap/söyle.
- ADR item 4 records the EXEC_START guard.

**Mutation for the new guard**
- Guard replaced with `if False:`. sha256 before and after `868e3f0a…a7c2ba`; restored from the backup copy, not `git checkout`.
- The two trial-sentence cases went RED (2 failed, 8 passed); the Almanca case still passes, because it never reached EXEC_START.
- The earlier mutations (speaker-prefix skip, research-open standing guard, `tools.py` clause) were not re-run this round. Those files did not change, and the inspector re-ran the `tools.py` one.

**Corpus:** cases `r.lang.1`, `r.lang.2` and `r.lang.3` are added. They expect intent none, no tool, and forbid `research.open`, `research.answer_mode` and `executive.start`.

**Run (evidence: PROVEN_AUTOMATED)**
- Suites: voice corpus, voice intents, b31, owner-utterance corpus and memory extraction. 3035 passed, 0 failed, 583 s. The count is lower than the earlier 3039 only because I left out the realtime-sessions suite, which I did not touch.
- `ruff check app tests/voice_corpus` and the new test file: clean.
- The extraction skip (c) and the tool refusal (b) are unchanged since the first round and covered by the same suites.

**NOT_RUN**
- Full `quality-gate.ps1`: it is a worker branch.
- Realtime-sessions suite this round.
- PROVEN_REAL: waits for the owner to repeat the sentence after release.

**Open risks**
- The owner's durable level is still `detail` in production. Reverting it is the owner's decision, as the lead already asked.
- Edge cases in extraction are unchanged from round one: `Assistant :` with a space before the colon, and a `|` inside an owner sentence.
