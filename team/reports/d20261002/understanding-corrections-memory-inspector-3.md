# Inspector report — `understanding-corrections-memory` (`11a047d6`, fourth pass)

All four points of the last RETURN are fixed and the card's acceptance is proven, including on PostgreSQL. Two guards are correct but have no test; I list them for the lead rather than return the task a fifth time.

## Pass 1 — run it
| Check | Result | Class |
|---|---|---|
| `test_understanding_corrections.py` | 86 passed | PROVEN_AUTOMATED |
| Owner Utterance Suite | 2756 passed, 0 failed (3091 s) | PROVEN_AUTOMATED |
| 40 related unit suites (understanding, memory, migration gates, intents, media, relay, protocol/falsification) | 1227 passed | PROVEN_AUTOMATED |
| Postgres file on a fresh scratch database, migrated 0001→0064 | 4 passed | PROVEN_AUTOMATED |
| `test_migrations` + memory persistence/eval + the vocabulary file, same database | 16 passed | PROVEN_AUTOMATED |
| `ruff check .` (whole API); format on the 8 touched files | clean | PROVEN_AUTOMATED |
| Merge into main `5f250e5b` (`git merge-tree`) | no conflict; no other 0064 on any cycle branch | PROVEN_AUTOMATED |

- **Mutations (mine, 9, different from M1–M7):** 7 RED, 2 survived. Each was restored from a backup copy with sha256 equal (`intents.py` `a40dd72d…`, `corrections.py` `d56aad0c…`, `service.py` `33a9d5a1…`).
  - RED on the unit file: open-verb requirement removed; teach-time probe asked with the vocabulary active; repair readings removed; kept heard word dropped; layer override off.
  - RED on the Postgres file: taught reading disabled; kept heard word dropped; version check comparing row count only. That last one stays green on the unit file, because the unit supersede test never reads the vocabulary between the two lessons.
  - **Survived A:** the `owned_by_a_table(capped)` guard on the all-caps path removed. All 86 unit and 4 Postgres tests stay green, and "DOSYAYI AÇ", "DOSYAYI AÇ VE OKU", "ŞARKIYI AÇ", "MUZIK AC", "SARKIYI AC" become `app_open` with planted rows — the last RETURN's defect. With the guard in place the same five, and "MÜZİK AÇ", keep their intents (scratch probe, PROVEN_PROXY).
  - **Survived F:** the relay's `secret_rejected` audit branch disabled; 86 stay green. The row is in fact written (`{kind: app, written: false, reason: secret_rejected}`, no words) — seen in a scratch relay run, PROVEN_PROXY.
- **Host snapshot is stale:** `collected_at` 2026-10-01T19:18:51Z, before the 23:50 UTC release HANDOFF names. HANDOFF says that release carried no migration, and the shared dev database is still at `0063_team_state`. The lead collects a new one before the schema release.
- **State left behind:** my scratch database and files are gone, tree equals HEAD, no proposal file leaked into either checkout.

## Pass 2 — break it
1. **A taught application word takes no owned sentence.** With all 704 teachable corpus words planted as application rows, 0 of 2591 distinct corpus utterances that a table owns changed reading; 12 unrouted and 8 bare-title ones became `app_open`, which is the stated cost. With 36 common nouns planted: 0 owned changed of 468 sentences.
2. **Compound sentences read only the open verb (not new, low).** After the lesson, "Işığı aç ve hesaplayıcıyı kapat." and "Kapıyı aç ve hesaplayıcıyı sil." are `app_open` HIGH 1.0 and the tool call opens the calculator. The app's own name reads the same way today ("Işığı aç ve hesap makinesini küçült." is `app_open` HIGH, layer rule), so this is the application table's existing grammar.
3. **Held:**
   - Device-named taught sentence runs on the named machine ("Ev bilgisayarında hesaplayıcıyı aç." → calc on ev).
   - The secret and the taught word appear in no table outside memory.
   - Only the learned word produced a proposal file.
   - A lesson refused as `known_word` leaves no audit entry at all (not a defect, just silent).

## For the lead at merge
- Add two tests (about 15 lines): an all-caps owned sentence with a planted row keeps its intent; the relay audit row says `secret_rejected`.
- `docs/DATA_MODEL.md` still lists six memory classes.
- Do not upgrade the shared dev database to 0064 while sibling worktrees are at 0063.

## NOT_RUN
- Full `quality-gate.ps1` and the remaining unit suites: lead runs them on the integration branch.
- Integration suite on the shared dev database: it must stay at 0063 until merge, so I used a scratch database.
- `LocalEmbedder` near form.
- The owner's first correction in production: READY_FOR_OWNER.

APPROVE
