# Inspector report — `understanding-corrections-memory` (branch at `5b2e1dd2`, third pass)

**Verdict: RETURN.** Every acceptance item on the card is proven, including on PostgreSQL, but a taught application word takes over sentences that other rule tables own, at HIGH.

## Pass 1 — run it

| Check | Result | Evidence class |
|---|---|---|
| `test_understanding_corrections.py` | 69 passed (41.6 s) | PROVEN_AUTOMATED |
| Owner Utterance Suite | 2756 passed, 0 failed (2586 s, run alongside my other suites) | PROVEN_AUTOMATED |
| 15 related suites (memory, migration compatibility/agreement, understanding ×5, app-open fallback, local-mode relay) | 461 passed | PROVEN_AUTOMATED |
| Committed Postgres test, scratch database migrated 0001→0064 | 2 passed | PROVEN_AUTOMATED |
| `test_migrations` + memory persistence/eval + vocabulary test, fresh scratch database | 14 passed | PROVEN_AUTOMATED |
| The four relay tests through the real relay on PostgreSQL at 0064 (worker's NOT_RUN) | 4 passed; rows seen in PG (`ofüs = ofis (cihaz)`, `ofisü = ev (cihaz)`, `şirket = iş (cihaz)`) | PROVEN_PROXY — my scratch harness, not committed |
| `ruff check .` (whole API); format on the 8 touched files | clean | PROVEN_AUTOMATED |

- **Mutations (mine, different from M1–M5):** 11 of 11 RED, each restored from a backup copy with sha256 equal (`corrections.py` `d685efc4…`, `service.py` `3f717a53…`, `policy.py` `36fb8048…`, `intents.py` `8df2b6da…`, migration `36f81c71…`).
  - Version-check reload removed (the card's mutation): acceptance test RED.
  - Policy row 1a, `_unlike`, relay `ctx.pop`, exclusive create, `explicit=True`, `app_for`, `_fresh`, the "no first" rule, the ACTIVE filter: each RED.
  - Migration `_NEW` without `vocabulary`: both Postgres tests RED (`IntegrityError` → `write_failed`).
- **Host snapshot:** `collected_at` 2026-10-01T19:18:51Z, after the 16:27 UTC release. `memories.memory_class` is VARCHAR(32) (`vocabulary` is 10); `memories.key` is VARCHAR(256) (longest possible key about 105).
- **State left behind:** the shared dev database is still at `0063_team_state`; my scratch databases are dropped; `git status` is clean. A `pagentos_scratch_insp_vocab` database was already there before I started; I left it.

## Pass 2 — break it

1. **A taught application word steals rule-owned sentences at HIGH.** Through the real relay, "Ona not defteri deme, dosya de." is written silently and durably. Before it, "Dosyayı aç." was `artifact_open`, "Dosyayı aç ve oku." was `document_read`, and "Müzik aç." / "Şarkıyı aç." were `media_play`, all HIGH 1.0. After the lesson (and the matching ones for `müzik` and `şarkı`), all four are `app_open` HIGH 1.0 and the tool call ran `desktop.open_application`.
   - My probe had 30 common nouns accepted as application words (`kapı`, `alarm`, `rapor`, `mail`, `takvim`, `dosya`…); 17 of 30 follow-up sentences changed reading.
   - The module docstring says a word the system already reads is never learned; that holds for verbs, aliases and app names only. No test covers it.
   - It needs the deliberate pair form with a bare app name; I found no ordinary-speech path to it.
2. **Those stolen turns are recorded as layer `rule`, confidence 1.0.** The calibration data cannot tell that a vocabulary word decided the turn.
3. **The secret guard sees the normalised word (low).** "Ona chrome deme, sk-proj-abcdefghijklmnopqrstuvwxyz123456 de." is written as a memory row and into a proposal file name. The committed test only covers a hand-built `Correction`.
4. **Held:**
   - Audit rows carry no words.
   - `memory.remember` has no `value` argument, so the model cannot plant a synonym.
   - Old code reads the class as a plain string, so rollback is safe.
   - No client enumerates the classes.
   - No proposal file leaked into the checkout.
   - Device words bind only by position.

## NOT_RUN
- Full `quality-gate.ps1` and the remaining ~4,870 unit tests: not run alongside the corpus (they block each other on this machine). Lead runs `scripts\quality-gate.ps1` before merge.
- `LocalEmbedder` near form.
- Proposals in production: the Cloud Core image has no checkout, so without `PAGENTOS_TEAM_PROPOSALS_DIR` the row is written and no proposal is (`no_proposals_dir`).
- The owner's first correction in production: READY_FOR_OWNER.

`RETURN (1. an application word that a rule table already routes — "dosya", "müzik", "şarkı" — must be refused as known_word, or app_for asked only when the whole router answers NONE; RED-first relay test that the four sentences above keep their intents after the lesson. 2. a turn decided by an application synonym records layer vocabulary, not rule. 3. run the secret guard on the spoken sentence form, with a test through correction_turn. 4. commit a relay-on-PostgreSQL integration test; mine was scratch.)`
