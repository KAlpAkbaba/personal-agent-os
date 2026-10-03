**Şu an üzerinde çalışılan:** researcher-every-cycle (d20261003). Area: cycle.ps1, register-nightly.ps1, team-cycle.tests.ps1, the ADR draft. Machine: MAIL.

All four return points are done. The research work is committed and pushed, and the full team-cycle suite passed 213/213 on the final commit.

**sha:** `5f252f126e717a5bd59fb4b1df8a6faf557dd5a3` (pushed, two commits on top of `f38a5e62`; tree clean). 4 files changed, all inside the area: `scripts/team/cycle.ps1`, `scripts/team/register-nightly.ps1`, `scripts/tests/team-cycle.tests.ps1`, `team/plans/researcher-every-cycle-adr.md`.

**1. Researcher at the usage limit: I fixed the cycle, and narrowed the test.** The ADR's point 4 says which and why.
- **Intended rule, kept:** with no model left and `-WaitForUsageLimit:$false`, the cycle stops starting new runs and writes the limit's own stop line. A worker's limit is handled the same way.
- **Product defect, fixed:** `Complete-Research` also wrote "araştırmacı: başarısız: Max kullanım limiti" under the stops, calling the limit a researcher failure. A limited researcher run now writes no failure line and no `research-last.txt` marker, so the next cycle runs it again.
- **Test expectation, corrected:** `task-one` can be `inspecting`. Its worker finished, and the stopped cycle starts no inspection.
- **New assertions:** the limit's own stop line is present, there is no "araştırmacı: başarısız", and `failed_runs` is 0.
- **RED → GREEN:** the new assertion failed against the old cycle (the report listed both stop lines), then passed with the fix. The research slice is 14/14.

**2. Committed:** `6142cdb6` holds the work plus the ADR. `5f252f12` holds the test fix below.

**3. Full `team-cycle.tests.ps1`, run to the end twice:**
- **On `6142cdb6`: 212 passed, 1 failed.** The failure was "researcher and BOTH workers in flight together": the researcher's own status snapshot, taken one second after it started, held only `cycle:researcher`. That one-second check is a stopwatch, not a claim; this PC was loaded by other sessions' gates.
- **Fix in `5f252f12`:** the claim is now asserted from the two workers' snapshots, each of which must list all three runs (`cycle:researcher`, `task-one:worker`, `task-two:worker`). Snapshots are taken 5 s into a run, and the run lengths (researcher 20 s, workers 10 s) are generous so every run is still in flight.
- **On `5f252f12`: 213 passed, 0 failed, exit 0.**
- `installer-strictmode.tests.ps1`: 24 passed, 0 failed. `cycle.ps1` keeps its BOM.

**Mutations** (restored from a backup copy; sha256 `6D71561A…75EE` identical before and after; never `git checkout --`):
- **Default flipped back to off:** RED, 4 of the 5 new cases failed.
- **Proposal post removed** (the call replaced by `$null = 0`; the file still parses with 0 errors): RED in exactly the two post tests, "an idea … WITH its text" and "in API mode the store receives…"; the other 12 cases passed. My first attempt left a broken file, so I discarded it and redid it this way.
- **Limit fix reverted:** RED in "a researcher at the usage limit…".

**4. Leftover processes:**
- **Stopped:** this task's orphaned suite run, pid 7144 with its timeout wrappers 20652/48744, including the many fake-team-api listeners it had started from this worktree.
- **Not stopped, on purpose:** pid 20012 belongs to another worker (`worker-area-widen-role-lines`), and pid 22892 is the lead's quality gate. The inspector was wrong that 20012 is ours.
- No process referencing this worktree is running now, and I deleted my scratch files under %TEMP%.

**Evidence classes**
- Default on, `-NoResearch` / `-Research`, the three runs in flight together, the API post (name and text, once), an empty queue still gets an idea, a researcher at the limit is not a failure: PROVEN_AUTOMATED.
- "A failing post leaves one risk line": covered by the lead's existing tests, which pass in the full run. I added nothing for it.
- Owner sees the researcher "çalışıyor" on /core/office and an idea with its text in the Onay Merkezi: READY_FOR_OWNER.

**Open risks**
- Every hand-started cycle now spends one researcher run unless it gets `-NoResearch` (recorded in the ADR).
- The test harness passes `-NoResearch` by default, so only the five new cases exercise the default.
- The ADR draft does not mention the concurrency test change in `5f252f12`. The lead can add a line when numbering it.
