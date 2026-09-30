## Şu an üzerinde çalışılan
Task `team-state-on-cloud-core`, area `services/api/app/team` + `scripts/team` + `scripts/lib/Team*.ps1`, machine: the office PC (worker, worktree `pilot-02`). I did not touch HANDOFF.

**sha `04f21bd6822c044dd64ad0e65aa2c0f2306fe79f`**, pushed on `team/pilot-02/worker-team-state-on-cloud-core`, worktree clean. 13 files, all inside the area.

**What was built**
- **Store:** `app/team/models.py` + migration `0063_team_state` (expand-only, one table `(kind,key)`), and `app/team/store.py` with `DbStore` and `FileStore` behind one `TeamStore` contract.
- **Routes:** `routes.py` now holds `/v1/team/approvals` and `/v1/team/queue` (GET queue, PUT `tasks/{id}` with `expected_updated_at` → 409 when stale, GET/POST `lock`, POST `reports`) on one router, so `main.py` needs no change.
- **Approvals:** `approvals.decide` runs on either store. A stale write is refused as 409.
- **Cycle:** `cycle.ps1 -QueueUrl` and `-QueueToken` (a path to a token file). Only tasks that changed are written, each with the `updated_at` it was read at. The lock goes through the API. The report stays a file and is also POSTed as text.
- **Schema:** `app/team/queue.schema.json` is a copy of `team/queue.schema.json`, and a test compares them byte for byte.

**Tests**
- Python: `test_team_state.py` (50) and `test_team_approvals.py` (48, the new store-parametrised section added). 98 passed, ruff clean. `test_migration_*`, `test_pilot01_wiring` and `test_team_queue_schema` also pass (236 in the team/migration subset).
- RED first: before the code, `test_team_state.py` failed at collection on the missing `app.team.store`. The 6 API-mode PowerShell cases failed with 73 file-mode cases green. After: `team-cycle.tests.ps1` 79 passed 0 failed, `provision` 13/13, `script-syntax` 136/0.
- API-mode PowerShell cases run against `scripts/tests/lib/fake-team-api.ps1`, an HTTP listener with the routes' rules. The file mode is unchanged.
- A Python test reads `TeamQueue.ps1` and checks its routes and body fields against `routes.py`, in both directions.

**Mutations (each RED, restored, sha256 checked)**
- **409 precondition removed** (`_check_put` + UPDATE guard in `store.py`): 7 failed. Store sha256 before and after `f8ac7ac9440efcdf95dbe2b0a0f4c501cb4e055a069782daeba3bee3b93ab671`.
- **Staleness 5 on the server side:** 3 failed (5h59 near miss, both stores, and the PowerShell-constant test). Same store sha256 after restore.
- **PowerShell constant 7:** the Python constant test failed, and so did the PS "stale at six hours" case. `TeamQueue.ps1` sha256 before and after `43165c88fc010a7f6811135dd77dfa3d4e1ae7a9b9b9df1199d47778285897b8`.
- **Client stops sending the version it read:** 3 PS API cases failed. Same `TeamQueue.ps1` sha256 after restore.

**Evidence classes**
- PROVEN_AUTOMATED: the store, lock and 409 rules, approvals on both stores, and the cycle in API mode against the fake.
- PROVEN_PROXY: the real FastAPI app over sqlite, served by uvicorn on 127.0.0.1 with a real owner token and the real PowerShell client. Create, second-writer 409, lock acquire, other-machine `held`, release, and a report shown by `GET /v1/team/approvals` all worked. Turkish text round-trips exactly.
- NOT_RUN: Postgres (the migration on a real database, and concurrent-write behaviour beyond the conditional UPDATE), and a full `cycle.ps1` run against the real API. The "dev stack" was not up; the run above is not the dev stack.

**For the lead at merge**
- `services/api/alembic/env.py` needs `import app.team.models  # noqa: F401`, like the `webtask` line. Migration 0063 chains from 0062.
- Set `app.state.team_store = DbStore(app.state.artifacts.session)` in the `main.py` startup, only where the DB is real. Unset means the files, as today.
- **Seed:** the cloud queue starts empty. Push the file queue once with `New-TeamApiStore` and `Save-TeamQueueApi`, or PUT each task. I did not ship a seed script because there was no test for it.
- **Owner token:** the owner writes the owner-session token to a file and passes its path as `-QueueToken`.
- **ADR:** `team/plans/team-state-on-cloud-core-adr.md` (unnumbered; it extends ADR-0214/0217).
- **Nothing to add** to `ledger/vocabulary.py`.

**Open risks**
- In API mode the server's clock decides lock staleness; in file mode it is the machine's.
- The lock's dead-holder check stays on the client (`takeover_dead`).
- The Onay Merkezi's proposal text still comes from `team/` on the machine serving the API.
- Tasks from the database come back in `(created_at, id)` order, not file order.
