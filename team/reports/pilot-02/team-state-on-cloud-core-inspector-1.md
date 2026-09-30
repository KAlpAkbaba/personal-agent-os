**Inspector report: team-state-on-cloud-core** (branch tip `09344f82`, worktree clean before and after)

**Pass 1: run it**
- I re-ran `test_team_state.py` and `test_team_approvals.py` from `services/api` with the main checkout's venv. `app.__file__` resolves to the worktree. Result: **98 passed**. Ruff is clean on `app/team`, both test files and migration 0063.
- The two team suites plus `test_team_queue_schema.py` give **213 passed**.
- `scripts/tests/team-cycle.tests.ps1`: **79 passed, 0 failed**. This covers the API-mode cases, the wrong-token and missing-token-file cases, and the file-mode cases.
- `script-syntax.tests.ps1`: 136 checked, 0 failed.
- The diff against `main` has 13 files, all inside the area. The `fake-team-api.ps1` move fixed the earlier return.
- I ran three mutations of my own, each restored by sha256:

| # | Mutation | Result |
|---|---|---|
| A | `routes.py` maps a stale write to 400 instead of 409 | RED, 2 failed |
| B | `TeamQueue.ps1` staleness 6 → 8, Python side untouched | RED, 1 failed (the test that reads the PowerShell source) |
| C | `store.py` `_check_put` skips the `updated_at` compare | RED, 3 failed |

- After each mutation I restored the file from a backup copy and confirmed the hash:
  - `routes.py`: `b0ffe93a1f7f85e0c129355b4213268a1b9eeb0360b24bc79e15af55ae1446f8`
  - `store.py`: `f8ac7ac9440efcdf95dbe2b0a0f4c501cb4e055a069782daeba3bee3b93ab671`
  - `TeamQueue.ps1`: `43165c88fc010a7f6811135dd77dfa3d4e1ae7a9b9b9df1199d47778285897b8`
- The B result confirms the acceptance criterion that the staleness constant is tested across the two languages.

**Pass 2: break it**
- **Contract drift:** `app/team/queue.schema.json` is byte-identical to `team/queue.schema.json`. A test also compares them, so future drift goes RED.
- **Migration:** 0063 chains from 0062, which is the tip on `main`. It adds one table, and its downgrade drops it. It changes nothing that already exists.
- **Secrets:** `cycle.ps1` takes `-QueueToken` as a file path only. The client reads the file, trims it and refuses an empty or missing file. Tests pin all three cases and the wrong-token case. Nothing in the diff writes a token to logs or to `Write-Host`.
- **Privacy and audit:** the cycle's `Write-Host` messages carry only the lock holder and the error text. I found no KVKK-relevant data in the audit trail or logs.
- **Auth:** all `/v1/team/queue` routes sit under `require_owner_session`.
- **Store choice:** without `app.state.team_store`, the routes fall back to the file store. The home PC without the API keeps working.

**Findings, none blocking**
1. **Postgres NOT_RUN.** The `DbStore` compare-and-swap (`UPDATE … WHERE updated_at = expected`, with an `IntegrityError` path on create) was exercised only on the tests' engine, presumably SQLite. Real concurrent-write behaviour on Postgres is unproven.
2. **Full `cycle.ps1` against the real API on the dev stack was NOT_RUN.** The API-mode proof is against `scripts/team/fake-team-api.ps1`, which is PROVEN_PROXY at most.
3. **`app/team/models.py` is not imported in `alembic/env.py`.** The lead must add that import. Autogenerate would otherwise miss the table.
4. **The worker's four open risks stand:**
   - Lock staleness follows the server's clock in API mode and the machine's clock in file mode.
   - The dead-holder check stays on the client.
   - The proposal text comes from the API host's `team/`.
   - Task order from the database differs from file order.

   None is a safety defect. The staleness clock difference is the least comfortable one. It should be settled by an ADR line at merge.
5. **Seeding.** The cloud queue starts empty and no seed script ships. Until the lead seeds it, the first approval on the cloud shows nothing.

**Evidence classes**
- PROVEN_AUTOMATED: Python store, routes, approvals parity (database and file), the 409 rule, the lock rule and the staleness-constant cross-check. PowerShell file mode and API mode.
- PROVEN_PROXY: API mode against the fake HTTP listener.
- NOT_RUN: Postgres, the real dev stack, and a full end-to-end cycle.
- READY_FOR_OWNER: the token file, and seeding the cloud queue.

**For the lead at merge** (confirmed from the worker's list)
- Add the `alembic/env.py` model import.
- Wire `app.state.team_store = DbStore(...)` in `main.py`, only where the database is real.
- Seed the queue once.
- Add the ADR number.

APPROVE
