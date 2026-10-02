# Inspector report — `cycle-auto-integrate` @ `3fc3d96b` (fourth inspection)

The three findings of the last return are fixed and hold, but the first real lead run exposed two defects: an unchecked file can reach main, and the step does not follow the model policy.

**Pass 1 — run it** (tree clean; 5 files, all inside the area; sha256 identical before and after: step `3ac39062…`, lib `3d9f3fe2…`)
- `team-integrate` 64 passed / 0 failed. `script-syntax` 145/0, `installer-strictmode` 24/0, `provision` 13/0, `team-cycle` 121/0. Real `-DryRun` here: exit 0, "nothing to integrate".
- RED-first holds: this round's tests against the previous code (`9347a753`) give 7 FAIL, 1 PASS (the main-moves case, as the worker said).
- **My mutations** (eight, scratch copies outside the repo, none the worker's): all RED.
  - Gate exit code ignored (1 FAIL); lead-diff check removed (2); skipped line never written (3); `worktree prune` instead of removing the one record (1).
  - A green record trusted after main moved (1); task given the gated sha, not main's (2); skipped log never cut (1); push never made (1).
- **PostgreSQL** (a scratch database of mine on the dev stack, migrated to `0063_team_state`; real API with `DbStore`, `integrate.ps1` over real HTTP, fake gate):
  - Other machine's lock: exit 3, two GETs, no write, the stored report unchanged.
  - Docker down: exit 4, GETs only.
  - Main moved during the gate: exit 11, both tasks `merged` with the reason.
  - Next run: exit 0, second gate, `--no-ff` merge pushed, both tasks `awaiting_release` with the 40-hex sha, listed at gate `yayin`, Turkish text intact in the rows.
- **Real rehearsal in a scratch clone with its own bare origin** (this task merged into `integrate/x1`; real `docker`, `uv`, `pnpm`; a real `claude -p` lead run; a mini gate of the real `test_ci_covers_every_suite.py` plus `script-syntax`):
  - Run 1, lead on `claude-fable-5-1`: environment built in 134 + 18 + 217 s, then the lead answered "You're out of usage credits" in 5 s. Exit 7, recorded as `lead_failed`.
  - Run 2, `-Model claude-opus-5-5`: lead 505 s; it wired `ci.yml`, `quality-gate.ps1`, `register-nightly.ps1`, `DECISIONS.md`, `HANDOFF.md`, `BUILD_STATE.json`; the diff check passed; mini gate green (7 passed; 150 scripts, 0 failed); main merged and pushed; exit 0.
- The host-snapshot rule does not apply (no `scripts/cloud`, `infra/docker` or migration).

**Pass 2 — break it**
1. **A file the diff check never saw reaches main.** The real lead run ended with "tests are running in the background; I will write the report when they finish". A lead stand-in that leaves such a process writing `src/a/task-one.txt` (inside the task's area, named by no report) got it committed and onto main in 2 of 10 runs (delays 400 and 700 ms): exit 0, task `awaiting_release`. The check reads the tree once; `git add -A` then commits whatever is there. In the other 8 runs the gate ran on a dirty tree.
2. **A usage limit is counted as a failed attempt.** This branch's `Read-TeamRunResult` does not recognise "out of usage credits" (`UsageLimited=False`); main's does (`True`), on the same real output. On this branch two such runs stop the branch with the TEAM_PROTOCOL 10 line.
3. **The step does not follow the model policy** (TEAM_PROTOCOL 9a; ADR-0214 addenda 7 and 10 on main). It reads `team/models.json` from the file only and never starts the lead one model down. With the lead's model out of credits — true on this account now — it would exit 7 every half hour and integrate nothing while the cycle runs one model down. No test asserts which model the lead run gets.
- Held up under my other attempts: this machine's own live lock in API mode (exit 3, GETs only); Docker down and `-DryRun` with the gate folder deleted (nothing made); folder deleted between a green gate and the merge (finished without a second gate); a non-empty leftover (exit 12 each run, no lead run; it replaces the branch's report with the error).
- No release, tag, force or last-known-good name; the token is a file path only.

**Evidence classes**
- Decisions and sandboxed flow: PROVEN_AUTOMATED.
- API mode on PostgreSQL, real tools, a real lead run and its wiring: PROVEN_PROXY.
- NOT_RUN: the FULL `quality-gate.ps1` in a gate worktree, the Cloud Core itself, the scheduled task, the Onay Merkezi page in a browser. I did not start the full gate: another run was resetting the shared dev database `pagentos` (my first probe died on `relation "owner_sessions" does not exist`), and a second gate would have collided with it.

## For the lead at merge
- The worker's wiring list stands; the rehearsal's diff is `E:\tmp-insp4\evidence\real-lead-wiring.diff`. The rehearsal lead made `-Integrate` an off-by-default switch in `register-nightly.ps1`.
- Open decision 1: if the lock is released during the gate, the gate and the inspectors' integration runs share the dev database `pagentos` and will reset it under each other.
- `.claude/worktrees/gate` is still your own worktree here; move it before scheduling.
- A lock or Docker stop is now only in the local `integrate-skipped.log`, not in the Onay Merkezi.
- `host-snapshot.json` (19:18Z) is older than the last release.
- 27 `pagentos-integ*` sandbox folders are left in `%TEMP%`; I did not sort mine from earlier runs'. Evidence is in `E:\tmp-insp4\evidence`.

`RETURN (1: commit only what the diff check saw — stop the lead run's process tree before the check and check the COMMITTED diff against the allow-list before the gate, tell the lead in its card to wait for every command it started, with a RED-first test where a process the run leaves behind writes inside a task's area; 2: merge main in and follow the model policy — read the setting where the cycle reads it, start the lead one model down when its model is limited and say so in the report, a usage limit never counted, with a test where the first model answers "out of usage credits" and one asserting the model the lead run is given)`
