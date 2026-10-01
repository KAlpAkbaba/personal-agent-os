**Inspector report — office-data-api (branch team/office-01/worker-office-data-api, commit f26aa3c9)**

Verdict: RETURN. The route and the pure function mostly hold, but one seat rule loses a returned worker task.

**Pass 1 — run it**
- `test_team_office.py` + `test_team_state.py`: 68 passed, 1 failed. This matches the worker's count.
- The one failure is `test_every_route_and_body_field_the_powershell_client_uses_is_one_the_server_has`. It says "a served route the client never calls: GET and PUT /v1/team/queue/status", because `TeamQueue.ps1` does not call the new routes yet.
- That failure sits outside the area and would put the main gate red at merge. The lead has to resolve it, either by landing the status writer in `TeamQueue.ps1` first or by allow-listing the routes.
- I ran pytest from the worktree and confirmed `app.team.office` imports from the worktree path, not the main checkout.
- Files changed are inside the area: `office.py`, `routes.py`, `store.py`, `test_team_office.py`, the ADR. No contract drift in other files and no secrets or paths in code.
- `ruff` and the full `quality-gate.ps1` were not run by me, and neither was the full unit corpus.
- My mutations (different from the worker's), each restored from a backup copy; sha256 is `514e0566…` before and after:
  - Lock-holder `cycle_id` check removed: RED, 1 test fails.
  - Worker run ordering reversed: RED, 3 tests fail.
  - Owner seat set to "working": RED, 8 tests fail.
- The worker's own mutations (staleness rule, 40-line cut) were not re-run. I did check that the staleness test exists and the code path matches.
- Strict pydantic accepts integer `estimated_usd` (0 and 21 both become float). No 422 for a normal PowerShell writer on that field.

**Pass 2 — break it**
1. **Defect (RETURN):** a returned worker task is dropped while another worker runs.
   - Probe: a worker run on task `b`, plus a task `a` that is `returned` with a worker report.
   - Result: worker-1 "working" on b, worker-2 and worker-3 "waiting", and `a` appears on no seat.
   - Cause: `office.py` indexes `free[index]` by the absolute seat index (worker-2 → 1), not by position among the free seats.
   - The contract says a seat with no live run is "returned" when the newest task of that role is returned or stopped. So worker-2 should show `a`.
   - The existing tests do not cover a mixed working + returned case. Fix it and add a regression test for it, including a RED.
2. **Minor, report or fix:**
   - A future-dated `updated_at` (clock skew) stays live indefinitely. The ten-minute rule only checks `now - written > 10 min`.
   - An `updated_at` without a timezone suffix, as PowerShell can write it, makes `running` false with no error. The status writer must emit `Z` or an offset. Pin this in the contract or the ADR.
3. **Fine:**
   - Garbage runs (None, int, role None) don't crash; `running_agents` is 0.
   - A malformed `summary` (None) becomes `[]`.
   - A run whose task id is unknown still shows "working" with a null title.
   - Eight seats in order every time.
   - The no-store case returns 200 (worker's test passes).
4. **Privacy and CPX32:** the view only passes through what the queue already holds, and it is pure with no extra I/O. `read_office` runs under `asyncio.to_thread`.
5. **Rollback:** no migration and no new table. Status uses a `team_state` row with kind `status`. A revert is clean.
6. **Open risk (worker's note, I agree):** the DB `put_status` is last-writer-wins, and the lock already keeps it to one machine.

**Evidence classes**
- Seat rules, staleness, 40-line cut, 422, 401, FileStore and SQLite DbStore routes: PROVEN_AUTOMATED.
- Postgres DbStore: NOT_RUN.
- PROVEN_REAL, the screenshot during a real cycle: NOT_RUN. The page and the status writer do not exist yet.
- The `quality-gate.ps1` full run: NOT_RUN.

**For the lead at merge:** resolve the PowerShell client contract test, number the ADR into `docs/DECISIONS.md`, and update HANDOFF and BUILD_STATE.

`RETURN (fix the dropped returned-worker-task seat indexing, with a mixed working/returned regression test and RED; add the future-dated and no-timezone `updated_at` decision to the ADR or the code)`
