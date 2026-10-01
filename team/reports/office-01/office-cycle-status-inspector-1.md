The inspection is finished and the verdict is RETURN. It's two untested rules plus an untracked-file risk; the implementation itself did what the card asked. I'm reporting now.

**Inspector report: office-cycle-status** (branch `team/office-01/worker-office-cycle-status`, sha 311574de, tree clean before and after)

**Pass 1: run it**
- I re-ran `team-cycle.tests.ps1` from a clean state: 112 passed, 0 failed, 1m50s. That matches the worker's 85 + 6 new, plus the other cases already in the suite.
- I read the diff of the 7 files. All are inside the area, with no contract drift. The status document's shape matches the card's contract.
- My own mutations (a different set from the worker's), each restored from `/tmp/cycle.bak`. `cycle.ps1` sha256 prefix 8e7be3d185fd4e46 before and after, and `git status` clean:

| Mutation | Result |
|---|---|
| M-A: delete the `Remove-Item` of `stop.flag` | RED: 2 stop tests fail, 110 passed |
| M-B: stop removing a finished run from `liveRuns` | **GREEN: 112 passed, 0 failed** |
| M-C: drop `-OnTick`/`-TickSeconds` from the `Wait-TeamRun` call (the heartbeat) | **GREEN: 112 passed, 0 failed** |

- No real cycle was run, and I started none, so there is no PROVEN_REAL.

**Pass 2: findings**
1. **RETURN: nothing proves a finished run leaves the live list.** M-B survives because the only mid-run snapshot is taken 4 s in, and the final document is cleared in `finally`. If the Remove broke, the Ofis page would keep showing a finished agent as working until the next run started. Needed: a test that reads `status.json` or the API PUT history after run 1 completes and before run 2 starts, and asserts the first run is gone and the estimate has risen.
2. **RETURN: the heartbeat has no test.** The worker added `-OnTick` and the 120 s tick beyond the card. M-C and the limit-wait slicing both pass untested. This is the only thing that stops a run longer than 10 minutes from reading as "no cycle". Needed: a test that makes `$statusTickSeconds` small (a parameter or env hook) and asserts `updated_at` advances during a longer fake run, or a unit test of `Wait-TeamRun -OnTick` that counts ticks.
3. **RETURN (small): `team/status.json` and `team/stop.flag` are not in `.gitignore`.** `lock.json` is not ignored either, but it is deleted at the end of a cycle. `status.json` persists after every cycle, so it leaves an untracked file in `team/`. Session-start treats a dirty tree as unfinished work, and a `git add -A` could commit it. Either ignore the two files, or name the ignore under 'For the lead at merge' (the worker's list omits it).
4. Not a defect, but worth knowing. The stop flag is honoured in API mode too, in the team root. This is wider than the card's "file mode" wording; the worker reported it. A stale flag left from an earlier cycle stops the next cycle at once. That is acceptable under the owner's wording.
5. The worker's own note holds: the `waiting` state is checked only through the API history. File mode has no wait test.
6. I found no secrets, no machine paths and no personal data in the status document. It carries only `machine`, `pid`, task ids and roles. A failed status write adds one risk line and the cycle goes on (tested).

**Evidence classes**
- Live status in file and API mode, and the stop flag: PROVEN_AUTOMATED.
- Heartbeat refresh, and a finished run leaving the list: NOT_RUN. Existing tests pass under mutation, so they prove nothing for these.
- Status shape against the API task: READY_FOR_OWNER. The API and Ofis page tasks must read the same `status.json` shape.

**Verdict:** `RETURN (1: add a test that a finished run leaves the live list before the next starts, RED under M-B; 2: add a heartbeat test, RED when OnTick is removed; 3: gitignore or lead-note for team/status.json and team/stop.flag)`
