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
- A tester's notes go under its JOB's id, `tj-<round>-<n>` (the card id; it matches the board's
  `TASK_PATTERN ^[a-z0-9][a-z0-9-]{2,63}$`). This covers the round's 'iş:' / 'sonuç:' notes for
  the card and the tester run's own notes (`PAGENTOS_TEAM_TASK` = the card id).
- `test-team/<seat>` was rejected: `/` is not in `TASK_PATTERN` (422).
- The round's own notes (the cap reasons, the forwarded failures, the breaking report to the
  Danışman, a retest reopening) go under the round id. A round id shorter than three characters
  does not match `TASK_PATTERN`; such a round falls back to `test-<round>`.
- The guard stays per job: one job over 20 notes in an hour is still refused. Never a fresh task
  per note.
- The Ofis' Test odası (`officeTestRoom.tsx`) reads a note by `seat` and text only, never by
  task, so it needs no change.

## Evidence

`scripts/tests/testteam-board-notes.tests.ps1`: a stand-in board holding board.py's
`TASK_PATTERN` and `RATE_PER_TASK_HOUR` (read from the source). On the current
`test-round.ps1`: 5 jobs x 6 notes -> 11 refused (429, task `test-team`), the same failure as
t-r10070152.
