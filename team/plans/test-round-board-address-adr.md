# ADR draft: the test team's round gets the board's address from the cycle

- Task: test-round-board-address (cycle d20261006)
- Status: proposed (the lead numbers it at merge)

## Context

Measured 2026-10-06: every test round printed `pano: UYARI: ekip panosu kullanılamıyor (panonun
adresi verilmedi (-Url ya da PAGENTOS_TEAM_URL))`. `scripts/team/cycle.ps1` hands its own agent
runs `PAGENTOS_TEAM_URL` / `PAGENTOS_TEAM_TOKEN_FILE` through `Start-TeamRun -Environment`, but
`Start-CycleTestRound` starts `scripts/testteam/test-round.ps1` with `Start-Process`, which in
Windows PowerShell 5.1 has no environment parameter: the round only saw what the cycle's own process
happened to carry. The test seats never posted, the Ofis showed them "iş bekliyor" while they worked.
The Danışman set both variables in the scheduled tick's wrapper as a stopgap.

## Decision

`Start-CycleTestRound` sets the two variables on its own process for the duration of the
`Start-Process` call and puts the inherited values back in `finally`:

- API mode: `PAGENTOS_TEAM_URL` = the queue URL, `PAGENTOS_TEAM_TOKEN_FILE` = the token file's PATH
  (never the token);
- file mode: both removed for the start, so a round never posts to an address the cycle was not given.

Nothing else changes (arguments, log files, the round's seats).

## Consequences

- The wrapper's stopgap becomes redundant; it can stay (same values) or be removed by the Danışman.
- Proof: `team-cycle.tests.ps1` case "the test team's round is told the board's address too ...";
  a fake round records its environment. Two mutations RED (no set; file mode inherits a decoy URL).
- Seen on the way, outside the area: `Start-TeamRun` (scripts/lib/TeamRun.ps1) does not clear the
  inherited board address in file mode either, so the existing case "each run is told how to reach
  the team's board" fails whenever the test shell itself carries `PAGENTOS_TEAM_URL` (as every
  cycle run now does). A follow-up card should remove the two variables there in file mode.
