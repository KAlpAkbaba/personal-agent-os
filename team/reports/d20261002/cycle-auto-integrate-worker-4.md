## Şu an üzerinde çalışılan
- **Task:** cycle-auto-integrate (sixth round, the lead's return at the merge). **Area:** `scripts/team/integrate.ps1`, `scripts/lib/TeamIntegrate.ps1`, `scripts/tests/team-integrate.tests.ps1`, `team/plans/cycle-auto-integrate-adr.md`. **Machine:** MAIL.
- **Branch:** `team/d20261001/worker-cycle-auto-integrate`, pushed, tree clean. **sha:** `a1b0363e711ada4def5af564b3c4f060a1094a16`.
- `team/nightly/lead` (main `f60e02e4`) is merged in as `5e8dcca0`; it merged without a conflict here, `docs/HANDOFF.md` included.

## What was found and changed
- **The lead's reading is proven as a mechanism.** With the step held between its two old lines, the old code fails every time, both ways: a process started in between is in no job, and a run that already ended cannot be assigned, so nothing is stopped. I cannot prove this was the cause of the 1-of-78 at the merge, only that hitting the window produces that failure.
- **Chosen: `CreateProcess` with `CREATE_SUSPENDED`, assign, resume** (`Start-TeamHeldRun`). The command has not run one instruction when it is assigned. A launcher was not chosen: it adds a second process and a hand-over protocol for stdin, both pipes and the exit code. The ADR text says this.
- **No job, no run.** If the command cannot be put into a job (or resumed), it is ended as created and no lead run starts. Exit 7, no attempt record, tasks stay `merged` with "lead koşusu başlatılmadı - süreç ağacı tutulamadı (…)", and the report says so. `Start-TeamRunJob` is gone.
- **Two seams**, both only delay or refuse: `PAGENTOS_TEAM_INTEGRATE_HOLD_BEFORE_JOB` (waits for a file, 10 s at most, writes `<file>.passed`) and `PAGENTOS_TEAM_INTEGRATE_JOB_FAILS`.
- One addition beyond the card: the prompt is written off the step's thread, so a command that never reads stdin is still cut at `-LeadMinutes`.

## Tests added (4), RED → GREEN
- Process started before the assignment, run still alive; the same with the run already ended; a run that cannot be held is refused before the command starts; a text guard (the step has one way to start the run).
- **RED on the old code plus the seam:** 3 of 3 failed in a clean run (the process wrote; exit 0 where 7 was expected). A second run's tail and a third overlapped my edits, so I do not count them. The text guard was added after the fix and has no RED run.
- **GREEN:** 3 of 3; full suite `team-integrate` **82 passed / 0 failed**, exit 0, 34 min.
- A first full run was killed by the harness's 90-minute limit at 51 PASS / 0 FAIL while the load proof ran beside it; the 82/0 is the second run.

## Mutation RED (scratch copies under `E:\tmp-w7`; the worktree was never mutated)
- A: command resumed and fed before the assignment → both held cases FAIL.
- B: a failed assignment ignored → the refusal case FAILS (exit 0, expected 7).
- sha256 of the worktree files, identical before and after: `TeamIntegrate.ps1` 66cbdf04…5850, `integrate.ps1` 58606a6c…102e, `team-integrate.tests.ps1` 102067c6…f17f.

## Load proof (point 3)
- **30 of 30 PASS, 0 failures.** Command: `powershell -File E:\tmp-w7\load30.ps1 -Repo <worktree> -Runs 30`.
- Each run is `team-integrate.tests.ps1 -Filter "leaves behind is stopped BEFORE its diff"`, beside `script-syntax`, `installer-strictmode` and `provision` looping (1628 rounds, all exit 0).
- For runs 1–24 the full suite and other workers' suites also ran; single runs took 19–590 s.

## Point 4 and fast checks
- The three round-five cases (child, WMI before the commit, WMI after the commit) are unedited and green. The shared helper `New-LeftBehind` and the stand-in lead gained optional parameters.
- `script-syntax` 151/0, `installer-strictmode` 24/0, `provision` 13/0.

## Evidence classes
- The window, the refusal and the flow: PROVEN_AUTOMATED.
- The real `claude.exe --version` starts suspended, is in the job before it runs, and its pipes and exit code are read: PROVEN_PROXY for the start only.
- NOT_RUN: a real `claude -p` wiring run through the new start, API mode on PostgreSQL after this change, the real gate, `team-cycle.tests.ps1` (its files are untouched).

## For the lead at merge
- Nothing new to wire: the gate step, the CI line and ADR-0254 on `lead/auto-integrate-wiring` stand. Section 12 of `team/plans/cycle-auto-integrate-adr.md` is the addendum text for ADR-0254.

## Open risks
- The job has no kill-on-close limit: a step killed mid-run leaves the lead's run going, as before.
- The two held cases cost 10 s each when green.
- `Add-Type` now compiles a larger C# type on every step run.
- The killed first run left `pagentos-integ*` folders in `%TEMP%`; I did not delete them because I could not tell mine from other agents'.
