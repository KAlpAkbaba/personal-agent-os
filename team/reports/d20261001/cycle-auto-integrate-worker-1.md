## Şu an üzerinde çalışılan
`cycle-auto-integrate` — alan: `scripts/team/integrate.ps1`, `scripts/lib/TeamIntegrate.ps1`, `scripts/tests/team-integrate.tests.ps1`, `scripts/tests/lib/fake-gate.ps1`, `team/plans/cycle-auto-integrate-adr.md` — makine MAIL. İş bitti, dal itildi, ağaç temiz.

## Report
- **sha:** `1bf19e8deddbcb753938277f29e63027694683ff` on `team/d20261001/worker-cycle-auto-integrate` (pushed; origin matches).
- **Files changed:** 5, all inside the area (diff against the commit the branch was opened from, `4aa9e7e4`).
- **Tests added:** `scripts/tests/team-integrate.tests.ps1`, 38 cases, covering every acceptance point in the card. **PROVEN_AUTOMATED**, fakes only.
- **RED → GREEN:** with `integrate.ps1` and `TeamIntegrate.ps1` removed, 36 failed / 1 passed; restored by sha256; now 38 passed / 0 failed. I drafted the library and script before the first test run, so the RED was produced by removing them.
- **Mutation RED** (restored from backup copies, sha256 identical before/after):
  - gate's exit code ignored → 2 cases RED (`pass-exit-1` reached main);
  - lead-diff check removed → RED (the refused file reached main);
  - also: two-red stop removed → 2 RED; gate's last word not required → 2 RED; area filter on blame removed → RED.
- **Fast checks:** `script-syntax` 143 checked / 0 failed, `installer-strictmode` 24/0, `provision` (parameter-collision lint) 13/0, existing `team-cycle` 121/0.
- **Real run:** `integrate.ps1 -DryRun` on this worktree's own queue exited 0 with "nothing to integrate" and left main unchanged. **PROVEN_PROXY** for the read path only.
- **NOT_RUN:** the real `quality-gate.ps1` in a gate worktree, a real `claude -p` lead run, real `uv sync` / `pnpm install` there, real `docker info`, API mode against the real Cloud Core. PROVEN_REAL needs the scheduled run.

## The "came back" note
It carried a priority (roadmap feeds the queue, workers must not idle), not a defect list; the branch was empty, so this is the first implementation.

## For the lead at merge
- Add the suite to `scripts/quality-gate.ps1` and `.github/workflows/ci.yml` (about 4 minutes).
- Add the call after the cycle in `scripts/team/register-nightly.ps1`, with the same `-QueueUrl` / `-QueueToken`.
- `scripts/team/cycle.ps1` still writes the gap line "tam kapı ve main'e birleştirme bu betikte yok; lead yapar"; it is stale once the step is scheduled.
- `scripts/tests/team-cycle.tests.ps1`: its encoding test does not list `scripts/lib/TeamIntegrate.ps1`.
- Number `team/plans/cycle-auto-integrate-adr.md` into `docs/DECISIONS.md`.

## Open risks
1. **The lock is held for the whole gate**, as the card asks, so no cycle starts on this machine while a gate runs. That conflicts with addendum 8 ("never paused for the lead's gate"). Releasing it during the gate is safe only in API mode; your decision, written in the ADR.
2. **`-Base` defaults to `main`, but worker branches are opened from `team/nightly/lead`.** The first green gate therefore puts that branch's commits on main along with the tasks. Decide whether that is wanted before scheduling.
3. **Deviations from the card's lead allow-list:** `state/BUILD_STATE.json` is allowed; `team/queue.json` and `team/lock.json` are refused although under `team/`.
4. **The gate worktree is detached**, not on the integration branch (git allows a branch in one worktree only); the branch is fast-forwarded to what was gated.
5. **A refused or failed lead run counts towards the two-attempt stop**, otherwise it would cost a lead run every half hour. The lead clears a stop with `-ClearGateStop`.
6. **A failed push** exits 9 and leaves the tasks `awaiting_release`, because they are on local main; the report says so loudly.
7. **The lead stand-in for wiring scenarios lives inside the test file**; `fake-claude.ps1` (outside my area) only writes splits. The green path does use the real `fake-claude.ps1`.
