# Inspector report — postgres-coverage-debt @ `58313530` (second pass)

**Pass 1 — run it (dev stack PostgreSQL, `pagentos-postgres`, dialect asserted `postgresql`)**
- The two new files: **18 passed, 11 xfailed** (16 s). All 11 strict xfails carry the PostgreSQL error the report quotes (defects 1–10; defect 6 is two cases).
- Ratchet: `tests/unit/test_postgres_coverage_ratchet.py` **3 passed**. The baseline lost exactly the eight tables (51 → 43).
- Diff `main...HEAD` touches 4 files, all inside the area. No app code, migration, `scripts/cloud` or `infra/docker` change, so the host snapshot check does not apply.
- `ruff check .` clean; `ruff format --check` clean on the 3 test files.
- Full integration suite, three runs with the branch:
  - Run 1: **4 failed, 128 passed, 11 xfailed**. I only captured the last traceback (`test_session_lifecycle_on_postgres`): `FATAL: sorry, too many clients already`. The other three (`test_m6_selfhealing_full_story`, two in `test_voice_persistence.py`) I did not read.
  - Runs 2 and 3: **132 passed, 11 xfailed**.
  - Without the two new files: **114 passed** (two runs).
- Connection peaks against `max_connections=300` (idle baseline 29):
  - Full suite with the branch: 291.
  - Full suite without it: 289.
  - The two new files alone: 111, about 82 held from 22 `owner_client` apps never disposed.
  - So the suite already runs at the limit without this branch, and the run-1 failure looks like that existing lack of headroom; the branch adds 2 at the peak.
- My mutations (different from the worker's; each restored from a backup, sha256 identical): **5 RED of 5**.
  - `add_evidence` drops `source_ref`: evidence test RED.
  - `PATCH` `change_reason` bound 512 → 600: RED with `varying(512)` on `INSERT INTO memory_versions`.
  - `snooze_alarm` ignores `resume_at`: RED (`07:31` != `07:40`).
  - `resume_routine` keeps `pause_reason`: RED.
  - `entities` put back in the baseline: `test_the_baseline_only_shrinks` RED.
- After all runs: 0 routines, 0 alarms, 0 `pgcov` entities or memories, 29 connections, `git status` clean.

**Pass 2 — break it**
1. **ADR rule 3 is false (medium, proven by a probe on PostgreSQL).** It says a `now` in 2001 "can only make the test's own routines due". `check_schedule_due` compares weekday and wall clock only, never the year.
   - Probe: a foreign armed routine (`weekdays=[2]`, 07:30 Europe/Istanbul) evaluated at the cloud-ring test's instant (2001-09-12 07:30:20) came back `('triggered', '2001-09-12')`.
   - That test uses the production `ActionDispatcher` and `WakeAlarmRunner` on the real session factory. A developer's own weekday 07:30 alarm in the dev database would be driven to PLAYING with a 2001 `triggered_at`, and the test would still pass.
   - 0 armed routines today, so nothing was harmed. This is worse than the documented `device_idle` limit, and the ADR hands the sentence to the next slice as a rule.
2. **Connection headroom (low for this branch, real for the gate).** The 22 undisposed apps hold about 82 connections; the suite-wide 289/300 is not this task's, and the lead should queue it.
3. Possible defect 11, by reading only (NOT_RUN): `experience/routes.py:227` builds `key=f"experience.lesson.{row.source_ref}"`. `source_ref` is VARCHAR(256), so the key can reach 274 in a VARCHAR(256) column — the same class as defect 8.
4. `memory_evidence` with `kind="episodic_occurrence"` (procedure detection) and `app.memory.store` are not exercised; the service path is, so acceptance holds.
5. No secrets or machine paths in the diff, no SQLite, no hand-written INSERTs. The teardown deletes are the only direct SQL and are documented.

**Evidence classes**
- Eight tables through their production writers on real PostgreSQL; defects 1–10; ratchet: **PROVEN_AUTOMATED**.
- Integration suite green: **PROVEN_AUTOMATED** on 2 of 3 runs, with the one failure attributed above.
- Finding 1: **PROVEN_AUTOMATED** by a scratch probe that is not in the tree.
- **NOT_RUN** by me: the full unit suite and `quality-gate.ps1 -Fast` (the diff has no app code and I ran the one unit file touched); the NUL probes on the fields the ADR lists as not run; finding 3.

**Verdict**

`RETURN (1. Correct ADR rule 3: a 2001 instant does fire any armed schedule routine whose weekday and time match; state the limit truthfully. 2. Make the cloud-ring test refuse to run the production dispatcher over foreign rows: before each `evaluate_due`/`tick`, assert no armed routine exists outside the test's own, failing loudly rather than firing them; the same guard serves the condition-trigger test. 3. Optional, in area: dispose each `owner_client` app's engines, or share one client per module, to release the ~82 held connections.)`
