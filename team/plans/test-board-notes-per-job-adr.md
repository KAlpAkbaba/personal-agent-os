# ADR draft: the test team's board notes are written per job (test-board-notes-per-job)

Status: proposed (worker-3, cycle d20261006). Number: the lead's.

## Context

2026-10-07 02:39, round t-r10070152 (staging da3e26b9): the round's log ended with
`PANO REDDETTI (HTTP 429) ... test-team has 20 notes in the last hour; at most 20`. The board
(`services/api/app/team/board.py`) allows `RATE_PER_TASK_HOUR` (20) notes per task per hour.
`scripts/testteam/test-round.ps1` posts every note under the one task `test-team` (`Send-Note`)
and starts every tester run with `PAGENTOS_TEAM_TASK = "test-team"`. Five seats of one round
share 20 notes an hour; the Ofis' Test odası then shows stale seats (an 'iş:' with no 'sonuç:').

## Decision

- The board's limit is not raised: it guards the Cloud Core.
- `Get-TestTeamBoardTask -Id <id>` (`scripts/testteam/TestTeam.ps1`) names the task a note is
  posted under. It holds board.py's `TASK_PATTERN` (`$script:TestTeamBoardTaskPattern`, a test
  compares the two) and compares with `-cmatch` (the board's `re` is case-sensitive; PowerShell's
  `-match` is not, which is why test-round.ps1's own `-Round` check lets `R1` in):
  - the id is lowercased (`R1` -> `r1`, `tj-R1-1` -> `tj-r1-1`);
  - still too short for the board (`t1`, `a`) -> `test-<id>`;
  - empty, or anything else off the pattern (`x/y`, `a_b`, 65 chars) -> thrown, never posted to
    be refused. Empty is refused on its own: it would become `test-`, which the board takes -
    one shared task again.
- A tester's notes go under its JOB's task, `Get-TestTeamBoardTask -Id <card id>` (`tj-<round>-<n>`):
  the round's 'iş:' / 'sonuç:' notes for the card and the tester run's own notes
  (`PAGENTOS_TEAM_TASK`).
- `test-team/<seat>` was rejected: `/` is not in `TASK_PATTERN` (422).
- The round's own notes (the cap reasons, the forwarded failures, the breaking report to the
  Danışman, a retest reopening) go under the round's task, `Get-TestTeamBoardTask -Id $Round`.
- The guard stays per job: one job over 20 notes in an hour is still refused. Never a fresh task
  per note.
- The Ofis' Test odası (`officeTestRoom.tsx`) reads a note by `seat` and text only, and the board
  route has no task filter, so it needs no change.

## The wiring (scripts/testteam/test-round.ps1, outside this card's area)

```
 if ($Round -notmatch '^[a-z0-9][a-z0-9-]{0,40}$') { throw ... }
+# The board allows RATE_PER_TASK_HOUR notes per task: the round posts under its id, a job under its card id.
+$roundTask = Get-TestTeamBoardTask -Id $Round
-    param([string]$Seat, [string]$Text, [string]$To = "")
+    param([string]$Seat, [string]$Text, [string]$To = "", [string]$Task = $roundTask)
-    ... "-Task", "test-team", "-Kind" ...
+    ... "-Task", $Task, "-Kind" ...
-    param([string]$Role, [string]$Prompt, [string]$Seat)
+    param([string]$Role, [string]$Prompt, [string]$Seat, [string]$Task = $roundTask)
-    $environment = @{ PAGENTOS_TEAM_SEAT = $Seat; PAGENTOS_TEAM_TASK = "test-team" }
+    $environment = @{ PAGENTOS_TEAM_SEAT = $Seat; PAGENTOS_TEAM_TASK = $Task }
 tester start:   Start-RoleProcess ... -Seat $card.tester -Task (Get-TestTeamBoardTask -Id $card.id)
 'iş:' note:     Send-Note -Seat $card.tester -Task (Get-TestTeamBoardTask -Id $card.id) -Text ...
 'sonuç:' note:  Send-Note -Seat $entry.Card.tester -Task (Get-TestTeamBoardTask -Id $entry.Card.id) -Text ...
```

## Evidence

`scripts/tests/testteam-board-notes.tests.ps1` (10 cases): a stand-in board holding board.py's
`TASK_PATTERN` (case-sensitive) and `RATE_PER_TASK_HOUR`, both read from the source.
- On the branch's test-round.ps1: 6 rule/helper/Ofis cases green, the 4 end-to-end cases red
  (5 jobs x 6 notes -> 11 refused, 429 `test-team`, as t-r10070152).
- On a scratch copy with the wiring above: 10/10. Mutations of the wiring: the tester run back
  under the round task -> 5x6 and guard cases RED; a fresh task per note -> guard and round-note
  cases RED.
- Mutations of `Get-TestTeamBoardTask` (no lowercase / no `test-` fallback / no empty guard /
  pattern drift) -> each RED; restored from a backup, sha256 equal.

Follow-up: `scripts/team/tick.ps1` posts the cycle's test-lead note under `test-team` (1-2 an
hour, no quota problem); for consistency it can take the round's task.
