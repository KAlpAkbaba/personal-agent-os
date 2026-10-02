# Inspector report — `lead-roadmap-feeder` (cycle d20261001, commit `ae5f5bcd`)

**Pass 1 — run it**
- `team-feed.tests.ps1` from a clean tree: **56 passed, 0 failed** (68 s). The worker's number holds.
- Fast gate: `script-syntax` 142/0, `installer-strictmode` 24/0, `provision` 13/0, `team-cycle` 121/0. The full `quality-gate.ps1` was not run (worker branch, not integration).
- Diff: the commit touches exactly the 4 area files; all three scripts carry a BOM; it adds no secret.
- **Real PostgreSQL run (the worker's NOT_RUN):** PROVEN_PROXY.
  - Setup: the real `feed.ps1` in a sandbox repo, the suite's fake model, the real `app.team.routes` + `DbStore` on a throwaway database of the dev stack's PostgreSQL (`team_state`: varchar 16/80/32 + jsonb), and the real `docs/ROADMAP.md`.
  - Result: a 64-character id, a Turkish title, a 4000-character goal, a 104-character row plus note, a `depends_on` card and a `needs_owner` item were all stored with no 4xx/5xx.
  - The lock was taken and released, the report posted, and the idea row committed on the lead's branch.
  - A second run was refused whole, with no second row and no second commit.
- Real Cloud Core, read-only: `feed.ps1 -DryRun -QueueUrl …` reports "14 runnable task(s), the seats are 3 - nothing to cut" and changes nothing.
- Snapshot of the real main checkout: 77 dirty entries hashed in under 1 s.
- **Mutations** (12 of my own, on scratch copies; worktree sha256 identical before and after, `git status` clean): 9 RED, **3 stayed GREEN**.
  - RED: row outside the table, roadmap restore, duplicate title, `created_at` order, lock release, `MaxNew`, one row per idea, owner item as card, Edit exclusion.

**Pass 2 — findings**
1. **The feed report displaces the cycle report in the Onay Merkezi** (proven on the real routes).
   - A locked-out feed (exit 3) still writes a section and POSTs `feed-<date>.md`.
   - `/v1/team/approvals` then returned `cycle_report.file = feed-2026-10-01.md` in place of `d20261001.md`.
   - This happens every 30 minutes whenever a cycle is running and fewer than 3 tasks are runnable; the stop-flag note does the same.
2. **A failed run's feed file is not proven unused** (survivor: `$done.Ok` removed from `$trusted`). The "lead run that fails" case passes only because its fake writes no file. A timed-out run that left valid JSON would be queued under that mutation.
3. **The uncommitted-roadmap guard has no test** (survivor: `$dirty` line removed). Without it, someone's uncommitted `docs/ROADMAP.md` edit is committed under the idea message.
4. **The detached-HEAD guard has no test** (survivor), though the header claims "never from a detached HEAD".
5. Not blocking, for the lead:
   - API mode is not atomic: cards go as one PUT each, so a failure on the second leaves the first queued and no report.
   - A `needs_owner` idea's proposal file exists only on this PC, untracked; the approvals route returned `proposal_text: None` for it.
   - There is no throttle (worker risk 4): an empty or refused answer costs one lead run every 30 minutes.
   - Write stays allowed and is policed by `git status`, so a write to an ignored path such as `.env`, or outside the repo, is not seen.
   - Any edit by the lead's session in the main checkout during a run refuses that run.
   - The wiring must not treat feed's exit 3 as a reason to skip the cycle.

**Evidence classes**
- PROVEN_AUTOMATED: the acceptance list and both card mutations.
- PROVEN_PROXY: the store path on real PostgreSQL through the real routes, and the runnable count on the real queue.
- NOT_RUN: a real `claude -p` lead run; the scheduled-task wiring (the lead's, at merge).
- PROVEN_REAL is not mine to give.

`RETURN (1: do not write or POST a report when nothing was started — lock held, stop flag — so the Onay Merkezi keeps the cycle report, with a test; 2: a case where the run fails or times out AFTER writing a valid feed file queues nothing, RED under the $done.Ok mutation; 3: a case for an uncommitted docs/ROADMAP.md — no idea row asked for, nothing committed, the edit left as found; 4: a case for a detached HEAD)`
