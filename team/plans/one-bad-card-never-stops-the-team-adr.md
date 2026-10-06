# ADR draft: one task's protocol problem sets that task aside; only the queue's own problems stop the team

Task: one-bad-card-never-stops-the-team (cycle d20261006). Number: the lead's.

## Context

Measured 2026-10-06 21:02-21:16: the first automatic test round forwarded 19 `test-fail-*` cards
as `proposed` with no area; seven were moved to `assigned` as they were. `Test-TeamQueue` said
"a task that is being worked on names its file area" for each, and the cycle answered the WHOLE
queue with "the queue breaks the protocol; nothing was run" (exit 2) - every worker seat slept
for over an hour until the Danışman moved the seven back.

## Decision

1. `Get-TeamQueueProblems` (scripts/lib/TeamQueue.ps1) sorts the protocol's problems by whose
   they are. The QUEUE's: not JSON, version, no task list, an id missing / malformed / used
   twice (a task that cannot be named cannot be set aside by name), a task depending on itself,
   a longer cycle in `depends_on` (new check). Everything else is ONE TASK's (fields, state,
   area, branch, a dependency that is not in the queue, budget, work without an area, an
   overlapping area). `Test-TeamQueue` returns the same sentences as before, flattened.
2. The cycle stops (exit 2) only for the queue's problems. A task with a problem of its own is
   set aside (`Set-TeamTasksAside`): state `stopped`, the problem as `reason` (prefixed
   `alan yok: önce dosya alanı` when the area is missing), a risk line
   `Danışman'a iletildi: kenara alındı: <id>: ...` in the report; the report's "Durdurulanlar"
   and the Ofis read the reason from the task. Done at the start (after the lock) and at every
   re-read of the store (Sync-Queue; a run in flight is the cycle's copy and is not touched).
3. A `stopped` task is held to the queue's rules only. A set-aside task held to every rule
   would stop the queue again at the next read; so the reason is written once. When somebody
   moves it out of `stopped` the rules apply again.
4. `Get-TeamMoveRefusal`: a move into a worked state (assigned, in_progress, inspecting,
   returned) without an area is refused, `alan yok: önce dosya alanı`. The cycle's
   approved -> assigned move stops such a task with that reason (it becomes the Proje
   Yöneticisi's duty, whose `grant_and_return` can give it an area); the duty's return path
   refuses with the same sentence.
5. test-round's forwarded cards carry a first area from the family's known code paths
   (`Get-TestTeamFamilyArea`: nobet, ev-stoku, alarm, dil-dayanikliligi, yanlis-duyulan, saglik);
   an unknown family carries none. The Proje Yöneticisi widens rather than invents.

## Not done here (outside the card's area)

- `scripts/team/feed.ps1` still refuses the whole queue on any problem (lines 169 and 505). Until
  it reads `Get-TeamQueueProblems(...).Queue`, the feeder waits for the next cycle to set the task
  aside. Follow-up card.
- The Onay Merkezi (services/api/app/team) still lets a task move to `assigned` without an area;
  the cycle now sets such a task aside instead of stopping, but the API should refuse the move.

## Consequences

One bad card costs that card, not the team. The cost: a stopped task's field-level defects are
no longer reported while it is stopped (it never runs).
