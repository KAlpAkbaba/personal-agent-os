**Worker report, team-state-on-cloud-core (return fix)**

**Sha:** `09344f829599da5468014b1066d5fd5ffd1dacdf`, pushed on `team/pilot-02/worker-team-state-on-cloud-core`, worktree clean.

**The return point:** `scripts/tests/lib/fake-team-api.ps1` was outside the area.
- I moved it with `git mv` to `scripts/team/fake-team-api.ps1`, which the area covers. The task says "Work only inside the area"; the area lists only `scripts/tests/lib/fake-claude.ps1` under `scripts/tests/lib`.
- I updated the path in `scripts/tests/team-cycle.tests.ps1` (one line, `-w` diff) and in the header comment of the moved file. The ADR file needed no change.
- I ran a diff of the branch against `main` and filtered out every path in the area. Nothing is left over, so all 13 files are inside the area.
- The move changed no behaviour. The fake is only started by the test as a child process.

**One thing to know:** my first sed replaced the path only in the header comment. With the path still wrong in the test, `team-cycle.tests.ps1` showed 74 passed, 5 failed, all "the fake team API did not start". I corrected the path and re-ran.

**Evidence** (PROVEN_AUTOMATED, run after the move):
- `team-cycle.tests.ps1`: 79 passed, 0 failed. This includes the API-mode cases and the file-mode cases.
- `script-syntax.tests.ps1`: 136 checked, 0 failed.
- The Python side is untouched by this fix. The earlier report's results (98 passed in `test_team_state.py` and `test_team_approvals.py`, ruff clean) stand. I did not re-run them.
- The earlier mutation results and full sha256 values also stand, since the store and client files are unchanged. Store sha256 `f8ac7ac9440efcdf95dbe2b0a0f4c501cb4e055a069782daeba3bee3b93ab671`, `TeamQueue.ps1` sha256 `43165c88fc010a7f6811135dd77dfa3d4e1ae7a9b9b9df1199d47778285897b8`.
- NOT_RUN: Postgres, and a full `cycle.ps1` run against the real API. The dev stack was not up, and I did not run it this time.

**For the lead at merge** (unchanged from the last report):
- Add `import app.team.models  # noqa: F401` to `services/api/alembic/env.py`. Migration 0063 chains from 0062.
- In the `main.py` startup, set `app.state.team_store = DbStore(app.state.artifacts.session)` only where the DB is real. Unset means the files, as today.
- Seed the cloud queue once from the file queue with `New-TeamApiStore` and `Save-TeamQueueApi`. It starts empty and I shipped no seed script.
- The owner writes the owner-session token to a file and passes its path as `-QueueToken`.
- The ADR is `team/plans/team-state-on-cloud-core-adr.md`, unnumbered.
- Nothing to add to `app/ledger/vocabulary.py`.

**Open risks** (unchanged):
- Lock staleness follows the server's clock in API mode and the machine's clock in file mode.
- The dead-holder check stays on the client.
- The proposal text in the Onay Merkezi still comes from `team/` on the machine serving the API.
- Tasks from the database come back in `(created_at, id)` order, not file order.
