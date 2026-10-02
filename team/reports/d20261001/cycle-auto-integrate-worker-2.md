## Şu an üzerinde çalışılan
`cycle-auto-integrate` (geri dönen iş, denetleyicinin 5 maddesi) · alan: `scripts/team/integrate.ps1`, `scripts/lib/TeamIntegrate.ps1`, `scripts/tests/team-integrate.tests.ps1`, `scripts/tests/lib/fake-gate.ps1`, `team/plans/cycle-auto-integrate-adr.md` · makine: bu PC, dal `team/d20261001/worker-cycle-auto-integrate`.

**sha:** `60fccc552c5d5eccaa753eb8983e5e04c9820333` — pushed, worktree clean. 5 files changed, all inside the area.

**The five points**
1. **Moved ref.** `refs/heads/<Base>`, the integration branch and `refs/remotes/<Remote>/<Base>` are read before and after the lead run. A move gives exit 13, names the ref with both shas in report, console and every task's reason, and stops the branch at once. The step does not put the ref back; the report carries the `git update-ref` line for the lead. Tested with main checked out nowhere, plus the integration ref and a push to origin.
2. **Tools.** `Resolve-TeamToolPath` walks PATH for `.exe` then `.cmd`, never `Get-Command`; a given `.ps1` is refused by name. The sandbox test passes neither `-UvPath` nor `-PnpmPath`, with `pnpm.ps1`/`uv.ps1` first on PATH.
3. **Red on the same sha.** If the last record is red on the tip and main is contained, the step waits: no lock, no lead run, exit 0. A new tip or `-ClearGateStop` gates it again.
4. **Returned task's code.** Decision: a branch goes onto main whole or not at all. While a task with that `integration_branch` is not merged or later, nothing of the branch is gated, and `-ClearGateStop` does not open it. The ADR says why reverting the merge was rejected.
5. **HEAD-ancestry guard.** New test: the lead amends the commit, touching only an allowed file. Expected exit 7 with "HEAD dalın dışına"; without the guard it is exit 11.
- Also fixed (inspector's minor 5): what the gate leaves changed in its worktree is discarded before the merge for main.

**RED → GREEN**
- Before the fix, 9 of the 12 new or rewritten cases failed (4 missing functions; exit 7 instead of 13; exit 10 "ortam kurulamadı"; `awaiting_release` instead of waiting; reasons without the wait words).
- The dirty-gate case went RED (exit 12) once it touched a file the task changed.
- The amended-commit case was green from the start, because the guard already existed; its RED is mutation M4.
- After: `team-integrate` 48 passed / 0 failed, exit 0. `script-syntax` 143/0, `installer-strictmode` 24/0, `provision` 13/0.

**Mutation RED** (restored from a backup copy; sha256 identical before and after each time: lib `bd9f5384…`, step `214199a4…`)
- M1 gate's exit code ignored → 2 FAIL
- M2 lead-diff check removed → 1 FAIL
- M3 ref comparison removed → 1 FAIL
- M4 HEAD-ancestry guard removed → 1 FAIL
- M5 `.ps1` counts as a tool → 2 FAIL
- M6 red commit gated again → 2 FAIL
- M7 returned task no longer holds the branch → 1 FAIL
- M8 gate leftovers not discarded → 1 FAIL
- M9 moved ref is only a strike → 2 FAIL

**Evidence classes**
- Decisions and the sandboxed flow: PROVEN_AUTOMATED (fakes).
- Real PATH on this machine: PROVEN_PROXY. `Get-Command pnpm` answers `pnpm.ps1`; the resolver gives `pnpm.cmd` (11.24.0), `uv.exe` (0.12.7), `docker.exe` (28.3.2), each started through `Invoke-NativeProcess`. `-DryRun` here: exit 0, tree unchanged.
- NOT_RUN: the real gate in a gate worktree, a real `claude -p` lead run, real `uv sync` / `pnpm install` there, API mode against the Cloud Core, `team-cycle` (not re-run; no file of the cycle changed), `Read-TeamGateLog` on a real gate log.

**Open risks**
- One returned task keeps the day's other merged tasks off main until it is fixed and merged again.
- After a moved ref, main may hold an ungated commit until the lead looks; another integration branch going green would push it. Not closed.
- A failed push is still not retried until another branch goes green (inspector's minor 6). Not closed.
- A legitimate move of main by a person during the lead's minutes also stops the branch.

## For the lead at merge
- Add the suite to `scripts/quality-gate.ps1` and `.github/workflows/ci.yml`; add the call in `scripts/team/register-nightly.ps1`; drop the stale "lead yapar" line in `scripts/team/cycle.ps1` and `scripts/tests/team-cycle.tests.ps1`; number `team/plans/cycle-auto-integrate-adr.md` into `docs/DECISIONS.md`.
- New exit code 13 (a ref moved during the lead's run) for the scheduled task's handling.
- Still to decide before scheduling: the lock is held for the whole gate (addendum 8), and `-Base main` while worker branches open from `team/nightly/lead`.
- Feed one real gate log to `Read-TeamGateLog` before the first scheduled run.
