# Inspector report — `cycle-seat-pool` @ `9ed17633` (second pass)

**Pass 1 — run it**
- **Full suite, one clean run on the final sha:** `team-cycle.tests.ps1` 208 passed, 0 failed, exit 0 (22:58:53 → 23:31:11). The worker's NOT_RUN is closed. The machine was loaded throughout: CPU 43–84 %, 16–60 `powershell.exe`, 18–20 `claude`, the owner's VR running.
- **Other suites:** `installer-strictmode.tests.ps1` 24 passed, 0 failed; `script-syntax.tests.ps1` 147 scripts, 0 failed.
- **Real script on the real queue:** `cycle.ps1 -DryRun -MaxParallel 4` exit 0, every task listed.
- **My mutations** (different from the worker's 21), both RED and restored from a backup copy with sha256 equal before and after (`d224985e…` / `5f872b6e…`):
  - M2: the version of a run in flight not put back after a re-read. RED in "the store put into work beside a run in flight"; the lead's stop would have been overwritten.
  - M5: a full role seat stops the queue behind it (`continue` → `break`). RED in "seats: each role has its own".
- **My probes** (added to a scratch copy of the suite only, not committed):
  - P1: `-MaxRunsPerTask 1` with two tasks side by side ends `stopped,stopped`, two calls, the right reason.
  - P2: two RETURNs stop task-one while task-two's 12 s worker is in flight; exit 0, both end correctly.
- **Merge check:** merges cleanly onto `team/nightly/lead` (`b2797727`). The diff against `982dc4fb` is six files, all inside the area.
- **Not applicable:** no table, migration, store, container, `scripts/cloud` or `infra/docker` is touched, so there is no Postgres or host-snapshot duty.
- **Not run:** `quality-gate.ps1` and the Python suite (the branch is not on the integration branch).

**Pass 2 — break it**
1. **Timing-shaped tests (risk, not a logic defect).** "the pool: seats are per role…" failed once in 6 runs on the unmodified code, at the heaviest load (five cases took 4 min). The snapshot read `ins-c:inspector,wrk-d:inspector,wrk-f:worker`: the seats were respected, the snapshot was just taken about 10 s late. The four `-PollMilliseconds 6000` cases passed 3 of 3 here (2 slices and the full run); the worker saw 3 fail at about 40 processes. The batch loop had no such pins, so the sensitivity is new with this diff. A gate run beside the cycle and VR can go red on these; a barrier hook in the fake is the fix (follow-up card).
2. **Claim without a committed test.** The ADR says `-MaxRunsPerTask` holds "by the tests that held it". No test, on the base or here, asserts the stop ("bu döngüde N koşu yapıldı"). It does hold (P1), but P1 is not in the suite.
3. **Operational change outside the area.** `tick.ps1` and `register-nightly.ps1` pass only `-MaxParallel`. With 4 that is now up to 4 workers + 2 inspectors + 1 integrator + researcher + lead = 9 `claude` runs on the owner's PC, where it was 4 in total. This is per the card; the lead should write `team/cycle-settings.json` or pass the counts deliberately. The Ofis capacity number and `TEAM_PROTOCOL.md` do not know the new seats.
4. **A cycle that dies now kills every run in flight** (the `finally`). It is deliberate and tested, but one exception while completing a run costs up to nine runs' work.
5. **Same as the base, not a regression:** state moves happen before the stop flag or a cap is read, and a `-CycleMinutes` cap does not end a usage-limit wait (`-MaxHours` and the stop flag do).
6. **Minor:** the "çalışma süresi doldu" line can appear twice when `-MaxHours` passes during a limit wait.
7. **Checked and clean:** merges run in the integration branch's own worktree; `feed.ps1`'s `Wait-TeamRun` call does not use the removed `-OnTick`; a child holding a pipe blocks the pool for at most 30 s; no secrets or paths in the diff; no KVKK surface; rollback is a revert of two commits.

**Evidence classes**
- Pool behaviour, per-role seats, area rule, store re-read, settings file, `-MaxHours`, failed starts: PROVEN_AUTOMATED (fakes; 208/208 on the final sha).
- `-MaxRunsPerTask` and two-RETURNs under the pool: PROVEN_PROXY (my probes, not committed).
- Ofis page showing a worker seat refilled while another worker runs: READY_FOR_OWNER (needs a real cycle on the released script).
- `quality-gate.ps1`, Python suite: NOT_RUN.

**For the lead before the gate:** items 1 and 2 as a follow-up card (barrier hook for the five timing-shaped cases; commit P1), and decide the seat counts in item 3 before release.

APPROVE
