**Report: office-cycle-status**

**Şu an üzerinde çalışılan:** office-cycle-status, area scripts/team + scripts/lib + tests, machine office-01. The lead writes it into HANDOFF at merge; I did not touch HANDOFF.

- **sha:** 311574de514ba2d49ff70d495566c9491486e7e5, pushed on `team/office-01/worker-office-cycle-status`. The worktree is clean.
- **Files changed:** 7, all inside the area. They are `cycle.ps1`, `TeamRun.ps1`, `TeamQueue.ps1`, `fake-team-api.ps1`, `team-cycle.tests.ps1`, `fake-claude.ps1` and `team/plans/office-cycle-status-adr.md`.

**What was built**
- **Status document.** `cycle.ps1` writes the contract document to `team/status.json` in file mode. In API mode it uses PUT `/v1/team/queue/status` through the new `Save-TeamStatusApi`.
- **When it writes.** It writes after the lock, after each run starts, after each run completes (estimate updated), and on the usage-limit states `waiting`, `stopped` and `ok`. It also writes in `finally`, so the end state has no runs even if the cycle crashes.
- **Heartbeat.** Beyond the card, `Wait-TeamRun` takes `-OnTick`/`-TickSeconds`, and the limit wait sleeps in slices. The status refreshes every 120 s. Without it, any run longer than 10 minutes would read as "no cycle" under your staleness rule.
- **Failed status write.** It adds one risk line, "canlı durum yazılamadı", and the cycle carries on.
- **Safe stop.** `Test-StopRequested` runs inside `Test-CapReached`, so it covers the researcher, the lead split and the batch. The stop line is exactly as specified. The flag is removed and the lock released, and it exits 0.
- **Stop details.** An approved inspection that was already in flight is still merged. A finished worker stays `inspecting`. The flag is checked in API mode too, in the team root, which is wider than the card's "file mode" wording.
- **Fakes.** `fake-team-api.ps1` gains the status route, a history of every PUT (`/__state` returns `status` and `statuses`) and `-FailStatus`. `fake-claude.ps1` gains env hooks: a snapshot of `status.json` taken 4 s into a run, and creating the stop flag from a chosen role's run.

**Tests: 6 new cases, 0 failures in the full suite (112 passed)**
- **RED first:** before the implementation the new cases failed with a missing snapshot, a missing `status.json`, and runs that kept going past the flag (`worker,inspector,worker,inspector` instead of `worker`).
- **GREEN:**
  - Live status: two workers in flight are named by task and role, and the final document has no runs and an estimate equal to the report's.
  - Limit states: `stopped` with the reset time, and `ok` after a wait.
  - API mode: the PUT documents include `waiting` with `resets_at` and a last document with no runs.
  - A failing PUT does not stop the cycle.
  - The stop flag, with the inspection merge variant.
- **Mutation 1:** removing the status write after run start turned the in-flight test and the API test RED. The limit-states test did not go RED, because it only reads the final file.
- **Mutation 2:** removing the stop check turned both stop tests RED.
- **Restore:** `cycle.ps1` sha256 prefix 8e7be3d185fd4e46 was identical before and after, restored from a backup copy.
- **Other suites:** `script-syntax.tests.ps1` passed (139 scripts, 0 failed) and `provision.tests.ps1` passed (13).

**Evidence class:** PROVEN_AUTOMATED. PROVEN_REAL is NOT_RUN: no real cycle was started, so `status.json` with two real workers is not yet seen.

**Open risks**
- The 10-minute staleness and "cycle holds the lock" judgement belongs to the API side. A cycle killed hard leaves a status that goes stale only through that rule.
- `usage_limit` stays `stopped` in the final document after a limit stop. That is intended.
- The `waiting` state is observed only through the API history, because in the tests the reset time is already past and there is no real wait window to read from outside.

**For the lead at merge**
- Move `team/plans/office-cycle-status-adr.md` into `docs/DECISIONS.md` with a number.
- The API task must add PUT/GET `/v1/team/queue/status` and the office route, following the status shape in the card.
- `TEAM_PROTOCOL` should name `team/stop.flag` and `team/status.json`.
