# ADR (unnumbered, the lead numbers it): a second return that is a new finding does not stop the task - the rule layer

Card `second-return-rules`, cycle d20261003. Proposal: `team/proposals/2026-10-03-ikinci-donus-yeni-bulgu.md`.
Code: `scripts/lib/TeamArea.ps1` (`Get-TeamPriorFindings`, `Resolve-TeamReturnStop`,
`$script:TeamPriorFindingsKey`, `$script:TeamReturnsHardCap`); tests: `scripts/tests/team-area.tests.ps1`,
cases `second-return *`.

## Context

`Get-TeamStateAfterInspection` stops a task at its second RETURN, whatever the return was about. In
d20261002 the lead reopened two of the four stopped tasks by hand because "the stop was only the
second-return rule": the first return's items were closed and the second return was a new finding
(`understanding-rules-read-lemmas`, `cycle-auto-integrate`). ADR-0253 settled the same shape for files
outside the area; this is the second half: a rule the cycle can execute instead of a lead's judgement.

## Decision

1. **The contract line.** The inspector's RETURN report carries ONE line, alone on its line, above the
   verdict: `onceki_bulgular: kapandi` or `onceki_bulgular: acik [n, n]` (the previous RETURN's items still
   open; `acik` with an empty or missing list is still acik). The key is case-sensitive. Same tolerance and
   strictness as `alan_disi:` (ADR-0253 rule 1): backticks, asterisks and surrounding spaces are ignored;
   the key starts the line and nothing may follow the value or the closing bracket; a bullet, quote mark or
   numbering before the key makes it prose; the LAST such line wins. Anything else after the key
   (`kapandi.`, `kapandı`, `kapandi ama`, `Onceki_bulgular`), or no line at all, is acik.
2. **The order** (`Resolve-TeamReturnStop -Task -Report`, the shape of the RETURN branch plus `Extra`):
   Returns (= task `returns` + 1) >= 3 -> stopped whatever the line says; Returns 1 -> returned (no line
   needed); Returns 2 and `kapandi` -> returned, `Extra = true`; Returns 2 otherwise -> stopped with
   today's reason `ayni is iki kez geri verildi`, plus `; açık kalan maddeler: n, n` when known. Every
   stopped reason starts with `Get-TeamStateAfterInspection`'s own text (a test reads it from
   `TeamQueue.ps1`, with `$script:TeamMaxReturns`, so the two cannot drift).
3. **A missing or malformed line stops.** The default is today's behaviour. An inspector that forgot the
   line, or wrote it in other letters, must not buy a round: only a deliberate, exact `kapandi` does.
4. **The third return stops regardless** (`$script:TeamReturnsHardCap = 3`). Two consecutive "new findings"
   on one card mean the card or the worker is the problem; that is the lead's to judge.
5. **Cost bound:** at most one extra worker + inspector run per task (~2-4 USD, the same run the lead's
   hand-reopen already paid). The run cap of 8 (`cycle.ps1 -MaxRunsPerTask`) is untouched and still bounds
   everything.

## What the wiring card must do

In `cycle.ps1`, for a RETURN/NONE verdict, AFTER `Test-TeamAreaReturnCounts` has said the return counts,
call `Resolve-TeamReturnStop -Task $task -Report $report` in place of `Get-TeamStateAfterInspection`'s
RETURN branch, and write State/Returns/Reason as today (Extra may be shown in the status document).
`Get-TeamStateAfterInspection` itself is unchanged by this card. The role-line card adds the contract
line to `.claude/agents/inspector.md` (mandatory on the second and later returns).

## Evidence

PROVEN_AUTOMATED: team-area suite RED first (117 passed, 16 failed), then 133/0 under PS 5.1; three
mutations RED with sha256 restore. PROVEN_REAL belongs to the wiring card's first real cycle.
