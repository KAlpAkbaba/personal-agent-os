**Inspector report: understanding-rules-read-lemmas, third pass (`4e9a9b83`)**

**What I ran (clean worktree; `normalize.py` sha256 `b50c7ed7…` matches the report)**
- **Four task files** (rules_read_lemmas, normalize, stt_utterance_corpus, voice_corpus_regressions): **374 passed, 1 xfailed** in 187 s. This matches the worker's numbers. The STT ratchet test is green with KNOWN_GAPS unchanged.
- **My own mutation** (different from the worker's): in `_is_compound_head` I changed `all(...)` to `any(...)`. Result: **RED**, 2 failed, 200 passed (the EYE_DISABLE row of the new router test, and `test_the_compound_head_is_proven_only_when_every_reading_is_possessive`). I restored the file from a backup copy, the sha256 matched again, and the tree is clean.
- **Router probes on the acceptance sentences:** 'Ekranları kapatın' gives display_off 0.9, 'Raporu okuyun' gives research_open 1.0 from the surface tables, 'Alarmı kurar mısınız' gives alarm_create 0.9, 'Araştırmayı durdurabilir misin' gives research_cancel 0.9. 'hesapmakinesini aç' gives app_open 0.75 and 'alarmkur' gives alarm_create 0.75. 'Ekranı kapatma' and 'Ekranları kapatmayın' give none.
- **Not re-run:** the Owner Utterance Suite (~20 min), because the defect below decides the verdict whatever it shows. The full unit suite and `quality-gate.ps1` were not run either.

**Finding 1: the branch still turns a "don't" into its positive (it adds this case; main does not do it)**
- `'Ekranı kapatma sesini kapatın'` ("don't turn off the screen, mute its sound", the way speech-to-text writes it, with no comma):
  - Surface rules (main behaviour): **none 0.0**.
  - Branch: **display_off 0.9, route_repair=polite**.
  - The router would turn the screen off, which is the opposite of what the owner said, at the confidence the worker notes acts at HIGH.
- `'Ekranı kapatma sesini açın'` gives **display_wake 0.9 polite**, the same mechanism.
- **Cause:** `_is_compound_head("sesini")` is True, because every reading of "sesini" carries a possessive. So `_says_dont` treats "kapatma" as the verbal noun and the polite reading goes ahead.
  - A possessive head after the negative does not prove a verbal noun when the word before it is an accusative object of that verb. "**Ekranı** kapatma" can only be "don't close the screen"; a verbal noun would not take the accusative "ekranı".
  - This is the same class as inspector-2's finding 1. The guard was moved from punctuation to the next word, but it still trusts one neighbour.
- This breaks the task card's binding rule: "a NEGATIVE form never becomes its positive". The new test only checks heads that prove nothing (ışıkları, ama, ekranları, hatırlatma); no row has a possessive head after an accusative object.
- **Fix direction (worker's choice):** an accusative object of the same verb right before the bare negative proves "don't". Alternatively, any sentence carrying a bare negative of a verb whose positive the reading would resolve to gets no polite reading. Add a test row with 'Ekranı kapatma sesini kapatın' that is RED before the fix, and a mutation for the new rule.

**Seen but not this branch's (for the lead)**
- On main, the surface tables send 'Araştırmayı durdurma raporunu durdurun' to exec_start 1.0, and the bare twin 'Ekranı kapatma sesini kapat' to display_off 1.0. These are pre-existing surface-table defects and need their own card.
- 'Bunu unutma' gives memory_remember 1.0, which is correct (it is not forget).

**Evidence classes**
- The task tests and my mutation: PROVEN_AUTOMATED.
- Owner corpus 2754/2754: NOT_RUN by me; the worker reports it passing.
- The polite sentences by voice: READY_FOR_OWNER.
- This task touches no database or infrastructure, so there is no PostgreSQL debt.

This is the task's second RETURN, so under the role file it stops the task.

RETURN (1: 'Ekranı kapatma sesini kapatın' resolves to display_off 0.9 through the polite reading where surface rules give none — a possessive head after an accusative object must not lift the "don't" guard; add the RED test row and a mutation for the rule)
