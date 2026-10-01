**Inspector report: cycle-lead-run** (branch `team/cycle-2026-10-01/worker-cycle-lead-run`, commits 9c830c64 and 3f11bcf4)

**Pass 1: run**
- `team-cycle.tests.ps1`: 106 passed, 0 failed (1m23s). All 8 `split:` cases pass. They cover a two-task split that runs in the same cycle, a `proposed` proposal with a row, and a proposal with no row (no lead run).
- They also cover rejection of an overlap, a shared file, a missing field and a missing file, each taken whole, with the proposal left in place. A second cycle makes no second split.
- `script-syntax.tests.ps1`: 136 checked, 0 failed. `provision.tests.ps1`: 13 passed, 0 failed.
- I did not run pytest or the full `quality-gate.ps1`. The change is PowerShell only and this is not the integration branch.
- **My mutations (different from the worker's):**
  - The intra-split overlap loop is disabled → RED (1 failing case, "two tasks of one split that share an area…").
  - Prefix matching loosened to a plain `StartsWith` with no `/` → RED (1 failing case, the in-work neighbour case).
  - For each, `TeamQueue.ps1` was restored from a backup copy and the full 64-hex sha256 matched (`ad8ac269…904a9`). `git status` was clean afterwards, and I never used `git checkout --`.
- The worker's claimed `cycle.ps1` hash (`7AFEA3D5…`) differs from the current `6993c6ea…`. That is explained by the last commit, 3f11bcf4, which only added docs to `cycle.ps1`. `TeamQueue.ps1` matches the worker's hash.

**Pass 2: adversarial**
- **Area:** only the six files in the task card plus the ADR changed; `git diff HEAD~2 HEAD` shows no other file. `docs/HANDOFF.md`, `docs/DECISIONS.md`, `state/BUILD_STATE.json` and `docs/THIRD_PARTY_COMPONENTS.md` are untouched. There is no new dependency.
- **Lead tools:** the role file grants `Read, Grep, Glob, Bash, Edit, Write, Agent`. The lead run drops `Bash`, `Edit` and `Agent` via `--disallowedTools` and `--allowedTools`, so it has `Read, Grep, Glob, Write`. A test asserts this.
- **Finding, medium:** `Write` is not scoped to the one split file. The run uses `--permission-mode acceptEdits` with `Write` granted, so only the prompt keeps the lead from writing elsewhere. The script cannot prevent a stray write. Its cwd is the main repo root, so a stray write would land in the working tree.
- **Finding, low:** `Test-TeamSplit` checks areas syntactically only. It does not check that the paths exist or exclude other sensitive roots such as `.claude/` or `scripts/team/`. Shared files, `main`, hand-gestures, `..` and absolute paths are covered.
- **Finding, low:** the overlap check also counts `approved` tasks as taken. This is stricter than `Test-TeamQueue`, so a long `approved` backlog can refuse splits until those tasks run. The worker disclosed this.
- **Finding, low:** the proposal ends as `done` with `reason: "bölündü: …"`, because the schema has no `split_into` field. That is acceptable, and the report shows the ids where the sha would be.
- **Privacy/secrets:** no secrets or absolute paths in the new code. The lead's raw output goes to the cycle's ignored report folder, as for other roles.
- **Rollback:** a refused split changes nothing. A queued split is ordinary `approved` tasks that can be stopped with a state edit.

**Evidence class:** PROVEN_AUTOMATED (fake model only).
**Not run:** a real lead run against the real model. The first nightly cycle with a roadmap-serving proposal is its proof, so that stays `NOT_RUN` / `READY_FOR_OWNER`.

**For the lead at merge:**
- Number `team/plans/cycle-lead-run-adr.md` and move it into `docs/DECISIONS.md`.
- Add one line to TEAM_PROTOCOL 3a.
- Consider a follow-up to scope the split run's `Write`, for example by checking that the tree is clean after the run, and to add a `split_into` schema field.

APPROVE
