## Şu an üzerinde çalışılan
- Task: `execution-call-site-routines` (return round, cycle d20261002), roadmap 2b / ADR-0213 row 1
- Area: `services/api/app/routines/{dispatch,target}.py`, the two test files, `team/plans/execution-call-site-routines-adr.md`
- Machine: owner's build PC, worktree `.claude/worktrees/team/d20261002/worker-execution-call-site-routines`

## Report — `execution-call-site-routines`

**sha:** `7a3a11aeeffc4b6fab492c6fa5b66b7512ba5dd5`, on top of `6328a062`; pushed, tree clean, branch level with origin.

**Files changed:** 2, both inside the area: `tests/unit/test_routines_execution_target.py` (+24) and the ADR text (+18/−2). No production code changed: `dispatch.py` is still `b3b46096…` and `target.py` still `13c1e78e…`.

**Return point 1: machine word beside the cloud word**
- New test `test_a_machine_named_beside_the_cloud_word_is_still_refused`, for `("bulutta","ev")` and `("ev","bulutta")`.
- **Probe:** `selection_for` is `None`, `can_run` is `False`, no ledger row.
- **Run:** nothing sent, `no_capable_device`, message carries `forced_target_not_allowed`, one `execution.refused` row with `forced=True`.
- I also added a `("bulutta","ev")` row to the probe == run parametrized test.
- **No RED before the change:** the behaviour already existed in `_spoken`, so the new test was green on first run. The mutation below is its only RED proof.

**Mutation C (`_spoken` returns the first word only):**
- `target.py` went to `f0d5bbc5…`; result **1 failed, 29 passed**. The failure is `…still_refused[targets0]`, the `("bulutta","ev")` case.
- Restored from the backup copy with `cp`; sha256 back to `13c1e78e…`.
- The `("ev","bulutta")` case and the new probe == run row stay green under this mutation. The first has the machine word first; in the second, probe and run share `_spoken` and drift together. Only the dedicated test catches it.

**Return point 2: ADR text**
- New consequence, in bold: a `selected` ledger row is not proof the action ran.
- I read `browser_agent/worker.py:380-388` and `1127-1155` to back it: every operation except `browser.worker_status` needs a `session_id` (else `validation_error`), and every operation other than `session_open` needs a session already open on that worker (else `unknown session`).
- The routine dispatcher sends one operation per firing and opens no session. So a `browser_action` is written `execution.selected target=cloud` and then fails on the worker.
- The ADR now says PROVEN_REAL needs that row and a succeeded command result from the cloud device for the same firing, and that production `browser_action` routines should be counted before release.
- I also added the mixed-word refusal to the forced-target consequence.

**Runs (all waited for)**
- **Unit:** 30 passed (27 + 3 new), PROVEN_AUTOMATED.
- **Integration, dev-stack PostgreSQL:** 2 passed, not skipped, PROVEN_AUTOMATED.
- **Lint:** `ruff check` and `ruff format --check` clean on the four area files.
- **NOT_RUN:** the 874-test neighbour set and the full unit suite (no production byte changed since the inspector ran them), mypy (not in the venv), `quality-gate.ps1`.
- **NOT_RUN:** a real run on the cloud worker. None is enrolled on the dev broker, so "the cloud worker executes it" stays PROVEN_PROXY at best.

**Could not do / left for the lead**
- Inspector items 2, 4 and 5 are unchanged in code and stay in the ADR as lead/owner decisions: the deny-listed-url acceptance line, `media_playback` outside the rule, and no setting to turn this off.
- One sentence in the new ADR paragraph is my inference, not a test: that `session_open` is the only `browser_action` that can succeed on the cloud today. It follows from one operation per firing, and I did not exercise it against a worker.

**Open risks**
- Releasing this breaks any production routine `browser_action` that depends on a machine-side session, while the ledger shows `selected`. The count of such routines is NOT_RUN — I have no production read in this role.
