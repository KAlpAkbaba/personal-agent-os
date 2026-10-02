## Şu an üzerinde çalışılan
- Task `cycle-auto-integrate` (third round, the inspector's five findings), area `scripts/team/integrate.ps1`, `scripts/lib/TeamIntegrate.ps1`, `scripts/tests/team-integrate.tests.ps1`, `scripts/tests/lib/fake-gate.ps1`, `team/plans/cycle-auto-integrate-adr.md`; machine MAIL, branch `team/d20261001/worker-cycle-auto-integrate`.

## Result
- **sha:** `9347a753654bdc9c5aac88181ecb0c8ac16cc2c7`, pushed; worktree clean. `team/nightly/lead` was merged in first (`69d9aa0f`).
- **Files changed:** 5, all inside the area. Working-copy sha256: lib `e4132e96…`, step `4ac7c7b9…`.
- **Not on the branch:** `-SkipStale` is not on `team/nightly/lead`; it sits on `lead/cycle-rereads-queue` (`3d52902e`). The step keeps the strict write and needs no `-SkipStale`.

## The five findings (each RED first, then GREEN)
RED run before the fix: 12 failed, 1 passed. After: 13/13.
1. **Renames:** the lead-diff check now uses `--no-renames`. A `git mv` out of a task's area into `docs/` is refused with exit 7, staged or committed. RED before: exit 0, "yayın bekliyor".
2. **Failed queue write (API mode):** the red attempt's record keeps the verdict and `applied: false`. The next run writes it without a second gate or lead run and exits 6; the run after that says "held by task-one (returned)". RED before: exit 0, silent wait.
3. **Environment before the lead run:** three runs with a failing `uv` give three exit 10 and 0 lead runs (RED before: 3).
   - What the build scribbles on a tracked file is reset before the lead run.
   - If the wiring changes a project or lock file, the environment is built again; a failure there counts as `lead_failed`.
   - Any other error after a lead run is recorded as `error` and counted; two stop the branch.
4. **Blame:** only failing lines of failing steps name a task (`Get-TeamGateFailingLines`). A PASS line names nobody, and neither does a gate that died or was killed at its cap. RED before: task-two returned from a PASS line.
5. **Caps:** defaults are `-GateMinutes 150` and `-LeadMinutes 30`. A cap of 0, or more than 240 together, is refused before anything starts. Each environment part is capped at 15 minutes. A hung fake gate is killed, reported red, and the lock released.

## Mutations (restored from a backup copy; sha256 identical before and after)
All ten went RED:
- gate's exit code ignored — 2 FAIL (card)
- lead-diff check removed — 2 FAIL (card)
- `--no-renames` removed — 1 FAIL
- unapplied verdict never looked up — 1 FAIL
- environment build skipped before the lead run — 1 FAIL
- post-build reset removed — 1 FAIL
- failing-line filter removed — 2 FAIL
- whole-log fallback restored — 2 FAIL
- cap check disabled — 1 FAIL
- error strike disabled — 1 FAIL

## Final runs (after the last change)
- `team-integrate` 59/0 (was 48)
- `script-syntax` 145/0
- `installer-strictmode` 24/0
- `provision` 13/0
- `team-cycle` 121/0
- Real `-DryRun` on this machine: exit 0, "nothing to integrate".

## Evidence classes
- Decisions and sandboxed flow, including the API-mode stale write: PROVEN_AUTOMATED (fakes).
- NOT_RUN: the real gate in a gate worktree, a real `claude -p` lead run, real `uv sync` / `pnpm install` there, `integrate.ps1` against the Cloud Core, and `Get-TeamGateFailingLines` on a real gate log (its marks come from the tools' known output formats, not a captured log).

## For the lead at merge
- Add `scripts/tests/team-integrate.tests.ps1` to `scripts/quality-gate.ps1` and `.github/workflows/ci.yml`.
- Add the call to `scripts/team/integrate.ps1` after the cycle in `scripts/team/register-nightly.ps1`. It need not pass caps; the defaults are caps.
- Number `team/plans/cycle-auto-integrate-adr.md` (point 9 is this round) into `docs/DECISIONS.md`.
- The cap departs from "no time cap on a run" (owner, 2026-09-30) for this step only, because it holds the team lock; it is recorded in the ADR for you to confirm.

## Open risks
- The blame reader is by inclusion: a failure whose lines carry no mark it knows returns nobody, and the lead has to look.
- A stale write is recovered by the next run, not prevented: the queue is still read before the lock and not again after the gate.
- Gate worktrees are never removed, and `gate-<n>.json` records are per machine.
- The lock is held for the whole gate (owner rule c), and a failed push is not retried; both unchanged.
- A 15-minute cap per environment part is untested against a cold real `uv sync`.
- Scratch logs and mutation backups are in `E:\tmp-integ-bak`, outside the repo; safe to delete.
