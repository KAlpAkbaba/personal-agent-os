## Inspector report — `understanding-rules-read-lemmas` @ `9756e9ea` (second inspection)

**Pass 1 — re-run (all PROVEN_AUTOMATED, worktree clean, 6 files, all inside the area)**
- Four task files: 362 passed, 1 xfailed (the strict 95 % target test), 74 s. Matches the report.
- Owner Utterance Suite, one process: **2756 passed, 0 failed** (2754 cases + 2 totals), 17 min 13 s.
- STT report block, built from the branch's harness: `total_cases 106, correct 98, acted 97, questions 1, not_understood 2, wrong_device_actions 0, wrong_device_observable_cases 11, confident_wrong_readings 6, correct_rate 0.9245, BELOW_TARGET`.
- By distortion: polite 29/29, fused 24/29, diacritics 24/24, invented suffix 18/21. Failing set equals `KNOWN_GAPS` (8); the diff removes 25 entries and adds none.
- 61 neighbouring unit files that use the router or the understanding layers: 2053 passed, 0 failed.
- `ruff check` and `ruff format --check` clean on the five Python files. Hashes match the report (`intents.py d9c42028…`, `normalize.py 3f582858…`).
- Cost of `resolve_intent`: 2.38 ms per sentence on base, 2.33 ms on the branch. No CPX32 concern.
- False-split sweep: 27 358 distinct tokens from the repo's tests, docs and app, 11 splits, all genuinely fused. No false split.
- My mutations (scratch copy, restored from backup, sha256 equal each time): 6 of 8 RED.
  - RED: verb as first half; route into a mail/calendar action; quotes rewritten; `keep` list dropped; split ceiling at 0.9; punctuation after a bare negative ignored.
  - GREEN: "a token that divides two ways is split anyway" survives.
  - One mutation was invalid (it raised an IndexError), so it proves nothing.
- NOT_RUN: the full unit suite (I ran 65 of 515 files) and `quality-gate.ps1`. No table, migration, container or cloud script is touched, so no PostgreSQL run is owed.

**Pass 2 — findings (224 + 45 probe sentences, base `e1543a97` against the branch)**
1. **A "don't" sentence without punctuation now performs the forbidden action at HIGH.** The `_says_dont` guard treats a bare negative followed by any word as a verbal noun unless punctuation sits between them.
   - "Ekranları kapatma ışıkları söndürün" and "Ekranları kapatma ama ışıkları söndürün": `none` on base, `display_off` 0.9 on the branch.
   - "Gözünü kapatma ekranları kapatın" → `eye_disable`; "Alarmı kurma hatırlatma kurun" → `alarm_create`; "Not defterini kapatma hesap makinesini kapatın" → `app_close`.
   - "Kendi kendini geliştirmeyi duraklatma ama araştırmayı duraklatın": `explain` on base, `evolution_pause` on the branch.
   - 9 of my 25 unpunctuated negative probes flipped; with a comma the guard holds.
   - 0.9 is at or above `high` 0.85 and layer 3's negation cap does not apply to a rule reading, so the relay acts with no read-back.
   - ADR decision 4 claims the opposite, and the tests cover only the comma form. Chrome Web Speech (ADR-0173) writes no punctuation.
   - The bare twin already does this on main at 1.0; the branch extends it to every polite form.
   - Closing it costs nothing measured: of 2860 corpus sentences, 1 has a bare negative mid-sentence and 0 of those get a layer-1 reading.
2. **One claimed rule has no test.** "A token that could be divided two ways is left whole" (ADR decision 3, module docstring): removing it leaves both task files green.
3. **Dictated or reported speech after a verb no table owns** (declared by the worker, the lead's call). 14 of my probes went from `none` to `display_off`/`alarm_create` at 0.9: "Ali'ye söyle ekranları kapatın.", "Şunu çevir: ekranları kapatın.", "Ekranları kapatın ne demek?", "Bana yarın ekranları kapatın diye hatırlat.". Each bare twin gives the same intent on main at 1.0, so it is consistent with the card but a wider surface.
4. **The clause-break guard also rests on punctuation** (declared). "Araştırma ne durumda durdurun": `explain` on base, `research_cancel` 0.9 on the branch. "Alarm ne zaman kurun dedim?": `alarm_query` → `alarm_create`.
5. **A homograph lowers confidence without changing the route.** "Yazın hava nasıl olur?" stays `weather_query` but drops from 1.0 to 0.9 with `route_repair="polite"`. Still HIGH; the audit row says "polite" for a sentence with no request in it.
6. **Out of area, for the lead at merge:**
   - Fused readings act at HIGH: the report's bands are high 100, medium 2, low 4, because `policy.rule_reading` maps every repair to 0.9.
   - The xfail reason in `test_stt_utterance_corpus.py` still says "73/106".
   - The harness measures with no layer-2 engine, while production now has one configured.

No secrets, no paths, no contract drift, no KVKK-relevant logging in the diff. Rollback is a plain revert of two modules.

**Evidence classes:** both corpora and the acceptance sentences PROVEN_AUTOMATED; polite sentences by voice READY_FOR_OWNER; full unit suite and full gate NOT_RUN.

RETURN (1: make `_says_dont` hold without punctuation — a bare negative of a known verb before another word says "don't" unless it is provably the verbal noun — with router tests for the unpunctuated sentences above and ADR decision 4 corrected; 2: add a test that holds the "divides two ways" rule, or remove the claim)
