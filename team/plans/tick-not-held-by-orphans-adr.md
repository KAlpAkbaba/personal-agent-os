# ADR-0214 addendum (number by the lead): the tick waits for its script's own process and stops what the script left in its job

**Status:** accepted (worker, cycle d20261003, card `tick-not-held-by-orphans`)

## Incident

2026-10-03, local time: the pool cycle `d20261003` (pid 46484) started at 02:00 from the
scheduled task's tick (pid 50864), ended and released its lock. The tick did not end: it had
no child of its own, but two processes an agent run had left behind at 02:40 - a
`tail -n +1 -f <a worker's log>` and a `grep --line-buffered` reading it, parent gone - were
still alive. The scheduled task (MultipleInstances IgnoreNew) skipped every later trigger
(LastTaskResult 0x800710E0); no cycle ran and the Ofis page said nothing. At 04:15 the lead
stopped the two processes, the tick exited at once, and the next start ran the cycle.

## Cause

`tick.ps1` started the feeder and the cycle with `Start-Process -NoNewWindow -Wait -PassThru`.
In Windows PowerShell 5.1 `-Wait` waits for the started process AND every process it ever
started (it puts them in a job and waits for the job to empty). Any descendant that never
ends - a log follower, a server a test forgot, a suite left in the background - holds the
tick for ever. Addendum 16 (no background commands in a run) removes the commonest source,
not the class. Measured on the old tick by `scripts/tests/team-tick.tests.ps1` (red first):
still alive at the 60 s hang guard in 5 of 6 cases, and it exited 0.1-2 s after the test
stopped the leftover - the leftover alone held it.

## Decision

- The feeder and the cycle are each created SUSPENDED (CreateProcessW, inherited handles and
  console as Start-Process -NoNewWindow made them), put into a Windows job object the tick
  owns, and only then resumed: every process they ever start is in that job, whoever its
  parent is by the end.
- The tick waits for the script's OWN process (WaitForSingleObject on its handle), never for
  its descendants. Its exit code is still the cycle's; the feeder still never decides whether
  the cycle runs.
- Only after that process has exited: the processes still in the job are listed, each is
  written to the tick's log (`team/logs/tick.log`, git-ignored; `-LogPath` for the tests) as
  `pid <id> <name> (left by the feeder|cycle): <command line, first 200 characters>`, and the
  job is terminated. With `-DailyId` (the tick then knows the cycle's id) the same lines are
  appended to `team/reports/<cycle>.md` under "## Geride kalan süreçler (tick durdurdu)" when
  that file exists.
- The boundary is the job, never a name or a path: a process started outside it (the owner's,
  another session's, the dev stack) is never touched, even with the very same command line.
  Nothing of a RUNNING script is touched.
- A job that cannot be created or assigned: the script still runs, the tick still waits only
  for its own process (Start-Process without -Wait), and says ONCE in its log "what the
  feeder or the cycle leaves behind will not be stopped". No kill-on-close: a tick that is
  itself killed does not take a running cycle with it.
- Test hook: `PAGENTOS_TEAM_TICK_JOB_FAILS` (set = no job object). Unset outside the suite.

## Evidence

PROVEN_AUTOMATED: `scripts/tests/team-tick.tests.ps1` (6 cases, gate step "Agent team tick not
held by orphans", CI line), red first on the old tick, three mutation REDs (-Wait restored;
the job ended while the cycle runs; leftovers stopped by command line instead of by job).
PROVEN_REAL is the lead's: the first real tick that logs a leftover it stopped, or a night
with no tick alive after its cycle.
