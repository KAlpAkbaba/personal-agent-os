**Şu an üzerinde çalışılan:** execution-target-rule, inspector RETURN fix (services/api/app/execution), machine: this PC. I did not touch HANDOFF.

**sha:** `4a07170eb8f5d2f6dc16621914fa2daedc9c6622`, branch `team/pilot-01/worker-execution-target-rule`. The push printed no error, but I did not check the remote. The worktree is clean.

**Files changed:** 2, both inside the area.
- `services/api/app/execution/rule.py`: the wall branch now needs `Target.CLOUD in chain`, plus a comment.
- `services/api/tests/unit/test_execution_target.py`: one new test.

**Test added:** `test_a_wall_is_not_a_cloud_run_when_the_job_can_never_be_in_the_cloud`
- It covers `DESKTOP + cloud_blocker="captcha"` and signed-in research with `auth_wall`.
- Neither may return `ask_owner`, name cloud as target, or have cloud in its chain. Desktop must select the device.
- **RED (before the fix):** `AssertionError: 'ask_owner' != 'ask_owner'`. The decision was `ask_owner`, target `cloud`, chain `(cloud,)`. This is the same defect the inspector found.
- **GREEN (after the fix):** 47 passed in 0.28s.

**Mutation:** I removed `and Target.CLOUD in chain`.
- Result: 1 failed, 46 passed. The new test went RED.
- I restored `rule.py` from a backup copy (`cp`).
- The first 16 hex characters of the sha256 are `b5a8d9ad90e4cb2b` before and after (not the full 64).
- After the restore: 47 passed.

**Checks:** `ruff check` and `ruff format --check` are clean on `app/execution` and the test file.

**Evidence class:** `PROVEN_AUTOMATED` for the guard and its near miss. The rest of the existing 46 tests still pass.

**Not run (`NOT_RUN`):**
- The full `services/api` suite and `quality-gate.ps1`. The module is pure and not wired in yet.
- Any integration, since the broker, ledger and availability probes are PR 2.

**Note for the lead:**
- The venv is the main checkout's. `pytest` imported the worktree tree, as the inspector showed earlier. I did not re-check `app.__file__` this time.
- The ADR text (`team/plans/execution-target-rule-adr.md`) is unchanged. No new decision was made, but the rule table could gain one line: a wall on a job that cannot run in the cloud is ignored, and the normal chain applies.
- Earlier notes still stand:
  - A wall is recorded as `execution.refused` with `ask_owner: true`, and the consumer must read that flag.
  - The rule module was written before its tests in the original pass. This fix followed the RED-first order.
