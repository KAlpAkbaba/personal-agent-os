## Şu an üzerinde çalışılan
- Task: `cycle-auto-integrate` (round 7, test changes only). Area: `scripts/tests/team-integrate.tests.ps1`, `team/plans/cycle-auto-integrate-adr.md`. Machine: the owner's build PC, worktree `worker-cycle-auto-integrate`.

## Report: cycle-auto-integrate @ `b113867f1629b2c3a8aa4ae465a11baa422d719f` (pushed; origin matches; tree clean)

**What was on the branch:** last night's run never committed anything. Its round-7 edits were still uncommitted in this worktree, and they covered all three of inspector-5's points. I ran them, mutation-tested them and committed them. Two files changed since `4e4ff72c`, both inside the area. The library and the step script did not change (sha256 `66cbdf04…` and `58606a6c…`).

**Changes:**
1. **Point 1:** the stand-in lead now starts the process it leaves behind before it reads its input, the way the real tool starts hooks. The two held cases now test that the lead's command is created suspended, not just that it is fed late.
2. **Point 2:** the refusal case checks that the refused command's process is gone, waiting at most 15 s. The step's output goes to a file and the step has a time limit, so a leftover suspended command makes the test fail instead of hanging it.
3. **Point 3 (optional, done):** a new case where the command never reads a 2 MB prompt; it is still cut off at `-LeadMinutes`.

The ADR text in `team/plans/cycle-auto-integrate-adr.md` now has a "seventh round" note.

**Tests:** the four new or changed cases passed before any mutation (4/4, 99 s). The full `team-integrate` suite was run in the foreground in 12 slices: **83 passed, 0 failed**, no case run twice. `script-syntax` 153/0, `installer-strictmode` 24/0.

**Mutations:** each was restored from a backup copy, with sha256 matching after every restore.

| Mutation | Result |
|---|---|
| `CREATE_SUSPENDED` dropped | held cases **RED 2/2** (the process wrote); before this round they stayed green |
| Command resumed before the assignment, unfed | held cases **RED 2/2** |
| Refused command not ended (`TerminateJobObject`/`TerminateProcess` removed) | refusal case **RED in 28 s** ("pids: 47860"); fails, does not hang |
| Prompt written on the step's own thread (`Feed(...).Wait()`) | no-read case **RED** (step took 128 s against the 0.2 min cap) |
| Card: gate exit code ignored | **RED** (`pass-exit-1` got code 0 where 6 was expected) |
| Card: lead-diff check removed | **RED 3 of 4** refusal cases (the fourth tests a write after the commit, which this check does not cover) |

**Evidence class:**
- Test and mutation claims above: PROVEN_AUTOMATED (sandbox and fakes).
- Not run (NOT_RUN): the real `quality-gate.ps1`, a real lead run, the scheduled task, `team-cycle.tests.ps1` (its files were not touched).
- The PROVEN_REAL claim (a task finished at noon is on main by the afternoon) cannot be shown until this is merged and scheduled.

**Open risks:**
- Still not fixed: the job has no kill-on-close limit, so if the step itself is killed mid-run, the lead's run keeps going.
- Scratch files from this run are in `E:\tmp-w8` (backups, slice filters, outputs) and can be deleted.

## For the lead at merge
- Unchanged from before: the earlier wiring list stands.
  - Add `scripts/tests/team-integrate.tests.ps1` to `quality-gate.ps1` and `ci.yml`.
  - Add the call to `scripts/team/integrate.ps1` to the scheduled task after the cycle.
  - Number the ADR from `team/plans/cycle-auto-integrate-adr.md` into `docs/DECISIONS.md`.
- The suite now takes about 23 minutes on this machine (sum of the 12 slices), so give it a long enough cap in the gate.
