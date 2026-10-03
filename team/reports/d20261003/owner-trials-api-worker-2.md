**Şu an üzerinde çalışılan:** owner-trials-api (sent back by the inspector), cycle d20261003, area: the 8 files on the card, machine: the owner's PC (build PC).

**owner-trials-api, report after the return.** Both points are fixed. Commit `7897acfad08121c975a27fe4279744f55c490306` is pushed and the worktree is clean.

**Changes:** 4 files, all inside the area: `services/api/app/team/trials.py`, `services/api/tests/unit/test_team_trials.py`, `services/api/tests/integration/test_team_trials_postgres.py` and `team/plans/owner-trials-api-adr.md`.

**Point 1 (blocker):** the "olmadi" fix task now has a `proposal`. It holds the same text as the task's goal, written as prose. It is not a `team/proposals/` path, for two reasons:
- The lead's split card (`TeamRun.ps1:227`) prints the proposal in full.
- On the Cloud Core there is no proposal file for the split run to read.

So nobody has to write a proposal file, and the ADR draft records why.

**Regression test:** `test_the_fix_task_waits_for_the_leads_split_and_is_never_run_without_an_area`, on both stores (file and database).
- It creates the fix task through the route.
- It checks that `area == []`, that `proposal` holds the sentence and the sha, and that the proposal is prose.
- It then runs the cycle's own `scripts/lib/TeamQueue.ps1` in Windows PowerShell 5.1. It asserts `Test-TeamSplitCandidate` returns True and `Get-TeamNextRole` returns Kind `rest`.
- The PostgreSQL test also asserts `proposal == goal`.

**RED before the fix:** 2 failed (both stores), `KeyError: 'proposal'`.

**GREEN after the fix:**
- `test_team_trials.py` and `test_team_approvals.py`: 80 passed (78 before plus the 2 new), no skips, so the PowerShell half really ran.
- PostgreSQL on the dev stack: I made a fresh database `pagentos_it_trials2` and migrated it to head (`0064`). `tests/integration -m integration -k team` gave 17 passed, 151 deselected. I dropped the database afterwards.
- ruff check and ruff format --check are clean (I ran ruff format once on the test file).

**Mutation proof:** I restored the file from a backup copy after each mutation. sha256 was `45062a69…6c0f` before and after.
- **M1:** I removed the `"proposal": goal` line. Result: RED, 2 failed (`KeyError: 'proposal'`).
- **M2:** I changed `FIX_STATE` to `"assigned"` to prove the PowerShell half bites on its own. Result: RED, 2 failed, with `split=False`, `next=run` coming from the real `TeamQueue.ps1`.
- The card's own mutations ("said required" removed, duplicate-decision guard removed) were proven RED in the first round. I did not change that code this time.

**Point 2:** the ADR draft now has an "Open risks" section with two entries:
- **Ledger before write:** the ledger event is recorded before the queue write, as in `approvals.decide`. On a `stale_write` the ledger keeps a `team.trial.*` event for a decision that never landed. The owner gets a 409 and tries again.
- **"olmadi" is two writes:** the task, then the fix task, not in one transaction. A failure between them leaves the verdict recorded with no fix task.

**Evidence classes:**
- **PROVEN_AUTOMATED:** the unit tests on both stores, real PostgreSQL integration, and the cycle's real queue functions run from the test.
- **NOT_RUN:** the full unit suite, the full gate and the team-cycle PowerShell suite (this is a task branch).
- **READY_FOR_OWNER:** "Oldu" on one of the 38.3–38.5 sentences, after the merge.

**Open risks and notes for the lead:**
- The two risks above.
- A task split out of the fix task gets the prose proposal copied in (`TeamQueue.ps1:619`). That is harmless, because it is just text.
- `TeamFeed.ps1:186` only looks at tasks whose reason starts with "sahip onayladı", so the fix task's "alan: lead belirler" does not trigger the roadmap check.
- The lead's merge work from the first report still stands: the vocabulary constants, the inspector role text, the release step, the cycle report reading the new shape in `TeamRun.ps1`, and 38.3–38.5 as the first open trials.
