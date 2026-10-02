# Inspector report — `lead-roadmap-feeder`, second pass (commit `34f95be9`)

**Pass 1 — run it**
- `team-feed.tests.ps1` from a clean tree: **61 passed, 0 failed**. The worker's number holds.
- Fast gate: `script-syntax` 142/0, `installer-strictmode` 24/0, `provision` 13/0, `team-cycle` 121/0. The first `team-cycle` attempt was killed at the tool's 30-minute limit on a loaded machine (79 PASS, 0 FAIL so far); the rerun alone took 5 min 26 s. The full `quality-gate.ps1` was not run (worker branch).
- Diff `ae5f5bcd..34f95be9`: 3 files, all inside the area; `TeamFeed.ps1` is unchanged; the remote ref equals HEAD. The worktree is clean and its sha256 values are the same after my work.
- **Real PostgreSQL (the worker's NOT_RUN on point 1): PROVEN_PROXY.** The real `feed.ps1` ran against the real `app.team.routes` + `DbStore` on a throwaway database with the migrated `team_state` table (varchar 16/80/32 + jsonb).
  - Lock held by the other machine: exit 3, no `report` row, nothing on disk, and `/v1/team/approvals` kept `cycle_report.file = d20261001.md`.
  - Stop flag: exit 0, with the same three results.
  - Lock free, a run started: lock taken and released, `feed-2026-10-01.md` posted.
- Real Cloud Core, read-only: `feed.ps1 -DryRun -QueueUrl …` reports "14 runnable task(s), the seats are 3 - nothing to cut", exit 0.
- **Mutations** (9 of my own, on a scratch copy, full suite each, each restore checked by sha256): 7 RED, 2 GREEN.

| Mutation | Result |
|---|---|
| `roadmap_row` check removed (the card's) | RED, 2 cases |
| Area overlap with a task in work removed, in `Test-TeamSplit` (the card's) | RED, 2 cases |
| Report POSTed but not written on a held lock | RED |
| Edit left allowed when the idea row is blocked | RED, 2 cases |
| `$ideas = @()` removed on the blocked path | RED, 3 cases |
| Report saved when the seats are full | RED |
| Report saved on the stop flag in API mode only | RED |
| Report saved on the API acquire race | GREEN (the worker declared this path untested) |
| `-not $finished.TimedOut` dropped from `$done.Ok` | GREEN (likely equivalent: a killed run has no ok result) |

**Pass 2 — findings, none blocking**
1. **A feed run that did start still displaces the cycle's report.** On the real routes, `/v1/team/approvals` returned `cycle_report.file = feed-2026-10-01.md` after a started run. The fix is in `services/api` (pick the report by name), outside this area, and needs its own card.
2. **A lead process that exits before reading its prompt kills the feeder.** `Start-TeamRun` throws "Boru sonlandı", `feed.ps1` exits 1 with no report line; the lock is released. Seen with my own fake; the code is `TeamRun.ps1`, shared with the cycle and outside the area.
3. **The acquire-race path has no test.** It is three lines read by eye; the repo's fake API cannot lose the race.
4. **The feeder's Turkish stdout arrives garbled** in a captured console ("�alistirilabilir"). The report file is correct; the scheduled task's log will not be.
5. **The deadline case** passed under heavy load here (suite at 144 s instead of 68 s), but it stays an 18-second bound on a process start.
6. **Still open from the first report:** API writes are not atomic; the `needs_owner` proposal file is local only; empty answers are not throttled; writes to ignored paths are not seen.
7. **Wiring is the lead's at merge:** the scheduled-task call (exit 3 and exit 1 must not skip the cycle), and the suite in `quality-gate.ps1` and `ci.yml` — neither names `team-feed` today.

**Evidence classes**
- PROVEN_AUTOMATED: the card's acceptance list, both card mutations, and the four return points.
- PROVEN_PROXY: "no run, no report" and the lock on real PostgreSQL through the real routes; the runnable count on the real queue.
- NOT_RUN: a real `claude -p` lead run; the scheduled-task wiring; the API acquire race; the full `quality-gate.ps1`.
- READY_FOR_OWNER: the first night the queue runs low.

`APPROVE`
