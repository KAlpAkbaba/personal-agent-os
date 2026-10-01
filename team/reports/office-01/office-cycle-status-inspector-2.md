**Inspector report: office-cycle-status, second pass** (sha `680c59dd`, worktree clean)

Pass 1 (re-run from clean state):
- `team-cycle.tests.ps1`: 114 passed, 0 failed, about 2m21s. The suite was 85 originally and 112 before the return fixes.
- My own mutations, applied together to `cycle.ps1` (a different pair from the worker's M-B and M-C):
  - M-D removed the `Remove-Item` of `stop.flag`. This turned "safe stop: …flag is gone and the lock is released" RED.
  - M-D also turned "an inspection in flight is still merged when the flag came up during it" RED.
  - M-E removed the status write after `usage_limit` is set to `waiting`. This turned "in API mode the live status goes through PUT…the limit's wait included" RED.
  - Mutated run: 111 passed, 3 failed.
- Restore: from a backup copy, not `git checkout --`. sha256 prefix `4f0ced5bb3b94f56` before and after, and `git status` is clean.
- The worker's M-B (finished run not removed from the live list) and M-C (heartbeat call removed) were not re-run by me. I accept their RED claims because the new tests exist and pass, but I did not verify those two mutations myself.
- Full `quality-gate.ps1`: NOT_RUN. Fast gate beyond this suite: not run.
- No real cycle was run, so no real `status.json` with two workers was seen.

Pass 2 (adversarial):
- Contract: the `Write-CycleStatus` document has exactly the agreed keys (`cycle_id`, `machine`, `pid`, `started_at`, `runs[task,role,started_at]`, `estimated_usd`, `usage_limit{state,resets_at}`, `updated_at`). API mode uses `PUT /v1/team/queue/status` through `Invoke-TeamApi`, and the fake API has the route.
- A failing status write is recorded once under risks and never stops the cycle (tested).
- Area: the return commits touch only `cycle.ps1`, `fake-claude.ps1` and `team-cycle.tests.ps1`. The wider diff against main is the integration base, not this task.
- Stop flag: it is checked before each new batch and during the usage-limit wait. In-flight runs finish, the flag is removed, `finally` clears the live runs, and the lock is released. File mode only, as the card says.
- No secrets or paths in the new code. The status document carries the machine name, pid and task ids only, so there is no KVKK concern.
- The test hook `PAGENTOS_CYCLE_STATUS_TICK_SECONDS` only accepts a whole number from 1 to 9999 and defaults to 120, so it is safe.

Findings that do not block approval:
1. `.gitignore` must get `team/status.json` and `team/stop.flag`. This is outside the area and is the lead's job at merge. Without it `status.json` shows as untracked work at session start.
2. A stale `stop.flag` stops the next cycle immediately. This is accepted, and the report line names the flag.
3. The `waiting` limit state is tested only through the fake API, not in file mode.
4. The heartbeat test takes about 7 s and needs 3 distinct `updated_at` values. It could flake on a very loaded machine, but the bound is lenient.
5. The flag is not removed if the cycle dies by exception, so it persists. That is fail-safe.

**For the lead at merge:** the `.gitignore` additions above, and `team/plans/office-cycle-status-adr.md` is unchanged, so decide whether the test hook needs a mention.

Evidence classes:
- Live status written on start, per run, on finish and at the end: PROVEN_AUTOMATED.
- Finished run leaving the live list: PROVEN_AUTOMATED.
- Heartbeat refresh: PROVEN_AUTOMATED.
- `usage_limit` waiting/ok in API mode: PROVEN_AUTOMATED. In file mode: NOT_RUN.
- Failing PUT not stopping the cycle: PROVEN_AUTOMATED.
- Safe stop: PROVEN_AUTOMATED.
- Real cycle showing two workers: NOT_RUN.

APPROVE
