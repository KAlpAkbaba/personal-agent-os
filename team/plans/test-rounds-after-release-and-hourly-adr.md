# ADR draft: test rounds after every staging release and on an interval (test-rounds-after-release-and-hourly)

Status: proposed (the lead numbers it and moves it into docs/DECISIONS.md)
Date: 2026-10-07

## Context

`scripts/team/cycle.ps1 -TestTeam` started the test team's round once, at the cycle's start. A
cycle lives up to 12 hours; on 2026-10-06 the 21:48 round ended within the hour and the five test
seats showed "iş bekliyor" for the rest of the evening (the owner: "neden testçiler iş bekliyor").

## Decision

While the cycle lives, `Invoke-CycleTestRoundTick` (main loop, every poll) owes a round when:

1. the cycle starts (as before);
2. the sha staging serves changes - `release.version` of `/v1/system/health` on
   `127.0.0.1:28001` (`-StagingHealthUrl`), read at most every `-StagingPollSeconds` (60);
   an unreadable staging is no release;
3. `test_round_every_hours` (team/cycle-settings.json; 0 to 48, default 2, 0 = off) has passed
   since the last round started. Read at every look: a changed setting counts mid-cycle.

Never two rounds at once: the round's own process is the lock - the process this cycle started,
or the one `team/reports/test-round.json` names (pid + process start ticks, so a reused pid is not
taken for a round), which an earlier cycle process of the machine wrote. A reason that arrives while
a round runs is kept and served when it ends; at the cycle's start a running round covers the
start (said, no second round). `test_parallel` 0 starts nothing (one risk line); the memory floor
and the gate's heavy slot stay the round's own to measure and say (Get-TeamTestCap).

Each round has its own name `t-<cycle>-<n>` (n = first number with no `test-round-<n>.log` in the
cycle's report folder; cycle part trimmed so the name is at most 41 characters), so its folder,
cards and log never mix with an earlier round of the same (daily) cycle id.

## Consequences

- The test seats are fed after every release that reaches staging and every two hours.
- `test-round.log` is now `test-round-<n>.log` (nothing read the old name).
- Known, not changed here: a round started by `Start-Process` inherits the cycle's output handles,
  so a caller that captures the cycle's output waits until the round ends (the tests hold the lock
  with a process they start themselves for that reason).
