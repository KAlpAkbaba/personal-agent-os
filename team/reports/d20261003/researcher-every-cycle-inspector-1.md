**Inspector report: researcher-every-cycle (d20261003)**

**State of the branch**
- **Nothing is committed.** `git log main..HEAD` shows only the lead's base commits. The three changed files and `team/plans/researcher-every-cycle-adr.md` sit uncommitted in the worktree. The worker reported "running in background, will commit" and stopped there, so the card's "wait for every command, commit, then report" was not followed.
- Two older `team-cycle.tests.ps1` processes are still running on this machine (pids 7144 and 20012, started 02:05 and 02:06). They are probably the worker's leftovers. I did not start them and did not stop them; the lead should.

**Pass 1: what I ran, from this worktree**
- Research, proposal and register slice of `team-cycle.tests.ps1`: **31 passed, 1 failed.**
  - **FAIL** is the worker's own new test: "a researcher at the usage limit is not a failed cycle".
  - The report from that run shows that when the researcher hits the usage limit:
    - the cycle stops: `Durdurulanlar: döngü: Max kullanım limiti … yeniden başlat`;
    - the run is marked as a failure: `arastirmaci: basarisiz: Max kullanım limiti`;
    - `task-one` is outside the expected states (approved, assigned, merged), even though its worker run finished `tamam`.
  - So either the product breaks acceptance item 5 ("the usage-limit rules apply … not a failure") or the test's expectation is wrong. Either way the task's acceptance is RED.
- The other new tests pass: default on, `-NoResearch` turns it off, `-Research` is still accepted, three runs in flight together, the API post happens once with name and text, the empty queue still gets an idea, and `register-nightly` no longer passes `-Research`. The earlier tests that the lead's note says must stay green also pass ("an idea the researcher wrote is in the store WITH its text", "a store that does not keep an idea's text …").
- My own mutation (different from the worker's): I changed `$runResearch` to `([bool]$Research -and -not $NoResearch) -or $ResearchOnly`. Result: **RED**, 4 of the 5 new cases failed. I restored the file from a backup copy; sha256 `41c4c117…c2` matches before and after, and the diff is unchanged.
- `installer-strictmode.tests.ps1`: 24 passed, 0 failed. `cycle.ps1` keeps its BOM.
- Not run by me: the full `team-cycle.tests.ps1` suite (about 208 cases). With a RED acceptance test it would not change the verdict. No PostgreSQL, migration, `infra/` or `scripts/cloud` change is in the diff, so there is no dev-stack run to do.

**Pass 2: trying to break it**
- No files outside the area; no secrets or paths in the code.
- The default flip is small and correct. `-Research` stays a no-op, so the registered nightly task's argument line stays valid.
- Side effect to note: every hand-started cycle now spends one researcher run unless it is given `-NoResearch`. The ADR draft records this.
- The test harness now adds `-NoResearch` by default. The existing tests keep their exact call lists, but none of them exercise the new default any more; only the five new cases (`-DefaultResearch`) do.
- I did not re-prove the card's second mutation ("proposal post removed → RED"). It rests on the lead's already-merged tests, which pass.

**Evidence classes**
- Default on / off, the concurrency snapshot, the API post: PROVEN_AUTOMATED (uncommitted).
- Researcher at the usage limit is not a failed cycle: failing (RED).
- Owner sees the researcher "çalışıyor" on /core/office: READY_FOR_OWNER.

RETURN (1. make "a researcher at the usage limit is not a failed cycle" pass by fixing the cycle or, if the cycle stopping is the intended rule, the test, and say which in the ADR; 2. commit the work, including team/plans/researcher-every-cycle-adr.md; 3. run the full team-cycle.tests.ps1 to the end and report its numbers; 4. stop the leftover team-cycle test processes before reporting)
