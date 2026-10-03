# ADR draft — addendum to the integration step's ADR (`scripts/team/integrate.ps1`): its own lock, a gate the wiring cannot shrink, the retry's reset held

Card `integrate-own-lock` (cycle d20261003). The card calls the step's ADR "ADR-0254"; in
`docs/DECISIONS.md` it is **ADR-0260** ("Merged work is gated and put on main by a step"; ADR-0254
is the misheard notebook). This draft is an addendum to ADR-0260 and carries out its "At merge
(the lead)" rulings 1 and 3 and the fifth inspection's surviving mutation. The lead numbers it.

**Decision.**

1. **The step's own lock (API mode).** Before anything is run, `integrate.ps1 -QueueUrl` takes a
   lock of its own: a machine-local file outside the repository (`%LOCALAPPDATA%\PagentOS\
   integrate-step.lock`; `-StepLockPath` for the tests), created exclusively (`CreateNew`),
   holding the process's pid, its start time, the machine and the time, and then KEPT OPEN
   read-only without delete sharing while the step lives. A second step that finds it held by a
   live process (pid alive AND started when the file says - a reused pid is not the holder; a
   file that cannot be opened is being written by a live one) writes the existing skipped line
   (`kilit bu makinenin başka bir entegrasyon adımında (pid N, ...)`) and exits 3, nothing else
   written. A file whose holder is gone is deleted, taken, and said in the report's risks
   (`... kilidi devralındı (pid N, ...)`). It is released in `finally` on every path. File mode
   does not take it: there the cycle's lock is taken exactly as before (one writer of the file).
2. **`-BesideCycle` (API mode).** With the switch the cycle's lock is never read, taken, released
   or written: the cycle runs while the gate does (owner's rule, ADR-0214 addendum 8 (c)).
   WITHOUT it the step behaves as before in API mode - it refuses to start while a live cycle
   (or the other machine) holds the cycle's lock, and holds that lock as `integrate-<branch>`
   for the whole gate - so cycles, and their inspectors' use of the dev database, still do not
   start under a gate. **Order:** the scheduled call passes `-BesideCycle` only after card
   `gate-own-database` is on main (the gate then resets a database of its own, ADR-0260 open
   decision 5 (b)); until then the step is called without it. `-BesideCycle` in file mode is
   ignored and said.
3. **A result the cycle overtook is dropped.** In API mode every queue write of the step reads
   the branch's tasks from the store again first (`Save-Queue` -> `Merge-TeamStepResults`). A
   task's result is written only when the store still has it `merged`, on the same
   `integration_branch`, with the same `sha` as when the step took it; it is then written as the
   store's copy with the step's own changed fields on top (another writer's other fields stay).
   Otherwise its result is dropped, the store's word stands, and the report names it
   (`kapı koşarken döngü değiştirdi, sonucu YAZILMADI: <id> (<field> '<was>' -> '<now>')`); the
   branch's other tasks are written. The shape of ADR-0214 addendum 11 for the cycle. Consequence:
   a task changed only in another field (a title) no longer makes the write fail with exit 12;
   the "verdict not written" recovery (ADR-0260 point 9) now covers a store that refuses the write.
4. **The wiring may grow the gate, never shrink it** (ruling 3, option (c), widened as the card
   asks). When the committed wiring diff holds `scripts/quality-gate.ps1`, the script compares
   the file at the commit before the run and at the wiring commit (`Get-TeamGateShrink`): a line
   holding `Invoke-Step` or `Assert-ExitCode` that the new file has fewer of (removed or renamed;
   trimmed, case-sensitive, counted - a moved line is not removed; a `#` comment is not a step),
   or a `Write-Host "QUALITY GATE: PASS"` line that is gone, refuses the run whole like a
   disallowed file: tree reset, nothing merged, `lead_refused` counted, and the report and every
   task's reason quote the line (`lead koşusu kapıyı küçülttü (scripts/quality-gate.ps1): silinen
   satır: ...`). Adding steps, and editing comments or a step's body around those lines, pass.
5. **The retry's reset is held by a test.** A limited lead run that wrote (inside a task's area
   and under `docs/`) before answering "out of usage credits": the retry one model down starts
   on a clean tree and its commit holds neither file. With the reset removed, that test is red
   and the two older limit tests stay green (the fifth inspection's survivor).

**Not done here.** Scheduling (`register-nightly.ps1`) stays the lead's, after
`gate-own-database`. The real gate beside a real cycle has not been run (NOT_RUN: the step is
not scheduled). The step's lock file is per machine, like the gate records.

**Evidence.** PROVEN_AUTOMATED: `scripts/tests/team-integrate.tests.ps1`, ten new cases (eight
through the step in a sandbox repository with the fake gate, the lead stand-in and the fake team
API, two on the rules), five mutations red and restored.
