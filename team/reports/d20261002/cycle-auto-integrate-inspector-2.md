# Inspector report — `cycle-auto-integrate` @ `9347a753` (third inspection)

The five findings of the second return are fixed and hold, and nothing ungated reached main in any run; I return it for one real defect, one untested safety guard and one test whose claim is not true.

**Pass 1 — run it** (tree clean; the 5 files are all inside the area; sha256 identical before and after, lib `e4132e96…`, step `4ac7c7b9…`)
- `team-integrate` 59 passed / 0 failed, exit 0. `script-syntax` 145/0, `installer-strictmode` 24/0, `provision` 13/0, `team-cycle` 121/0. Real `-DryRun` here: exit 0, "nothing to integrate".
- **My mutations** (nine, on scratch copies outside the repo; none repeats the worker's ten):
  - RED: ref comparison emptied (1 FAIL), held-branch wait off (2), already-red wait off (2), reset before the merge for main removed (1), rebuild after wiring skipped (1), `applied` never set true (4), API lock not released (2).
  - **Survived, 59/0:** the "main moved while the gate ran" check removed (`integrate.ps1:663`), and again with the tree-equality throw (`:679`) removed too.
- **Real run in a scratch clone with its own bare origin** (real tools, fake lead, no tool path given):
  - `docker`, `uv` and `pnpm` resolved and started. Environment build, cold: api 128 s, browser 16 s, pnpm 175 s — far under the 15-minute cap.
  - The real `quality-gate.ps1 -Fast` ran in `.claude\worktrees\gate\integrate\x1` through `Invoke-TeamGate`: exit 1 after 4949 s (the API unit step alone took 4729 s, with my nine suite runs and a 6-seat cycle loading the machine).
  - `Read-TeamGateLog` read the real log correctly: step "API unit tests", the task with my failing test `returned`, the other `merged`, main unmoved, lock released, exit 6.
  - The same log shows `test_ci_runs_every_powershell_suite` failing on this branch (2 failed, 14119 passed; the other is mine) until the lead adds the suite to `ci.yml`.
- **PostgreSQL (dev stack): the real API with `DbStore`, `integrate.ps1` over real HTTP, fake gate.**
  - Another machine's lock: exit 3, no worktree.
  - Red: exit 6, Turkish reason stored intact, one task `returned`.
  - Main moved during a green gate: exit 11, nothing merged.
  - Next run: exit 0, `--no-ff` merge naming the gated sha, pushed, both tasks `awaiting_release` with the 40-hex sha, and `/v1/team/approvals` lists them at gate `yayin`.
- The host-snapshot rule does not apply (no `scripts/cloud`, `infra/docker` or migration). The fixture (19:18Z) is older than the last release (20:47Z).

**Pass 2 — break it**
1. **A gate worktree folder deleted by hand breaks the branch for good.** Proven on the real code: `git worktree add` fails with "missing but already registered", exit 12 on every run, with no strike, no stop and no reason on the task. The step never removes these trees (about 1.2 GB each, one per cycle id), so someone will delete one.
2. **No test covers main moving while the gate runs.** The behaviour is right (see above), but both guards can be removed with the suite green. This is the path that keeps an ungated merge off main.
3. **A run stopped by the lock or by Docker still writes to the store.** On PostgreSQL the Onay Merkezi's `cycle_report` became the "kilit başka koşuda" stub, and the branch's last red-gate report was overwritten. The test named "no write at all" (`team-integrate.tests.ps1:1415`) leaves `POST /reports` out of its own filter. `cycle.ps1:271` does the same, so the lead may accept the behaviour; the test's claim must then say so.
- No release, tag, force or last-known-good name; the token is a file path only; a second run on this machine is refused while the first is alive.

**Evidence classes**
- Decisions and sandboxed flow: PROVEN_AUTOMATED.
- Tool resolution, environment build, the real fast gate and its log, API mode on PostgreSQL: PROVEN_PROXY.
- NOT_RUN: the FULL gate in a gate worktree (and so a real green log), a real `claude -p` lead run, the Cloud Core itself, the Onay Merkezi page in a browser, the scheduled task.

## For the lead at merge
- **`.claude/worktrees/gate` is already your own worktree here** (`lead/cycle-rereads-queue`). The step's trees would nest inside it: `git add -A` there stages `integrate/<cycle>` as an embedded repository, and removing your worktree triggers finding 1. Move it before scheduling.
- The worker's wiring list stands. Still yours to decide: the lock held for the whole gate against rule (c), `-Base main` while worker branches open from `team/nightly/lead`, and that the wiring run may edit the `quality-gate.ps1` it is then judged by.
- Evidence is in `E:\tmp-insp\evidence`. I deleted my four probe rows from the dev database; two dev owner sessions labelled `inspector-integrate-probe` remain.

`RETURN (1: Reset-TeamGateWorktree must recover a registered-but-missing gate tree — prune or re-register — with a RED-first test; 2: a RED-first test where main moves during the gate (the fake gate's hook exists): nothing merged, tasks stay merged, the next run gates again; 3: do not post or overwrite the branch's report when nothing ran, or make the "no write" test assert what it says)`
