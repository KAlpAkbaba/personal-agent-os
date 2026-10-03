# ADR (unnumbered; lead numbers it) - researcher-every-cycle: research is the cycle's default

Context: ADR-0214 addendum 5 (owner, 2026-10-01): the researcher runs in EVERY cycle. The pool
(addendum 8/15) already gives the researcher its own seat beside the tasks' runs, and addendum 11
already posts every waiting idea's text to the store. What was left: `cycle.ps1` ran the
researcher only with `-Research`.

Decision:
1. `cycle.ps1` runs the researcher by default. `-NoResearch` turns it off; `-Research` is still
   accepted and changes nothing (a scheduled task registered with it keeps working);
   `-ResearchOnly` and `-ResearchEveryHours` are unchanged.
2. `register-nightly.ps1` no longer passes `-Research`.
3. The test harness (`Invoke-Cycle` in `team-cycle.tests.ps1`) passes `-NoResearch` unless a test
   asks for the researcher (`-Research`, `-ResearchOnly`) or for the script's default
   (`-DefaultResearch`), so the existing tests keep their exact call lists.

Not rebuilt (already on the base branch): the run beside the first refill, the live status entry
`{task: cycle, role: researcher}`, the proposal POST (`Send-IdeaTexts`), the usage-limit rules of
`Resume-OwnRun`. New tests pin them under the default.

4. A researcher at the usage limit follows the rule of every run, and THE CYCLE was fixed (not
   the test): when a lower model is left, the run restarts on it. With no model left, the cycle
   waits out the limit, or with `-WaitForUsageLimit:$false` it stops starting new runs and writes the limit's own stop
   line ("Max kullanım limiti; ... aynı -CycleId ile yeniden başlat"). Stopping in that mode IS the
   intended rule (the same as a worker's limit). What was wrong: `Complete-Research` also wrote
   "araştırmacı: başarısız: Max kullanım limiti" under the stops, calling the limit the
   researcher's failure. A limited researcher run now writes no such line and no
   `research-last.txt` marker (the next cycle runs it again). The test now also accepts
   `inspecting` for the task beside it: its worker finished, and the stopped cycle starts no
   inspection.

Consequence: a hand-started cycle now spends one researcher run unless `-NoResearch` is given.
