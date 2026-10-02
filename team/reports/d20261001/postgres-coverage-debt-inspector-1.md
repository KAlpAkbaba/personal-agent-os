**Inspector report: postgres-coverage-debt @ `645cd72d`** (dev stack: PostgreSQL 16.15 on 127.0.0.1:15432, schema `0063_team_state`)

**Pass 1: run it**
- **Two new files + ratchet, clean state:** 20 passed, 8 xfailed, matching the report (17 integration + 3 ratchet).
- **Full `pytest tests/integration -m integration`:** 131 passed, 8 xfailed, exit 0, 158.8 s, matching the report.
- **`--runxfail` on the two files:** 8 failed, 17 passed. Each failure is the quoted PostgreSQL error: `varying(32)`, `(200)`, `(500)` twice, `(256)` then `PendingRollbackError`, NUL twice, `UntranslatableCharacter`.
- **Defect 5 threshold re-measured:** a 188-character reason passes, 189 raises.
- **Rows left behind:** none. Routines, alarms and `pgcov` entities and embeddings count 0 after every run; only append-only audit rows stay, as reported.
- **Lint and diff:** `ruff check` and `ruff format --check` are clean on the three files. The worker's two commits touch only the four area files, and the baseline lost exactly the eight tables (51 → 43).
- **My mutations** (restored from backups, sha256 OK, tree clean) gave 5 RED out of 5:
  - `POST /v1/routines` name limit 200 → 2000: `varying(200)`.
  - Memory `key` limit 256 → 2560: `varying(256)`.
  - Edge `relation` limit 64 → 640: `varying(64)`.
  - Alarm cancel `reason` limit 200 → 2000: `PendingRollbackError`.
  - `entities` put back in the baseline: the ratchet's own message.
- **NOT_RUN:**
  - `quality-gate.ps1 -Fast` and the unit suite beyond the ratchet file; no production code changed.
  - mypy: `No module named mypy` in the venv.

**Pass 2: break it**
1. **Missed in-slice defect, measured on PostgreSQL.** `service.remember_explicit` with a 257-character key raises `StringDataRightTruncation: value too long for type character varying(256)`. By reading only, voice `memory.remember` passes the model's `key` unbounded (`tools_memory.py:339`). No test in the tree covers it.
2. **The NUL class was not probed on two in-slice surfaces, and both answer 500.** Measured: `POST /v1/memory/remember` with U+0000 in `text` (which `memory_versions.text` stores), and `POST /v1/alarms` with U+0000 in `label` (`wake_alarms`). Both raise `PostgreSQL text fields cannot contain NUL (0x00) bytes`. The report's NOT_RUN line "U+0000 on tables outside this slice" understates this.
3. **A claim without evidence in the tree.** The docstring at `test_routines_alarms_postgres.py:669` says `terminal_reason` at its full VARCHAR(200) "is the last alarm test's". That test is strict-xfail and raises before its assertion, so no passing test stores more than a short reason there; 188 is the longest that works.
4. **`wake_alarms` coverage gap (not a failure).** Only the device-local path (`reconcile_local_fired`) reaches PostgreSQL. The cloud ring path (`evaluate_due` → `wake_alarm` action → ARMED/FIRING → sequence) is not exercised, so `armed_at`, `last_firing_id`, `media_session_id` VARCHAR(128), `greeting_due_at` and `greeted_at` are only ever asserted NULL.
5. **Low:** `evaluate_due(now=2001, device_idle_s=900)` runs over every armed routine in the dev database. A developer's own condition-trigger routine would get a 2001 firing; there are 0 armed routines today.
6. **Low:** the 65-character timezone 422 cannot tell `max_length=64` from an invalid zone.
7. **Clean:** no secrets or paths, no SQLite (the dialect is asserted `postgresql` in both files), no contract drift. Rollback is reverting two commits.

**Evidence classes**
- Eight tables written through production functions on real PostgreSQL: PROVEN_AUTOMATED.
- Defects 1–7 as reported: PROVEN_AUTOMATED.
- Findings 1–2 above: PROVEN_AUTOMATED by my scratch probe, which is deleted and not in the tree.
- Voice reachability of finding 1: NOT_RUN (read only).
- Cloud ring path of `wake_alarms`: NOT_RUN.

The card's acceptance criteria are met as written. I am returning it because the slice's purpose is finding what PostgreSQL refuses in these eight tables, and three in-slice refusals have no test in the tree.

`RETURN (1: add a strict-xfail test for a key over 256 characters through voice memory.remember, or remember_explicit if the tool cannot be driven, quoting varying(256), and list it under Defects found and in the ADR; 2: add strict-xfail NUL tests for POST /v1/memory/remember text and POST /v1/alarms label, and correct the NOT_RUN line; 3: prove terminal_reason at the longest passing length (188) in a green test and fix the line-669 docstring; 4: either exercise the cloud ring path's wake_alarms columns or record them in the ADR as unpaid debt for the lead to queue)`
