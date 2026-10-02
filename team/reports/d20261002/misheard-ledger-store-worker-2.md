## Şu an üzerinde çalışılan
`misheard-ledger-store` · alan: `app/voice/misheard/*`, migration 0065, `alembic/env.py`, `app/main.py`, üç test dosyası, ADR metni · makine: MAIL (worktree `team/d20261002/worker-misheard-ledger-store`). **Bitmedi: 24 saatlik süpürme döngüsü lifespan'e bağlanamadı (ALAN_ISTEGI aşağıda); gerisi tamam.**

## Report
- **sha:** `9d480c4eb727252190f472d512fa593b744f2548`, equal to origin; worktree clean, nothing running. The branch was rebased onto `integrate/d20261002` (`1b08509a`) and force-pushed with a lease pinned to `dd036e5c`.
- **Files:** 11, all inside the area. The migration is `20261002_0065_misheard_utterances.py` on `0064_memory_vocabulary_class`; `alembic heads` shows one head and `alembic check` reports no drift for the table.
- **ALAN_ISTEGI:** `services/api/tests/unit/test_bounded_delivery.py`, `services/api/tests/unit/test_health_endpoint.py`. With the loop started in the lifespan, `test_every_background_loop_the_app_starts_can_be_seen_in_health` went RED: a started loop needs a health key, and both pinned lists are outside the area. The lifespan therefore runs one purge at start and starts no loop. `PurgeLoop` (24 h, clean cancel) is built and unit-tested but not started; the ADR lists the lines needed to finish it.

**Return points**
1. Done (rename, re-chain, rebase).
2. Implemented, except the 24 h loop above. `record()` and the owner's GET also purge.
3. `test_misheard_postgres.py`: 6 passed on the dev stack's PostgreSQL. It covers columns and widths from `information_schema`, the round trip with a 200-character `tool`, the savepoint (a real server `DataError`, then the next statement succeeds), a `record()` against the 0064 schema, and downgrade/upgrade.
4. Two wordless-log tests read `structlog.testing.capture_logs()` and the house configuration's printed lines, plus caplog. Both assert the fault events were logged, so they are not vacuous. The marker is ASCII, because the JSON renderer escapes Turkish letters.
5. `tool` is `VARCHAR(64)` and `record()` cuts it; it is kept for `tool_failed` only. **The other three misheard-* cards need the same width.**
6. `test_the_lifespan_purges_at_start` goes through the real app's lifespan. Clean cancel is proven on `PurgeLoop` itself in the store tests, not in the lifespan.
7. Mutations, each restored from a backup copy with sha256 identical (`service.py` `ad0d1915…`, `main.py` `0bbf91a2…`):
   - `listen_only` guard removed → 4 failed.
   - `list_items` expiry filter removed → 1 failed.
   - Idempotence check removed → 1 failed.
   - Extra: savepoint removed → Postgres test failed; error text logged → both log tests failed; tool cut removed → failed on SQLite and Postgres; lifespan purge removed → failed.
8. Counts: store 37 functions / 54 cases, routes 13 / 18, Postgres 6 / 6.

**Inspector findings also addressed:** an unknown `band` or non-finite `confidence` is stored as null; `is_request` takes a count or the readings; the hold is gone at exactly 120 s. Two red-first tests were wrong and are fixed: `_seed(engine, …, engine=…)` was a TypeError, and the GET-purge test seeded in an order where `record()` did the purging.

**Suites on `9d480c4e` (PROVEN_AUTOMATED)**
- Unit: `3 failed, 14719 passed, 5 skipped, 1 xfailed` (1:39:46). None of the three comes from this change; each was reproduced on an untouched copy of `integrate/d20261002`:
  - `test_postgres_coverage_ratchet::test_the_baseline_only_shrinks`: the base's frozen list still holds `activity_events` (`gate/d20261002-2` already dropped it). The card's "passes without editing the list" holds only for the new-table test.
  - `test_owner_error_language::test_every_error_class_has_turkish`: `'unexpected'`.
  - `test_logging_middleware::test_task_id_defaults_to_none_in_logs`: order-dependent; research activities set `task_id_var` and never reset it.
- `test_host_snapshot_schema` and `test_migration_model_agreement` pass; the table "waits for a release".
- Integration: `140 passed` (3:22). An earlier run had 1 failure, `test_mobile_push::test_the_announcer_fires…`; it also failed 5/5 on the base copy and is flaky.
- Ruff format and check are clean on the changed files.

**Deviations and risks**
- Integration ran on a scratch database, `pagentos_scratch_misheard`, on the dev stack's server (left at 0065 for the inspector). Upgrading the shared `pagentos` to 0065 would break `alembic` for sibling worktrees; it was not touched.
- `DELETE /{id}` answers `{deleted: 1}`; the CONTRACT names no body.
- No CHECK constraints and no foreign keys on the table; in a true race the losing `record()` answers `None` (the row exists). Both are in the ADR.
- An expired hold leaves memory only at the next `hold`/`held`/purge pass, not on a timer.
- ADR: `team/plans/misheard-ledger-store-adr.md`; the `is_request` narrowing is marked "sahip incelemesi bekliyor".
- **Release exception:** this card carries a migration, so its release is asked of the owner (ADR-0214 addendum 9). Nothing is READY_FOR_OWNER.
