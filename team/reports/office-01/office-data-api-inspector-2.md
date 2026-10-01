**Inspector report — office-data-api (second round, HEAD dc17cedd)**

**Pass 1 — run it**
- `test_team_office.py` + `test_team_state.py`: 74 passed, 1 failed. `test_team_office.py` alone is 25/25 green, which matches the worker's count.
- The one failure is `test_every_route_and_body_field_the_powershell_client_uses_is_one_the_server_has`. It reports "a served route the client never calls: GET/PUT /v1/team/queue/status". This branch causes it by adding those routes, and nothing in `scripts/lib` calls them yet. It stays red until the lead exempts the routes or the status-writer task adds the client calls.
- ruff check and ruff format are clean on `app/team` and the office test file.
- My mutations on `office.py` (sha256 `c455fc4f…` before and after, restored from a backup copy):

  | Mutation | Result |
  |---|---|
  | Lock-holder cycle match removed | RED, 1 test |
  | `lock_is_running` check removed | RED, 3 tests |
  | Staleness rule removed | RED, 1 test |
  | 40-line cut removed | RED, 1 test |
- The working tree stayed clean. My throwaway probe files and the stray `.venv` I created are gone.
- Not run: Postgres `DbStore`, the full unit corpus, and `quality-gate.ps1`. The DB path was exercised only on SQLite.

**Pass 2 — break it**
- **PUT validation:**
  - Rejected with 422 as required: `pid: true`, an unknown key, a wrong type, a bad usage state, an unknown role, a missing field.
  - Accepted: integer `estimated_usd` (0 and 21), so a PowerShell integer is fine.
  - Also accepted: a status with five worker runs. `office_view` then fills only worker-1..3, which is correct, and `running_agents` is capped at the seats placed.
- **Live status vs lock:**
  - A held lock with the matching `cycle_id` gives `running=true`, with worker-1 working and the other worker seats waiting.
  - A released lock gives `running=false`. A lock held by a different cycle also gives `running=false`.
  - The same results came out on both the file and DB stores.
- **No store or team root:** answers 200 with the empty office (tested).
- **Auth:** the OFFICE and STATUS routes return 401 without an owner session.
- **Secrets and privacy:** I found no secrets or absolute paths in the added code. `office_view` only passes through fields the queue and status already hold. `GET /queue/status` exposes `pid` and `machine`, but owner session only.
- **Contract:** exactly eight seats in the contract order, and approvals equal those from `/v1/team/approvals`.
- **Second-round fix:** the free-worker-seat index and the `updated_at` rules are in code, in the ADR, and covered by tests. I verified the first and left the mutation proof for the second to the worker's report.
- **Findings, none of them blocking:**
  1. **DB status write:** last-writer-wins, so an old heartbeat could overwrite a newer one if requests reorder. The single-machine lock bounds this.
  2. **Summary lines:** `_task_report` cuts the number of lines but not their length. They pass through as the queue holds them.
  3. **Unknown task in a run:** a run for a task id the queue doesn't know yields `task_title=null`. This is handled and does not crash.
- **Rollback:** the change is additive, with no migration and no new table. It reverts by removing the commits.

**Evidence classes**
- Seat rules, staleness, 40-line cut, 422 and the empty office: PROVEN_AUTOMATED.
- DbStore: PROVEN_PROXY, SQLite only.
- Page and real-cycle screenshot: NOT_RUN. The page and the status writer don't exist yet.

**For the lead at merge**
- Resolve the `test_team_state.py` client-contract failure above. It is outside this task's area.
- Number the ADR in `docs/DECISIONS.md`.
- Tell the status-writer task that `updated_at` must be UTC `Z`.

Code and tests satisfy every acceptance line. The only red check is the shared-file contract test the lead owns.

APPROVE
