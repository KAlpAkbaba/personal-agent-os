# ADR draft: the scheduled tick asks for the test team's round by default (test-team-in-the-tick)

Status: proposed (worker, cycle d20261006). The lead numbers it.

## Context

The test team (card test-team, released 2026-10-06 as 2568bfc5) never ran: `cycle.ps1` starts a
round of `scripts/testteam/test-round.ps1` only with `-TestTeam`, and `scripts/team/tick.ps1`
(what the scheduled task "PagentOS Team Nightly Cycle" runs through the wrapper) had no such
parameter. The five test seats showed "iş bekliyor" all day beside a healthy staging.

## Decision

- `tick.ps1` passes `-TestTeam` to the cycle **by default**. `-NoTestTeam` opts out; `-TestTeam`
  is accepted and says the default out loud; both together are refused before anything runs.
- Before asking for the round the tick asks staging's health (`-StagingHealthUrl`, default
  `http://127.0.0.1:28001/v1/system/health`, 15 s). Anything but a 200: the cycle runs **without**
  `-TestTeam`, and one risk line ("test ekibi turu başlamadı: staging (...) yanıt vermedi; döngü
  test ekibi olmadan çalıştı") goes to the tick log, to the board (seat test-lead, task test-team,
  kind bilgi), and - with `-DailyId`, once the cycle has ended - under "## Riskler (tick)" in the
  cycle's report. Never a failure: the tick's exit code stays the cycle's.
- The board note is sent to the queue the tick was given (`-Url $QueueUrl -TokenFile $QueueToken`,
  the address cycle.ps1 gives its agents as PAGENTOS_TEAM_URL): the scheduled task's environment
  has no PAGENTOS_TEAM_URL, and without the arguments board.ps1 said UYARI and the note was lost.
  A tick without `-QueueUrl` leaves board.ps1 to its own defaults.
- Every other argument the tick forwards is unchanged, word for word; `-TestTeam` sits after
  `-Base` and before the queue arguments.
- The scheduled task and the wrapper are not changed: the default already turns the round on.

## Consequences

- The staging check is made once, when the tick starts: a staging that comes up later in a
  12-hour cycle gets no round until the next tick (the round itself still refuses non-staging
  hosts and measures its own cap).
- `-BoardPath` is a test hook (a fake board.ps1); the suite never reaches the real staging or the
  real board (`team-tick.tests.ps1` gives every case a closed port and a fake board by default).
