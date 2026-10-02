## Şu an üzerinde çalışılan
`understanding-corrections-memory` (ADR-0224 düzeltmeler, ikinci geçiş) — alan: `services/api` understanding / memory / relay — makine: sahibin geliştirme PC'si, worktree `worker-understanding-corrections-memory`. Durum: bitti, itildi, ağaç temiz.

## Report
- **sha:** `c627f0889e5ba4305f582bd22eeb6ad1b986afa2` (pushed; two commits on top of `0a581524`: `9cf045f5`, `c627f088`).
- **Files changed:** 9. Five are inside the card's area. **Four are outside it**, added because the return names them: `alembic/versions/20261001_0064_memory_vocabulary_class.py`, `app/memory/types.py`, `tests/integration/test_understanding_vocabulary_postgres.py`, `app/voice/realtime_sessions/service.py`. Drop them at merge if the area was not meant to widen.

**The three return points**
1. **Postgres.** Migration 0064 widens `ck_memories_class` by one value, and `MemoryClass.VOCABULARY` is now the real member (the stand-in enum in `policy.py` is gone). The new integration test writes a row through `learn()`, reloads it in a fresh runtime, corroborates, supersedes (via `learn` and via `supersede_memory`), and forgets it by its heard word. A second test downgrades to 0063 and shows `write_failed` with the session still usable.
   - RED: with the migration file removed, 1 failed (`IntegrityError`); restored with sha256 `8c979b94…` equal.
   - GREEN: 14 passed with `test_memory_persistence`, `test_migrations` and `test_memory_eval`.
   - Run in a scratch database on the dev stack's Postgres, dropped afterwards; the shared dev database is still at `0063_team_state`.
2. **"Ona ofis deme, hesap de" teaches nothing.** Refused for either kind: any form of "bilgisayar…" and any verb of the rule tables (new `intents.rule_verb_words()` reads the tables themselves). Refused as a device only: any word of an app alias phrase or Turkish app name, bare or with a closed case ending. A heard word over 32 characters is also refused.
   - RED first: 9 failed, then green.
3. **Relay wired** in `record_client_events` (five places, as in the earlier patch). The skip markers are removed, so both relay tests run; one now also says the refused pair to the relay.

**Tests**
- `test_understanding_corrections.py`: 45 passed, 0 skipped.
- Owner Utterance Suite: 2756 passed, 0 failed (1051 s), with the relay wired.
- Whole unit suite in chunks: 11,251 passed, 5 skipped, 1 failed. The failure was the expand-only gate wanting a `compat: widening` note on 0064; fixed in `c627f088` and that file re-run green. The other chunks were not re-run after that comment-only change.
- Ruff check and format are clean on every touched file.

**Mutations** (restored from backup copies; sha256 `954631ff…` and `3f717a53…` equal before and after)
- Index reload removed (the card's mutation) → 8 RED, including both relay tests.
- Router-word refusal disabled → 10 RED.
- Relay `vocabulary(db)` call removed → 2 RED.
- Relay `learn` call removed → 2 RED.
- Length bound removed → 1 RED.

**Evidence classes**
- Module, relay end to end on SQLite, corpus, mutations: PROVEN_AUTOMATED.
- Vocabulary row on PostgreSQL at 0064: PROVEN_AUTOMATED (committed test, real Postgres).
- `LocalEmbedder` near-form number: NOT_RUN.
- Full local gate script and the integration suites other than the four named: NOT_RUN.
- Owner's first correction in production: READY_FOR_OWNER.

**For the lead at merge** (details in `team/plans/understanding-corrections-memory-adr.md`)
- Renumber 0064 if another branch of this cycle also adds one; the integration test holds two `"0063_team_state"` literals.
- Do not run this branch's integration suite against the shared dev database while sibling worktrees are at 0063: their `alembic upgrade head` will not find revision 0064.
- This is a schema release; the old colour is compatible.
- `MEMORY_CLASSES` now has seven values, so `memory.remember` and `memory.search` offer `vocabulary`.
- HANDOFF, the ADR number and DECISIONS (ADR-0224 addendum 4) are yours. Nothing is needed for `protocol_files.py` or the falsification list.

**Open risks**
- The pair form still has no spoken receipt. An ordinary unknown word ("ona ofis deme, müzik de") is still learned from one sentence; that is what the pair form is for, but a misheard sentence can still teach a device word silently.
- Verb stems of three letters or more refuse by prefix, so a real name beginning with one ("sayfa…", "kur…") cannot be taught. It fails towards asking again.
- The turn's vocabulary stays in the ContextVar after the utterance (inspector's note 4); not changed.
- On the Cloud Core image no proposal file is written without `PAGENTOS_TEAM_PROPOSALS_DIR` (`no_proposals_dir`); unchanged.
