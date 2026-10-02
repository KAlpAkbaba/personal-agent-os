# Worker report — `cycle-auto-integrate` (fourth round)

## Şu an üzerinde çalışılan
- task `cycle-auto-integrate` · area: `scripts/team/integrate.ps1`, `scripts/lib/TeamIntegrate.ps1`, `scripts/tests/team-integrate.tests.ps1`, `scripts/tests/lib/fake-gate.ps1`, `team/plans/cycle-auto-integrate-adr.md` · machine: this PC, worktree `worker-cycle-auto-integrate`

**sha:** `3fc3d96b3ac7f7099f9b25b88e454e9a07989a24`, pushed; worktree clean. 4 files changed, all inside the area (`fake-gate.ps1` untouched).

**The three findings**
1. **Deleted gate tree.** `Reset-TeamGateWorktree` now removes git's record of that one path (`git worktree remove`, not `prune`) and adds the tree again. An empty leftover folder is reused. A non-empty leftover without `.git` is not deleted: the run stops, naming the folder and saying to delete it by hand.
2. **Main moves during the gate.** New test using the fake gate's hook: exit 11, main holds only the other writer's commit, nothing pushed, both tasks stay `merged` with the reason. The next run merges the new main in and gates again on a different commit.
3. **Nothing ran → no report.** Stopped by the lock or Docker, the step neither writes nor posts the branch's report; in API mode it sends GETs only. The stop sentence goes to one line in `team/reports/integrate-skipped.log` (local, newest 200 kept). The "no write" test now rejects every non-GET request.

**Extra defect found by the mutations (fixed, RED-first):** the tree-equality guard before the merge to main never fired. It compared `""` with `""`, because it asked for a tree through a function that only resolves commits. With only the first guard removed, an ungated merge reached main. New `Test-TeamSameTree` reads the trees and treats an unreadable one as "not the same".

**RED → GREEN** (PROVEN_AUTOMATED)
- Before the fix: 6 of 7 new or changed cases failed (`git worktree add` "missing but already registered", exit 12; report overwritten; `POST /reports` seen). The main-moves case passed on the old code, so its RED comes from mutation.
- `Test-TeamSameTree` case: RED (function missing), then GREEN.
- Final: `team-integrate` 64 passed / 0 failed (was 59); `script-syntax` 145/0; `installer-strictmode` 24/0; `provision` 13/0.

**Mutations** (run on a scratch copy outside the repo; worktree sha256 identical before and after the final run: step `3ac39062…`, lib `3d9f3fe2…`)

| Mutation | Result |
|---|---|
| Worktree record not removed | RED (2 cases) |
| Leftover folder deleted recursively | RED (1) |
| Main-moved check removed | RED — step ends 12 on the tree guard, main untouched |
| Both guards removed | RED — the merge lands on main |
| Report written when nothing ran | RED (5) |
| `Test-TeamSameTree` always true | RED (1) |
| Tree guard alone removed | survives (2/0) — unreachable while the first guard stands |

**ADR text** (`team/plans/cycle-auto-integrate-adr.md`): point 10 for this round, plus "Open decisions for the lead", none built:
1. the lock held for the whole gate, against rule (c);
2. `-Base main` while worker branches open from `team/nightly/lead`;
3. the wiring run may edit the `quality-gate.ps1` it is then judged by;
4. `.claude/worktrees/gate` is the lead's own worktree on this machine.

## For the lead at merge
- Add `scripts/tests/team-integrate.tests.ps1` to `scripts/quality-gate.ps1` and `.github/workflows/ci.yml`. `test_ci_runs_every_powershell_suite` fails on this branch until then.
- Add the call to `scripts/team/integrate.ps1` in `scripts/team/register-nightly.ps1`.
- Number the ADR into `docs/DECISIONS.md`.
- Move your worktree out of `.claude/worktrees/gate` before scheduling.

**NOT_RUN:** `team-cycle` suite this round (nothing it loads changed); the real gate in a gate worktree; a real `claude -p` lead run; API mode on PostgreSQL; the scheduled task.

**Open risks**
- A half-deleted gate folder still ends exit 12 every run until someone deletes it; the message says so, but nothing is written on the task.
- `integrate-skipped.log` is per machine and is not shown in the Onay Merkezi, so a lock or Docker stop is no longer visible there.
- `cycle.ps1` still overwrites its own report in the same case (outside the area).
