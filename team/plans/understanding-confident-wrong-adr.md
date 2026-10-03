# ADR (lead numbers it) — ADR-0224 addendum 4's gap, step 2: no confident WRONG reading of a broken word (2026-10-03)

Status: built (worker, card understanding-confident-wrong, cycle d20261003). Extends ADR-0269.

**The number (branch on 4aae2c9c, layers 1-3, no layer-2 engine in the harness).**

| | ADR-0269 (2026-10-02) | 2026-10-03 |
|---|---|---|
| correct | 98 / 106 = 92.45 % | **103 / 106 = 97.17 % - TARGET_MET** (99 acted + 4 questions) |
| invented suffix | 18 / 21 | **21 / 21** |
| fused | 24 / 29 | **26 / 29** |
| polite / diacritics / real | 29/29, 24/24, 3/3 | unchanged |
| wrong-device actions | 0 (11 observable) | 0 (11 observable) |
| confident wrong readings | 6 | **1** (the fused time, below) |

Owner Utterance Suite on the same sources: **2754 / 2754** (2756 tests passed, 46 min 50 s).

**The six of the day, and their causes.**

1. `op.app.1` / `op.app.8` invented ("Notü Defteri'ni aç.", "Hesapü makinesini aç."): the
   media table's bare-title guess took them at 1.0 - layer 1 could not read a word the STT gave
   an ending nobody said. Layer 1 now does (`normalize._invented`): ONE vowel after a
   consonant-final word it knows that is not a verb, and only a vowel that word's harmony
   cannot take - its own vowel with the dot lost or misplaced ("ekranlari", "haberlerı") is a
   letter confusion and is left alone. `lemma_reading` writes the word (`LemmaReading.invented`),
   the router routes it with `route_repair="invented"` at the confusion's 0.75: read back, never
   HIGH. A sentence a table owns as heard and reads the same way is not touched (`Saatü ...`).
   Held: no owner-corpus sentence carries an invented ending.
2. `selfdev.fix` fused / invented ("Şubug'ı kendin düzelt.", "Şu bug'ı kendinü düzelt."):
   `_memory_match`'s CORRECT branch (the bare stem "düzelt", any object) owned them at 1.0.
   Layer 1 learned "bug" (said "bag": `_FLAT` harmony, "bug'ı") and "kendin" (a closed word),
   so it reads them as `selfdev_fix` - and the router's owned-sentence rule kept memory_correct.
3. `creative.redraw` fused ("Buresmi Paint'te yeniden çiz."): the bare repeat (step 6, "yeniden"
   anywhere) owned it at 1.0. Layer 1 learned "resim" with its elision (`_ELIDED`: resmi).
4. **Decision - two claimants.** Where a REPAIRED word (a split or an invented ending) lets
   another table claim the very words the table as heard claimed (`_claim_the_same_words`: the
   matched words overlap, one clause), the result is the words-as-heard reading at 0.75, with
   `route_repair` naming the other ("fused+contested:selfdev_fix"). The policy
   (`rule_reading` -> `rival`, `decide(rival=...)`) never acts on it: LOW, ONE question naming
   both. A table that owns the sentence for OTHER words (a second command "Müziği durdur ve
   ekranlarıkapat", dictation after a clause break) keeps it as before. Polite forms are not
   contested: a polite form is a whole word the tables read as heard (ADR-0269 decision 2).
5. **One number for one decision.** The relay's adapter (`policy.rule_reading`) gave any repair
   the suffix-dropped 0.9 (HIGH) while the router gave a repaired word 0.75: a fused or invented
   repair is now a confusion in both.

6. **The fused time (second pass, area widened by the consultant).** `c.collision.alarm_create.fused`
   was read right (alarm_create, MEDIUM) but the alarm tool parses the sentence as heard:
   `tr_time._tokens` kept "yedibuçukta" one word, `_spoken_clock` found no hour, the tool
   refused. `_split_fused_fraction` now cuts a token into hour + "buçuk"/"çeyrek" (and their
   listed forms) only when the head is a number word, bare or with a clock case suffix
   ("sekizeçeyrek"), so no other word is ever cut. It left KNOWN_GAPS; the strict xfail on
   `test_stt_corpus_meets_the_target` is removed - the target is a plain test now.

**Final number (second pass):** 104 / 106 = 98.11 %, 100 acted + 4 questions, 2 not_understood
(`am.1.fused`, `mc.search.1.fused`), **confident_wrong_readings 0**, wrong-device 0, TARGET_MET.
