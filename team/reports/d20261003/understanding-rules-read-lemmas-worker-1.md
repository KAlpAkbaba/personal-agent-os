## Şu an üzerinde çalışılan
understanding-rules-read-lemmas, third pass (inspector-2 sent back items 1 and 2). Area: `normalize.py` and the two task test files, plus the ADR plan; `intents.py` and `stt_corpus.py` are not changed in this pass. Machine: the owner's build PC, worktree `worker-understanding-rules-read-lemmas`.

**Both returned items are fixed.** Commit `4e9a9b83145858cf7e460419ff3a0764b0db5d98` is pushed, and the worktree is clean. I merged `team/nightly/lead` (with main `65cd94ff`) first; no conflicts.

**Changes (4 files, all inside the area):**
- **Item 1, "don't" without punctuation:** A bare negative before another word now counts as "don't" unless the next word proves it is the verbal noun. The proof is a new check, `_is_compound_head`: the next word is a known noun, not a verb, and every reading of it has a possessive ending ("indirme klasörünü"). Words like "ışıkları", "ama", "hatırlatma", "kurun", a bare noun, or an unknown word prove nothing, so that sentence gets no polite reading and is resolved as heard.
- **Item 2, the "divides two ways" rule:** It now has a test, `test_a_token_that_divides_two_ways_is_left_whole`. The example is "masaüstümüziki", which splits as masaüstü + müziki and as masaüstümüz + iki. I found it by searching every pair in the vocabulary, and it was the only such token.
- **ADR plan:** Decision 4 is corrected and explains the change; decision 3 names its test; the third-pass corpus numbers are added.

**Tests (PROVEN_AUTOMATED):**
- **New router test:** the six unpunctuated sentences from the inspection, including "Ekranları kapatma ışıkları söndürün" and "Alarmı kurma hatırlatma kurun". Each gets no polite reading and resolves exactly as the surface rules do.
- **New `normalize` tests:** four sentences where a bare negative says "don't" with no comma, the compound-head check itself, and the two-ways test.
- **Red before the fix:** 10 failed, 4 passed (the two-ways rule already existed, so its test passed). **After:** 202 passed in the two task files.
- **Four task files:** 374 passed, 1 xfailed (the strict 95 % target test).
- **64 neighbouring unit files that use the router or the understanding layers:** 2330 passed, 0 failed.
- **Owner Utterance Suite:** 2756 passed, 0 failed (2754 cases plus 2 totals), 20 min 20 s. TMP and TEMP pointed at an empty folder on E:; it held 11 016 leftover folders afterwards, and I deleted it.
- **STT report block** (built from the branch): `total_cases 106, correct 98, acted 97, questions 1, not_understood 2, wrong_device_actions 0, wrong_device_observable_cases 11, confident_wrong_readings 6, correct_rate 0.9245, BELOW_TARGET`. Bands: high 100, medium 2, low 4. Polite 29/29, fused 24/29, diacritics 24/24, invented suffix 18/21. Unchanged from the last pass; `KNOWN_GAPS` is unchanged (8).
- **ruff:** `check` and `format --check` clean.

**Mutations** (restored from a backup copy each time; `normalize.py` sha256 `b50c7ed7…` matched before and after both):
- Compound-head guard switched off (`if False and …`): RED, 9 failed.
- Split allowed when a token divides two ways (`found[0] if found`): RED, 1 failed.

**Not done, and open risks:**
- Not run: the full unit suite and `quality-gate.ps1`.
- READY_FOR_OWNER: the polite sentences by voice.
- The bare twins of these sentences ("Ekranları kapatma ışıkları söndür") are resolved by the surface tables, unchanged from main. This pass guards only layer 1's reading.
- The guard leans toward "don't". For example, "indirme geçmişi" (without a further case ending) is treated as "don't". That costs only the polite reading, and neither corpus moved.
- Unchanged and out of my area: inspector findings 3–5 (dictated speech, the clause guard resting on punctuation, the "Yazın" homograph), and finding 6 for the lead. Fused readings still act at HIGH because `policy.rule_reading` maps every repair to 0.9, and the xfail reason still says "73/106".
