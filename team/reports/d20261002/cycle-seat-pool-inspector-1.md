## Inspector report — cycle-seat-pool @ `ab3d3b9c` (cycle d20261002)

**Pass 1 — run it**
- Full `team-cycle.tests.ps1` from the clean worktree: **203 passed, 0 failed**, matching the report.
- Fast checks: script-syntax 147/0, installer-strictmode 24/0, provision 13/0, team-feed 65/0.
- The diff against the branch's base `982dc4fb` is 6 files, all inside the area. The HANDOFF and QUALIFICATION lines in `main...HEAD` come from the base commit, not the worker.
- Headline acceptance checked independently with my own harness (`E:\tmp_insp\break2.ps1`), same fake, `-MaxParallel 2`, task-two's worker 8 s:
  - Pool: task-three's worker snapshot names `task-two:worker` and `task-one:inspector` beside it.
  - Batch loop (`982dc4fb`): the same snapshot names `task-three:worker` alone. RED-before holds.
- Four mutations of my own, on a scratch copy and restored from backup with sha256 OK each time:
  - A candidate chosen in this refill does not hold its files: RED (unit). The integration overlap test stayed green; the queue's own rule covers that case.
  - The task version of a run in flight is not kept across a re-read: RED (`merged/merged/merged/merged` instead of `…/stopped/…`).
  - Runs in flight do not count against seats: RED, 3 tests.
  - A run past its deadline is not over: RED, but only through the harness's 300 s hang guard, not a behavioural assertion.
- The four pinned store-outage tests, run without the `-PollMilliseconds 6000` pin: 2 pass, 2 fail. Both failures are on trace shape only: the pool sees the stop before merging, so there is no merge to name. The store's word stands in both. The pin is legitimate.
- No table, migration, store, broker or container is touched, so the PostgreSQL rule and the host snapshot do not apply. The status contract is unchanged: `StatusRequest.runs` has no length cap and the Ofis view already lists several runs per role seat.
- Worktree left clean, HEAD unchanged, the three production scripts' sha256 unchanged.

**Pass 2 — break it**
1. **Regression, reproduced: a failed start ends the cycle with runnable work left.**
   - Setup: three `assigned` tasks, `-MaxParallel 1`, task-one's worktree cannot be made (a file stands where its folder goes).
   - Pool: `calls: (none)`, states `task-one=stopped, task-two=assigned, task-three=assigned`, exit 0 after 2.5 s, and the report gives no reason for ending.
   - Batch loop (`982dc4fb`), same sandbox: task-one stopped, task-two and task-three worked, inspected and **merged** (4 runs).
   - Cause: `Start-PoolRun` returns `$false`, so `Started` is 0 with an empty pool and `Moved` false, and the main loop breaks (`cycle.ps1` ~1449–1471). The stop is a state change the loop does not count.
   - This breaks acceptance (5) "ends when nothing is in flight and nothing is runnable" and item (4) "everything the batch loop guaranteed still holds". No test covers it.
   - Reproduce: `E:\tmp_insp\break1.ps1 -Case failstart` (add `-Ref 982dc4fb` for the batch loop).
2. **Risk for the lead, not a defect of this diff: more concurrent runs on the owner's PC.**
   - `-MaxParallel` is now worker seats only, and `tick.ps1` / `register-nightly.ps1` still pass the old number.
   - A registered `-MaxParallel 4` becomes up to 9 runs (4 workers, 2 inspectors, 1 integrator, researcher, lead).
   - Decide the number, or write `team/cycle-settings.json`, before the scheduler runs this.
3. **Risk: merges now run beside live workers.** `Merge-TeamBranch` treats any non-success of `git merge` as a conflict and sends the task back (existing behaviour). With other git processes active, a transient failure would be recorded as "entegrasyon dalında çakışma". Not reproduced with the fakes.
4. **Smaller notes.**
   - A researcher or lead run that fails to start throws uncaught, and the `finally` then kills every run in flight.
   - The ADR is unnumbered in `team/plans/`; the lead must move it into `docs/DECISIONS.md`.
   - `-MaxParallel` is not mentioned in `docs/TEAM_PROTOCOL.md`, so the per-role seats are undocumented there.

No secrets, absolute paths or personal data in the diff. Rollback is a revert of one commit, with no schema or state format change.

**Evidence classes**
- Pool behaviour, seats, area rule, re-read, settings, `-MaxHours`, limit wait: PROVEN_AUTOMATED (fakes).
- Ofis page showing a worker seat refilled while another worker of the same cycle runs: NOT_RUN (needs a real cycle; READY_FOR_OWNER after the fix).
- Full `quality-gate.ps1` and the Python suite: NOT_RUN (task branch, no Python touched).

RETURN (1. a start that fails with nothing else in flight must not end the cycle: count the stop as a change and refill again, as the batch loop did, with a RED-first test — three assigned tasks, one worker seat, the first worktree blocked, the other two end merged; 2. on the way, catch a failed researcher/lead start instead of letting it kill the runs in flight, or say in the ADR why not)
