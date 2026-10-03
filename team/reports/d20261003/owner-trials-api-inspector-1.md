**owner-trials-api: inspector report** (branch `team/d20261003/worker-owner-trials-api` @ `003c27a4`)

**Pass 1: run**
- **Diff:** the task commit touches 8 files, all inside the area. The two schema copies are byte-identical (`cmp`). The other files in `main...HEAD` (handoff, qualification, d20261002 inspector reports) come from the lead branch's base commits, not from this task.
- **Unit tests (rerun):** `test_team_trials.py` and `test_team_approvals.py` gave 78 passed, 0 failed. ruff check and ruff format --check are clean.
- **PostgreSQL:** I created a fresh database `pagentos_insp_trials` on the dev stack's `pagentos-postgres` and migrated it to head. `tests/integration -k team -m integration` gave 17 passed (including `test_team_trials_postgres.py`), 151 deselected. I dropped the database afterwards. The worker's `pagentos_it_trials` is still there for the lead to drop.
- **My mutation:** I replaced the "every trial is oldu" guard on the reason line with `if True:`. Result: RED, 4 failed (the oldu test and the second-fix-number test, on both stores). I restored it from the backup copy; sha256 is `d6b9cb21…eac32` before and after, and `git status` is clean.
- **Not run:** the full unit suite, the full gate and the team-cycle PS suite (this is a task branch, not the integration branch). The PS queue code does not walk the schema (`TeamQueue.ps1` has no `$ref` handling), so the schema change itself is low-risk there.

**Pass 2: break it**
1. **BLOCKER: one "Olmadı" stops every later cycle.** The fix task that `trials._fix_task` creates has `state: approved` and `area: []`, but no `proposal` field. The card says "a task without an area is not runnable"; the cycle's own code says otherwise. I fed the exact task the route produces to `scripts/lib/TeamQueue.ps1` (real run, PowerShell 5.1):
   - `Test-TeamSplitCandidate` returns False, because a split candidate needs a `proposal`.
   - `Get-TeamNextRole` returns `move -> assigned`. `cycle.ps1:1046` makes that move and a worker is started with no area.
   - `Test-TeamQueue` on the result says `fix-owner-trials-api-1: a task that is being worked on names its file area`. `cycle.ps1:345` then refuses the whole queue ("the queue breaks the protocol; nothing was run") on every following cycle.

   The repo already records this exact trap at `scripts/tests/team-feed.tests.ps1:290-291`: "an approved task with no area and no proposal would be moved to 'assigned' and break the queue for every cycle". The unit test asserts `fix["area"] == []` (`test_team_trials.py:266`), so it passes for the wrong reason: it checks the shape, not that the task cannot be run.
   - **Fix (inside the area):** make the fix task a split candidate. Give it a `proposal` path (the schema has the field at `queue.schema.json:134`) that `cycle.ps1:1096`'s lead split can read. Either the original task's proposal or a written trial record works; the split must have a file to read.
   - **Regression test:** add a test that the created fix task is `Test-TeamSplitCandidate`-true, or at minimum carries a non-empty `proposal`. If no file is written, say in the ADR who writes the proposal file.
2. **Minor:** the ledger event is written before the queue. On a `stale_write`, the ledger records a decision that never landed. This matches `approvals.decide`, so it is a known pattern. List it under open risks; it does not block.
3. **Minor (worker already reports it):** "olmadi" makes two writes that are not atomic (the task, then the fix task).

**Clean:** the listing, the 422/404/409/401 paths, the 500-character limit, the lock behaviour on the database store, the reason line and the state never moving (no PROVEN_REAL claim) all match the contract. There are no secrets or paths in the code and nothing personal goes to the logs beyond the owner's own words in his own ledger.

**Evidence classes**
- PROVEN_AUTOMATED: unit tests (file and database stores), plus real PostgreSQL integration on the dev stack.
- PROVEN_PROXY: the blocker, shown with the cycle's real queue functions.
- NOT_RUN: full gate, team-cycle suite.
- READY_FOR_OWNER: "Oldu" on one of the 38.3–38.5 sentences, after the merge.

RETURN (1: the fix task from "olmadi" has no `proposal` → the cycle moves it to assigned with no area and the queue breaks for every cycle (`cycle.ps1:1046`, `:345`, `team-feed.tests.ps1:290`); make it a split candidate and add a regression test that proves it is not runnable as it stands. 2: list the ledger-before-write orphan under open risks.)
