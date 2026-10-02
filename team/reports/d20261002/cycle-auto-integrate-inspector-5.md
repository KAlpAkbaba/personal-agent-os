# Inspector report — `cycle-auto-integrate` @ `a1b0363e` (seventh inspection)

The suspended start itself is correct in every run and probe I made, but no test would notice if it stopped being suspended, so I am returning it for the tests only.

**Pass 1 — run it** (tree clean; 5 files, all inside the area; sha256 identical before and after: lib `66cbdf04…`, step `58606a6c…`, tests `102067c6…`)
- `team-integrate` **82 passed / 0 failed**, exit 0, beside my other runs. `script-syntax` 151/0, `installer-strictmode` 24/0, `provision` 13/0.
- Load: the case that failed at the merge plus the two held cases, 8 runs beside the full suite and the mutations: 8 of 8 green (24 cases, 76–125 s each).
- **The card's two mutations** (scratch copies under `E:\tmp-insp7`): gate exit code ignored → RED (1 FAIL); lead-diff check removed → RED (2 FAIL).
- **My mutations of this round:**
  - Job never terminated in `Stop-TeamRunJob` → RED (3 FAIL).
  - `CREATE_SUSPENDED = 0` → **GREEN, 4 of 4 pass**.
  - A refused command not ended → **PASS**, and only after I killed the leaked suspended process by hand; until then the run hung.
  - Prompt written on the step's thread → GREEN.
- **PostgreSQL** (scratch database on the dev stack at `0064`, since dropped; real API with `DbStore`, the step over real HTTP):
  - Refused start: exit 7, no lead call, no gate, no attempt record, both tasks `merged` with the new reason, Turkish intact, lock free, no process left.
  - Next run: exit 0, first attempt green, `awaiting_release` with main's sha, listed at gate `yayin`.
  - Both held cases in API mode: exit 0, the process started, never wrote, was stopped.
  - `team_state.doc` is `jsonb`, so the new reason meets no column width.
- **The new start beside the old one, driven directly:**
  - Same arguments (spaces, quotes, trailing backslash, Turkish), stdin bytes, exit code, working directory and environment.
  - 3 MB on each pipe, a 600 KB prompt, and a `.cmd` tool with a space in its path all behave the same; a missing tool throws the same way.
  - A command that never reads a 2 MB prompt is cut at a 6 s cap in 6.5 s.
  - A grandchild is listed and stopped; no handle growth over 40 runs.
  - All of it also holds when the caller is already inside a job, as a scheduled task is.
- **One real `claude -p`** with the step's own arguments (haiku, 15 s, 0.03 USD), asked to start a process and leave it: held, result parsed ok, Turkish answer intact, the process stopped and never wrote.
- Host-snapshot rule does not apply (no `scripts/cloud`, `infra/docker` or migration).

**Pass 2 — break it**
1. **The held cases pass for the wrong reason.** The stand-in lead reads its input before it starts anything, and the prompt is fed only after the assignment, so the cases pin "not fed early", not "created suspended". My probe shows the difference is real: a command that starts a process before reading input (as the real tool starts hooks and servers) had it running 9.2 s BEFORE the assignment with the flag off — outside the job, alive after the stop, and it wrote. With the real code it started 0.9 s after, was stopped and never wrote.
2. **"Ended as it was created" is asserted nowhere.** The refusal case checks only that the lead logged nothing. A refused command left suspended holds the step's output pipe: the test hangs rather than fails, and in production the scheduled caller would hang too.
3. Minor: the off-thread prompt write (the worker's addition) has no test; my probe covers it.
- Held up: no file outside the area, no release, tag or last-known-good name. The two seams can only delay 10 s or refuse.
- The third mutation on the list (a refused start marked as paid for) changes nothing on the tested path, so it proves nothing either way.

**Evidence classes**
- Decisions and sandboxed flow: PROVEN_AUTOMATED.
- The suspended start (direct, inside a job, real `claude.exe`, one real `claude -p`), API mode on PostgreSQL: PROVEN_PROXY.
- NOT_RUN: a real lead wiring run on the lead's model in a gate worktree, the full `quality-gate.ps1` (not on the integration branch), `team-cycle.tests.ps1` (its files are untouched), the scheduled task, the Cloud Core.

## For the lead at merge
- The worker's wiring list stands; nothing new.
- The job still has no kill-on-close limit: a step killed mid-run leaves the lead's run going (the worker says so).
- 127 `pagentos-integ*` folders are in `%TEMP%`; I did not sort them. Evidence is in `E:\tmp-insp7\evidence` and `E:\tmp-insp7\mutlogs`.

`RETURN (1: make the held cases' stand-in start its process BEFORE it reads its input, so a command created running — CREATE_SUSPENDED dropped, or resumed before the assignment without being fed — is RED; prove it with that mutation; 2: assert in the refusal case that the refused command's process is gone, with a bounded wait so a leaked one FAILS rather than hangs; 3: optional — a case where the command never reads its input and is still cut at -LeadMinutes)`
