**Şu an üzerinde çalışılan:** cycle-lead-run — alan `scripts/team/*`, `scripts/lib/Team*`, ilgili testler — makine: MAIL.

**Report — cycle-lead-run**
- **sha:** `3f11bcf4fd2394fa7246f895ff708e08f8269a19`. The branch is pushed and the worktree is clean. The feature commit is `9c830c646f28427d5e74f4979764c18d395581a3`.
- **Files changed:** 6, all inside the area. They are `cycle.ps1`, `TeamQueue.ps1`, `TeamRun.ps1`, `team-cycle.tests.ps1`, `fake-claude.ps1` and `team/plans/cycle-lead-run-adr.md`.

**What it does**
- After the researcher step, each proposal with an empty area gets one fresh lead run. It applies to `approved`, or `proposed` with a non-empty `roadmap_row`.
- The lead run has Read and Write only. `Bash`, `Edit` and `Agent` are left out of `--allowedTools` and named in `--disallowedTools`.
- The lead writes `team/plans/<cycle>-split-<id>.json`. `Test-TeamSplit` validates it in the script, not the model, and refuses the whole split on any problem.
- A sound split is appended as `approved` tasks and runs in the same cycle. The proposal becomes `done` with reason `bölündü: <ids>`.
- A refused split leaves the proposal where it was and adds a line `bölme reddedildi: <id>: <reasons>` under "Açık riskler". The next cycle asks once more.
- A proposal with an empty `roadmap_row` still goes to `awaiting_owner`.
- `-ResearchOnly` skips the split phase.

**Tests (PROVEN_AUTOMATED, fake model)**
- **RED first:** `-Filter split` gave 1 passed, 20 failed (functions missing).
- **GREEN:** `team-cycle.tests.ps1` passes 106 of 106, including the 21 `split:` cases. `script-syntax` passes 136 of 136 and `provision` 13 of 13.
- **Cycle cases:**
  - A proposal becomes two tasks, both merged in the same cycle.
  - The lead run's tools contain no `Bash`, `Edit` or `Agent`.
  - A rejected split (overlap, shared file, missing field, no file) queues nothing, including its good half, and the proposal stays `approved`.
  - A proposal without a row waits for the owner with no lead run.
  - A second cycle makes no second lead run.
  - A `proposed` proposal with a row is split too.
- **Near misses:** each rule is tested beside its near miss, e.g. `busy` vs `busy2`, 25 vs 26 entries, `docs/HANDOFF.md.bak`, `gestures` vs `hand-gestures`.
- I added a `-Filter` parameter to the test script so the mutation runs re-run only the `split` slice.

**Mutation RED proof:** each row is one mutation of `TeamQueue.ps1` or `cycle.ps1`. I restored from a backup copy each time and compared the sha256 (Get-FileHash, full 64 hex) before and after.

| Mutation | Failing cases |
|---|---|
| overlap check removed | 2 |
| `roadmap_row` condition removed | 3 |
| shared-file check removed | 2 |
| missing-field check removed | 2 |
| area-outside-repository check removed | 1 |
| 25-file limit removed | 1 |
| `main` / hand-gestures branch check removed | 1 |
| hand-gestures area check removed | 1 |
| lead keeps `Bash` and `Edit` | 1 |
| proposal not closed after split | 2 |

- `TeamQueue.ps1` hashes were `AD8AC269843965509D330FD4151EC17F7F8A202C615FB77262B8A4C0BDC904A9` before and after every run. `cycle.ps1` hashes were `7AFEA3D5CF13F3E0217E9DC688FAC38D0FA4039880D2EE94FE60396C10874118` before and after every run. All were identical, and the tree was clean afterwards.

**Not done**
- No real model run. A real lead run against the real model is `NOT_RUN`. The first nightly cycle with a roadmap-serving proposal is its proof.
- I did not run pytest, since the change is PowerShell only.

**Open risks**
- **Stricter than `Test-TeamQueue`:** the overlap check also counts `approved` tasks as taken. A prod queue with many `approved` tasks may refuse splits until they run.
- **Proposal state:** the proposal ends as `done` because the schema has no `split_into` field and I may not edit it. The report shows `(bölündü: ids)` where the sha would be.
- **Lead role file:** `.claude/agents/lead.md` is outside my area and unchanged. The split-only instructions travel in the card.

**For the lead at merge**
- Number `team/plans/cycle-lead-run-adr.md` and move it into `docs/DECISIONS.md`.
- Consider one line in TEAM_PROTOCOL 3a: "the cycle splits roadmap-serving proposals itself."
- Consider a `split_into` schema field later. I touched none of the four shared files.
