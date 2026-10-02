## Şu an üzerinde çalışılan
`understanding-corrections-memory` (ADR-0224 corrections, fourth pass) — area: `voice/understanding/corrections.py`, `voice/intents.py`, `realtime_sessions/service.py`, the unit and integration tests, the ADR text — machine: owner's dev PC, worktree `worker-understanding-corrections-memory`.

**All four points are fixed, committed and pushed; one design call on point 1 goes beyond the card's wording and needs the lead's eye.**

- **sha:** `11a047d68f9b012dcfc14fa7097b3d7c10f6c86d`, pushed, worktree clean.
- **Files changed:** 6, all inside the area.

**The four points**
1. **Taught application word no longer takes owned sentences.** `app_for` is now asked last, in `resolve_intent`, after the words as heard and both repair readings; `_app_open_match` reads the allow-list only. "Dosyayı aç", "Dosyayı aç ve oku", "Müzik aç" and "Şarkıyı aç" keep their intents after the lesson, even with rows planted directly. The lesson itself is also refused as `known_word`, by asking the router about "<word> aç" and its accusative.
2. **Layer.** A turn an application synonym decided records layer `vocabulary` (was `rule`); `matched` is `vocabulary:<app id>`.
3. **Secret guard on the spoken sentence.** `correction_turn` refuses the pair form with `secret_rejected` and `correctable` keeps no heard word from such a sentence. The relay's audit row carries the reason, no words.
4. **Relay on PostgreSQL, committed.** Two tests in `test_understanding_vocabulary_postgres.py` drive `create_app` on the real database.

**Decision for the lead (point 1):** a taught word outranks exactly one reading, the media table's bare-title last resort (`adıyla aç`).
- **Why:** that rule reads any two unknown words plus "aç" as a title. Under a strict "only when the router answers NONE", "Ofis bilgisayarında hesaplayıcıyı aç" resolved to `media_play`.
- **Cost:** with a row present, "<taught word> <unknown word> aç" opens the application where it was a title search before.
- **To go strict:** make `owned_by_a_table` return True for the bare-title match; the device-named sentence then goes back to media.

**Evidence**
| Claim | Result | Class |
|---|---|---|
| New tests RED before the fix | 16 failed, 3 passed | PROVEN_AUTOMATED |
| `test_understanding_corrections.py` | 86 passed | PROVEN_AUTOMATED |
| Owner Utterance Suite | 2756 passed, 0 failed (3513 s) | PROVEN_AUTOMATED |
| 30 related suites (understanding, intents, media, memory, relay) | 1069 passed | PROVEN_AUTOMATED |
| Postgres file, scratch database at 0064 | 4 passed (2 new relay tests) | PROVEN_AUTOMATED |
| `ruff check .` (whole API), format on touched files | clean | PROVEN_AUTOMATED |

The two Postgres relay tests were written after the fix, so their RED is by mutation only. The corpus process started before `ruff format` rewrapped the test file; app code was unchanged.

**Mutations:** 7 of 7 RED on the unit file; five re-run on the Postgres file alone, 5 of 5 RED. Each was restored from a backup copy with sha256 equal (`intents.py` `a40dd72d…`, `corrections.py` `d56aad0c…`).
- M1 `app_for` back inside the application table
- M2 teach-time probe removed
- M3 layer override removed
- M4 pair-form secret guard removed
- M5 kept-word secret guard removed
- M6 (the card's) index reload removed — acceptance RED, second call still LOW
- M7 bare-title counted as ownership

**For the lead at merge** (full list in `team/plans/understanding-corrections-memory-adr.md`)
- The Postgres relay tests teach fixed keys (`device:ofus`, `app:hesaplayici`, `app:dosya`) and forget them before and after. Run them on the dev stack or a scratch database, never production.
- Unit and integration files cannot share one pytest session; the unit conftest blocks a real database.
- Nothing new for `app/protocol_files.py` or the falsification list.
- Migration numbering and the schema-release note are unchanged from earlier passes.

**NOT_RUN**
- Full `quality-gate.ps1` and the rest of the unit suite.
- Integration suite on the shared dev database: it is still at `0063_team_state`; my scratch database is dropped.
- `LocalEmbedder` near form.
- Owner's first correction in production: READY_FOR_OWNER.

**Open risks**
- The teach-time probe asks the router in its default state; a word only a focus-dependent table reads is not refused at teach time, only never read where a table owns the sentence.
- An unowned common noun ("kapı") taught as an application still opens it on "kapıyı aç".
- The pair form still has no spoken receipt.
