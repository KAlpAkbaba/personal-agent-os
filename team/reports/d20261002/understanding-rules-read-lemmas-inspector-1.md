**Inspector report — `understanding-rules-read-lemmas` @ `9c211032` (code `1d30ee3c`)**

**Pass 1 — run it**
- **Task files:** rules-read-lemmas, normalize, STT corpus and regressions gave 343 passed, 1 xfailed (the target test), matching the report.
- **STT report block (my run):** `total_cases 106, correct 98, acted 97, questions 1, not_understood 2, wrong_device_actions 0, wrong_device_observable_cases 11, confident_wrong_readings 6, correct_rate 0.9245, BELOW_TARGET`.
- **By distortion:** polite 29/29, fused 24/29, diacritics 24/24, invented suffix 18/21.
- **KNOWN_GAPS:** 33 → 8, none new; the corpus diff is one hunk, no sentence touched.
- **Owner corpus:** all 2754 cases passed, but not in one clean run. The machine was running three other pytest suites, so I ran it in three overlapping segments.
  - Cases 1–1808 ran in one long process with 2 failures: `genesis.approve.yetkilendiriyorum.v1` and `genesis.cancel.vazgectim`.
  - Cases 1681–2100: 420 passed. Cases 2101–2754: 654 passed.
  - Both failed cases passed alone and in an in-order rerun of 1610–1640 (31 passed). I stopped the long process before its summary, so the failure text is lost and the cause is unknown.
  - The two aggregate tests were not run.
- **Other layers:** the remaining `test_understanding_*` files and `test_adr022402_wiring` gave 252 passed; ruff check and format are clean.
- **Area:** 6 files, all inside it. Source hashes equal the worker's before and after (`976c5102…`, `e472d80c…`).
- **NOT_RUN:** the full unit suite (14 693) and `quality-gate.ps1`; the lead's gate must cover them. PostgreSQL does not apply (no table, migration or store touched).

**Mutations** (13 of my own, on a scratch export; the worker's tree was never edited)
- **RED (9):** `is_negative` always False; bare negative at clause end not counted; `_says_dont` skipped; known word may split; mail/calendar action guard off; split ceiling 0.9; quotes unprotected; verb as first half; polite ceiling 1.0.
- **Survived (4)**, green in the two unit files and in STT + regressions:
  - `keeps_slots = False`: the test named "keeps the owner's own words" passes without the branch it claims to hold.
  - The "a mail/calendar-owned sentence is never re-read" guard removed (ADR decision 5 has no test).
  - Ambiguous split takes the first way: unreachable today, 0 of 9604 vocabulary pairs divide two ways.
  - `_MIN_HALF = 1`: equivalent, no one-letter known word exists.

**Pass 2 — break it**
- **Regression: a sentence a table already owns is overridden by a device action at 0.9.** In `_layer_one_route`, when the surface reading is owned and the bare-imperative reading reaches another intent, the second wins. The ADR's premise ("they reached it without the verb") is false for dictated content.
  - "Şunu hatırla: ışıkları söndürün." was `memory_remember` 1.0 on base and is `window_close` 0.9 now.
  - "Şunu yaz: sabah alarmı kurun." went from `type_text` to `alarm_create`.
  - "Yarın bana hatırlat: müziği durdurun." went from `memory_remember` to `stop`.
  - 23 of 176 probe sentences flip this way (owners: `memory_remember`, `type_text`, `explain`). The 2754 corpus holds no such sentence, so it stays green.
- **Negatives hold through the router:** kapatma, kapatmayın, kapatmaz mısın, unutma, unutmayın, the fused negatives, and "Işıkları söndürme, ekranları kapatın" all stay unrouted or `memory_remember`.
- **Mail/calendar actions hold:** Gönderin, Gönderir misin, Maili silin, Toplantıyı silin and fused "mailigönder" stay `none`, in the `draft_pending` and `event_focused` states too. `mail_read` is newly reachable as a query ("Mailleri okuyun.").
- **Polite equals bare, including the bare rule's own weakness:** "Ekranları kapatın demedim." is now `display_off` 0.9; "Ekranları kapat demedim." already was on base. Not a regression of this task, but the surface is wider.
- **Splitter precision:** I ran it over 32 410 distinct words from the repo's docs, tests and app text. It made 22 splits, all true fusions; no false split.
- **Cost:** 200 short sentences took 0.146 s before and 0.173 s after; a 2 600-character dictation is unchanged in practice.
- **Disclosed deviations I confirm:** "Raporu okuyun." stays 1.0 (the card contradicts itself; the worker followed the rule). A fused reading is 0.75 in the router but 0.9 through `policy.rule_reading`, so it is acted on without read-back; that one-line fix is outside the area and is the lead's.

**Evidence classes**
- PROVEN_AUTOMATED: the STT corpus numbers, the acceptance sentences, the negative and known-word mutations.
- PROVEN_AUTOMATED with the caveat above: owner corpus 2754 cases, segmented, 2 unexplained failures under load.
- NOT_RUN: full unit suite and gate.
- READY_FOR_OWNER: the polite sentences by voice.

**Return list**
1. A sentence owned by another table must not be turned into a different action by a polite form inside its content. Add a RED-first test with the three sentences above and their base intents; "Kendi kendini geliştirmeyi duraklatın." must still resolve to `evolution_pause`.
2. Add a test that fails when `keeps_slots` is removed, and one that holds "a mail/calendar-owned sentence is never re-read".
3. Correct ADR decision 2's claim to match the fix.

For the lead, not the worker: run the owner corpus in one process on a quiet machine before merge.

RETURN (1: owned dictation/remember sentence overridden by a device action at 0.9 — regression vs base; 2: two untested guards, one test passing for the wrong reason; 3: ADR claim)
