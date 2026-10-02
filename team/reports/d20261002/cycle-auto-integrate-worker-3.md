## Şu an üzerinde çalışılan
`cycle-auto-integrate` (5. tur), alan: `scripts/team/integrate.ps1`, `scripts/lib/TeamIntegrate.ps1`, `scripts/tests/team-integrate.tests.ps1`, `scripts/tests/lib/fake-gate.ps1`, `team/plans/cycle-auto-integrate-adr.md`; makine MAIL. Bitti: commit edildi, itildi, ağaç temiz, arka planda çalışan bir şey yok.

**sha:** `44014c8894d59f06921bc6c5844224b43f2a7a2b` on `team/d20261001/worker-cycle-auto-integrate`, pushed. `team/nightly/lead` was merged in first (`a9628de2`, no conflict). 5 files changed, all inside the area.

**Finding 1 — only what the diff check saw is committed**
- The lead run is held in a Windows job object from its start. When it ends, or is cut at its cap, everything it left going is stopped and waited for before anything is read.
- What the run changed is committed first and judged second: the allow-list is held against the committed diff.
- The tree is put back on that commit before the gate.
- The merge card now tells the lead to wait for every command it started and start nothing in the background.

**Finding 2 — the model policy**
- The lead's model is read where the cycle reads it: the store's setting in API mode, else `team/models.json`, else the defaults.
- `team/limits.json` is read and never written.
- A limited model starts no run and builds no environment. A run that answers "out of usage credits" is restarted at once one model down, and the report says `model düşürüldü`.
- A usage limit is never an attempt, so it always exits 7 and never 8.
- The step never waits for a reset; `integrate.ps1` has no sleep at all.

**Tests**
- RED first on the old code: of the 15 new or extended tests, 14 FAIL and 1 PASS. The pass is the no-sleep guard, which the old code already satisfied.
  - The left-behind writer did write, and its file reached main with exit 0.
  - The gate ran on a tree holding `?? src/a/late.txt`.
  - The lead run was given no model.
- A small `git.exe` shim puts the late write at a fixed moment, so the defect reproduces every run instead of 2 in 10.
- A 14th new test was added after the RED run: a lead run cut at its cap. It is not RED-first (the old code also passes it); it guards the rewritten wait.
- GREEN on the committed tree: `team-integrate` 78 passed / 0 failed (64 before).
- Other suites: `script-syntax` 151/0, `installer-strictmode` 24/0, `provision` 13/0, `team-cycle` 184/0. A real `-DryRun` here exits 0 with "nothing to integrate".

**Mutations** — 14, in a scratch copy outside the repo; every one RED. The worktree's step, library and fake gate have identical sha256 before and after.
- The two the card requires: gate exit code ignored; lead-diff check removed.
- Finding 1: process tree not stopped; check reads the lead's own HEAD instead of the commit; tree not put back before the gate; lead cap ignored; card line removed.
- Finding 2: never lowered; no retry one model down; store's setting not read; limits file ignored; a sleep added; limit counted as an attempt; `-Model` not validated.

**Evidence class**
- Decisions and the sandboxed flow: PROVEN_AUTOMATED. The process-tree stop is exercised with real Windows processes, including one started through WMI that no job holds.
- NOT_RUN: a real `claude -p` lead run, the full `quality-gate.ps1` in a gate worktree, API mode on PostgreSQL, the scheduled task, the Onay Merkezi page.

**Open risks**
- A writer no job holds that writes while the gate runs makes the gate judge a tree that is not the commit. Main still gets exactly the commit.
- A limit the step meets is not remembered across steps; the next one spends one short run finding it again unless a cycle wrote `team/limits.json`.
- A file name git quotes (non-ASCII) is refused by the allow-list even under `docs/`.

## For the lead at merge
- Add `scripts/tests/team-integrate.tests.ps1` to `scripts/quality-gate.ps1` and `.github/workflows/ci.yml`; add the call to `scripts/team/register-nightly.ps1`. The suite takes about 25 minutes.
- Number `team/plans/cycle-auto-integrate-adr.md` into `docs/DECISIONS.md`. Point 11 is this round; open decisions 5 (the gate and the inspectors share the dev database) and 6 (a lock or Docker stop is not in the Onay Merkezi) are written, not built.
- `.claude/agents/lead.md` could carry the same "wait for every command you started" line; it is outside my area.
- `.claude/worktrees/gate` is still your own worktree; move it before scheduling.
- Evidence is in `E:\tmp-w5` (`red-first.log`, `final.log`, `mutlogs\`).
