## Şu an üzerinde çalışılan
Görev `postgres-coverage-debt` (worker, döngü d20261001) · alan: `services/api/tests/integration/test_memory_tables_postgres.py`, `test_routines_alarms_postgres.py`, `tests/unit/test_postgres_coverage_ratchet.py`, `team/plans/postgres-coverage-debt-adr.md` · makine: sahibin Windows geliştirme makinesi, dev stack PostgreSQL `127.0.0.1:15432`, şema `0063_team_state`. Durum: bitti, itildi.

## Report
- **sha:** `645cd72d723436458e25c0a34b3590fcabec9603` (pushed, remote equals local; tree clean). Two commits: `88a790e5` tests + baseline, `645cd72d` ADR text.
- **Files changed:** 4, all inside the area (2 new integration files, the ratchet, the ADR text).
- **Result:** all eight tables are exercised on real PostgreSQL; `UNEXERCISED_BASELINE` lost exactly those eight (51 → 43) and the ratchet is green (3 passed).
- **Tests added:** 28 (12 memory, 16 routines/alarms): 20 pass, 8 strict-xfail. Each file asserts `engine.dialect.name == "postgresql"`; no SQLite.
- **Full run:** `pytest tests/integration -m integration` → 131 passed, 8 xfailed, exit 0, 153 s. `ruff check` clean. No test rows left in the DB (counted).

**Production writer per table**
- `memory_versions`: `service.remember_explicit`, `service.edit_memory`, `PATCH /v1/memory/{id}`
- `memory_evidence`: `service.record_observation`
- `memory_audit_events`: `lifecycle.record_audit` via `record_observation` / `remember_explicit` / `lifecycle.reindex_missing`, `POST /v1/memory/remember`
- `entities`: `service.create_entity`, `graph.sync_from_events`, `POST /v1/memory/entities`
- `entity_edges`: `service.create_edge`, `graph.sync_from_events`, `POST /v1/memory/edges`
- `routines`: `routines_service.create_routine` / `pause_routine` / `resume_routine` / `cancel_routine`, `POST /v1/routines`, voice `routine.create` / `routine.pause`
- `routine_firings`: `routines_service.evaluate_due`
- `wake_alarms`: `alarms_service.create_alarm`, `reconcile_local_fired`, `snooze_alarm`, `cancel_alarm`, `POST /v1/alarms`

**RED → GREEN**
- First run: 9 failed, 16 passed, each failure a PostgreSQL error. One of the nine was my own test passing a 300-character reason no surface can produce; I shortened it. The other eight are the defects below.
- Ratchet before the baseline edit: RED, "remove them from UNEXERCISED_BASELINE", naming exactly the eight.

**Mutation RED** (sha256 before/after identical, restored from backup copies)
- 512 → 513, 200 → 201 (routine name), 200 → 201 (alarm label), and `wake_alarms` put back in the baseline → 4 failed: `value too long for type character varying(512)`, `(200)`, `(200)`, and the ratchet's own message.

**Defects found** (nothing fixed; each is `xfail(strict=True, raises=…)`)
1. `POST /v1/routines`: `source` has no max length → 500, `StringDataRightTruncation: value too long for type character varying(32)`.
2. Voice `routine.create`, 201-character name → `… character varying(200)`.
3. Voice `routine.pause` (`routine.cancel` shares the code path), 501-character reason → `… character varying(500)`.
4. `evaluate_due`: joined `skip_reason` over 500 characters → `… character varying(500)` on the `routine_firings` insert. The exception leaves the tick, so routines after it in that pass are not evaluated.
5. `cancel_alarm` with a reason of 189+ characters (REST allows 200; measured 188 passes, 189 raises): the `alarm.cleaned_up` ledger key overflows `character varying(256)`, then `PendingRollbackError` escapes. The alarm is already CANCELLED, the caller gets a 500, and the cleanup row is never written.
6. `POST /v1/memory/entities` with U+0000 in `name` → `PostgreSQL text fields cannot contain NUL (0x00) bytes`; inside `attrs` → `UntranslatableCharacter: unsupported Unicode escape sequence` (two xfails).
7. `POST /v1/routines` with U+0000 in `name` → the same NUL error.

**Evidence class:** PROVEN_AUTOMATED on real PostgreSQL (dev stack) for everything above.

**NOT_RUN**
- Unit suite beyond the ratchet file (no production code changed).
- Whether `app.routines.service._record_ledger` has defect 5's shape: read, not run.
- U+0000 on tables outside this slice.

**Open risks**
- Ledger rows and memory audit rows written by these tests stay in the dev DB; both tables are append-only, and naming the ledger table to clean it would have falsely paid its debt.
- Unverified, by reading only: after a device-local snooze is reconciled, the alarm's earlier one-shot routine appears to stay armed.
- ADR text is at `team/plans/postgres-coverage-debt-adr.md` for the lead to number.
