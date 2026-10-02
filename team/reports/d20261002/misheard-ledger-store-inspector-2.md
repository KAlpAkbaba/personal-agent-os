## Inspector report: `misheard-ledger-store` (cycle d20261002, pass 2, run by the lead outside the cycle)

**Subject:** `90063e753af1651f0dddc9505f1e761b749a9372` = HEAD = origin; worktree clean before and after; 13 files against merge-base `e6d13b14`, all inside the area. I changed no code. Nothing of mine is running; my two scratch databases are dropped.

**Pass 1: what I ran (PROVEN_AUTOMATED unless said)**
- Unit, the four files (misheard store + routes, `test_bounded_delivery`, `test_health_endpoint`): **119 passed** (24 s). Matches the report.
- Guards (ratchet, host-snapshot, migration-model, owner-error-language, logging-middleware + 38 files matching `wiring|contract|qualification|ci_covers`): **638 passed, 1 failed**. The red is `test_qualification_evidence` row 41.7: present on the base `e6d13b14`, fixed on the lead tip by `baf3667b`; the branch touches nothing under `docs/`. Not this change.
- **PostgreSQL, on a database of my own migrated from EMPTY to head** (`pagentos_scratch_insp2_misheard`): `alembic upgrade head` ran every migration up to 0065, one head, `alembic check` names no drift for the table; `test_misheard_postgres.py` **6 passed**; integration suite **159 passed, 11 xfailed (3:48)**.
- **Merged tree** (`git merge-tree` lead tip `a865b88c` + `90063e75`: clean, tree `ce1f929e`, exported to scratch): four unit files + guards **747 passed, 0 failed**; host-snapshot 10 passed, 1 skipped (no `.git` in the export), the table "waits for a release"; Postgres file 6 passed; one alembic head; `ruff check .` clean (branch and merged).
- **My mutations** (scratch copy, each restored from a backup, sha256 identical: `main.py ce5146d4…`, `service.py ce9ae8e1…`, `routes.py 0046529c…`, migration `31a1efdb…`, `models.py 22fd0926…`): health line removed -> 4 RED; loop failure keeps the exception text -> 3 RED; `interval_s=60` in `main.py` -> 2 RED; owner-session dependency removed -> 1 RED; `answer` accepts an expired row -> 1 RED; `stop()` leaves the heartbeat bound -> 1 RED; **migration `tool` VARCHAR(32) on PostgreSQL -> 3 of 6 RED**; model `TOOL_WIDTH = 128` -> RED on SQLite and PostgreSQL. **Two survived** (finding 3).
- **NOT_RUN by me:** the full unit suite (the lead's gate runs it; the worker's count is 14 777 passed, 1 failed = row 41.7), `quality-gate.ps1`, anything on production.

**Pass 2: break it, on the dev stack's PostgreSQL (scratch database `pagentos_scratch_insp2_probe`)**
- Widths hold: 2001 chars -> 2000; 2001 astral characters -> 2000 chars / 8000 bytes; a 65-character `tool` -> 64; NUL stripped; a lone surrogate -> `None`, transaction usable.
- Race, two sessions on one `(session_id, heard_at)` behind a barrier, 30 rounds: 30/30 one row, one writer got the row, the other `None`, both transactions usable.
- Expired row with no purge: not listed, `answer` is `None`, POST is 404 in Turkish, GET then deletes it. At the exact expiry instant: not listed, purged.
- **The real lifespan on PostgreSQL:** up in 0.55 s, `passes 1, last_removed 1`, health `misheard_purge` ok / `required: false`, task cancelled at shutdown. On the 0064 schema: start is not stopped, `failures 1, last_error "ProgrammingError"`, `record()` answers `None`, transaction usable.
- Loop survives a failed pass (a raising scope, then a backend I terminated mid-pass): `failures 2, passes 2`, row purged, pool clean. Shutdown with a pass in flight: `stop()` 0.0 s, task cancelled; the thread's DELETE completes after the lock is released (as the worker says).
- Logs: a secret-looking sentence through every path incl. real `DataError` / `IntegrityError` / `OperationalError`: **0 hits** in the printed lines and stderr; no request line or 422 body echoes `meant`.

**Findings**
1. **`start()` waits for a database write with no bound** (medium; the worker put this choice to the lead). With an expired row locked by another transaction, the REAL application did not start within 8 s and started the moment the lock was released. No `lock_timeout` anywhere (`create_engine(url, pool_pre_ping=True)`). Every existing loop's `start()` only creates its task (`RetentionSweeper`, `SelfModelRefresher`); the card says "in the shape the existing lifespan loops have". Unreachable today (no writer, no row); reachable once `misheard-relay-wiring` writes and a row is 30 days old, because `record()` purges inside the voice turn's transaction.
2. `record()` "never raises" holds for database faults, not for the caller's types: an unhashable `reason` / `mode` -> `TypeError`, a non-datetime `heard_at` / `now` -> `TypeError` / `AttributeError`, raised before the `try` (transaction untouched). Low.
3. Test gaps (survived mutants): purge `<=` -> `<` (the purge at the exact instant is untested); the `except` around the purge inside `record()` narrowed (no test forces that purge to fail, so "a fault never reaches the caller" is proven for the write only). Low.
4. Malformed JSON on POST `/meaning` answers FastAPI's own `{"detail":[...]}` in English, not `{detail:{code,message}}`; a lone surrogate in `meant` is an unhandled `UnicodeEncodeError` (500). Neither echoes the text. Low.
5. For `misheard-relay-wiring`: the second writer of one key WAITS for the first transaction to end (measured: the whole 1.5 s I held it), then answers `None`; and a caller with its own unflushed failing row gets `None` from `record()` (logged as `misheard_record_failed DataError`) and `PendingRollbackError` at its next statement - the relay must flush before it calls.
6. Health reads `status: ok` for a loop whose every pass fails (only `failures` / `last_error` say so) - the house `LoopHeartbeat` semantics, advisory.
7. Clean: files outside the area, secrets, paths, contract columns and widths (migration = model = `information_schema`), coverage ratchet untouched, rollback (downgrade drops the table; the 0064 schema is survived).

**For the lead at merge**
- Decide finding 1 before any card can create a row: first pass inside the task (as the other loops) or `SET LOCAL lock_timeout` on the pass; either is inside this card's area. Carry findings 2 and 5 into `misheard-relay-wiring`.
- **Host snapshot is older than the last release:** `collected_at` 2026-10-02T13:05:42Z on the lead tip (06:54:28Z on the branch's base) vs `86e6fde9` serving since 20:55 UTC (Stage 45, no migration in it). Collect a new one (`collect-host-snapshot.ps1`).
- Run the full unit suite on the merged tree (NOT_RUN by me). `ruff format --check` flags the two granted test files on the base and the branch alike; the gate runs `ruff check` only.
- The ADR needs its number; `tool` = 64 goes into the other three misheard-* cards; `is_request` is "sahip incelemesi bekliyor"; `docs/HANDOFF.md` was not updated by the worker's commits (outside the area).
- Dev server: the worker's `pagentos_scratch_misheard` (at 0065) is still there, not mine to drop; shared `pagentos` is at 0064, untouched. My probe scripts and outputs are in the session scratchpad (`probe_pg.py`, `probe*-results.txt`, `mutations*.txt`, `guards.txt`, `integration.txt`, `m/`).
- Evidence: everything above PROVEN_AUTOMATED on the dev stack; nothing READY_FOR_OWNER; nothing PROVEN on production.

APPROVE
