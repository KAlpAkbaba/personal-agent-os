# office-stable-seats - ADR draft (for the lead: number it as an ADR-0214 addendum)

## Context

2026-10-02 15:50, the owner at the Ofis page: "ajanlar arasında işler yarı yolda değişmiş galiba",
and a warning sign over an idle seat. Nothing changed hands; the page made it look so:

- (A) `office_view` put the live worker runs on worker-1..N by their position in the status' `runs`
  list. When the run on worker-1 ended, every other run moved one seat down.
- (B) a worker task in state `returned` (reopened, runnable, waiting for a free seat) was drawn as a
  `returned` seat - standing, with the warning sign - the same picture as a task the cycle gave up.

What the pool (cycle-seat-pool, on main) gave: NO seat number. `New-CycleStatus` wrote `task`,
`role`, `started_at`, `model`; nothing existed to reuse, so the number is built here.

## Decision

1. **The rule.** `cycle.ps1` gives a worker run, when it starts, the LOWEST positive seat number no
   live worker run holds (`Get-FreeWorkerSeat`), keeps it on the run's live entry until the run ends,
   and writes it as `seat` (an integer) on the run's status entry. Runs of the other roles (lead,
   researcher, integrator, inspector) carry no `seat`.
2. **The route.** `routes.py::_Run` gains `seat: int | None = None`. **The strict-mode limit:** the
   status model is `strict=True, extra="forbid"`, so a `seat` that is not an integer (`"2"`, `2.5`,
   `true`) is a 422 for the whole heartbeat - and the cycle answers its first 422 by switching to the
   legacy status for the rest of the cycle. Only integers reach `office_view`, so its fallback absorbs
   bad INTEGERS (0, negative, a duplicate); the string/float/bool branch of the fallback is defence in
   depth for a document that reached the store some other way. Acceptable because `cycle.ps1` only
   ever writes `[int]`. (Test: `test_a_seat_that_is_not_an_integer_is_refused_by_the_strict_route`.)
3. **The legacy shape stays without `seat`.** `New-CycleStatus -Legacy` (the form a Cloud Core that
   refuses `model`/`limits` gets) writes no `seat` either: a Core old enough to refuse `model` refuses
   `seat` too, and a seat in the legacy retry would 422 again and throw. Asserted in the existing
   legacy-status case of team-cycle.tests.ps1 (no accepted legacy document carries a seat).
4. **The page's server side.** `office_view` seats a live worker run with a valid `seat` (int >= 1,
   claimed by no other live run) on `worker-<seat>`. The number of worker seats is max(4, the highest
   valid seat, the number of live worker runs). A run with no seat, a bad one or a duplicate (all
   claimants of a duplicate fall back) takes the lowest free seat, in start order - so no run is ever
   left without a seat. `running_agents` and `capacity` are computed as before.
5. **Two meanings that were one.** On a free worker seat: a task in state `stopped` (the cycle gave it
   up; the lead or the owner must act) stays `returned` and is drawn first; a task in state `returned`
   or `assigned` with a worker report that is not running only waits for its next run - the seat is
   `waiting` with that task (`task_id`, `task_title`) and `queued: true`. The other roles' seats are
   unchanged. The page draws a queued seat as the ordinary seated, idle figure with the task's title in
   the muted colour and `sırada` (where a working seat says `çalışıyor`), with NO warning; the warning
   is for state `returned` only.

## An old cycle

A running cycle keeps the code it started with: until it is restarted its status has no `seat`, and
`office_view` places every run by the unseated rule - list order onto worker-1..N, which is byte-for-
byte today's placement (the existing placement tests pass unedited). Only the new cycle moves the
picture to stable seats. Release order: the API (routes + office) first or together with the cycle
change; the cycle restarted after - else its seated status meets a Core without `seat`, gets a 422
and spends the rest of the cycle in the legacy shape (no `model`, no `limits` on the page).

Two existing tests changed because they encoded meaning (B) replaces: a `returned` worker task now
reads `waiting` + `queued` (`test_returned_worker_tasks_fill_the_free_seats_up_to_the_fourth`,
`test_a_returned_worker_task_is_shown_while_another_worker_runs`).

## The spoken summary

`speech.py` reads the office's seats, so the voice summary changes with the page: its "Bir görev
geri döndü" now counts only `stopped` worker tasks (what a person must look at); a `returned` task
that waits for its next run is no longer spoken as "geri döndü". That matches what the page draws.
`test_team_speech.py`'s two cases that said it for a `returned` task now use `stopped`.

## One number for the board and the page

`team/nightly/lead` (the team board, 2026-10-03) had already given a worker run a board seat
`worker-<n>` by the same rule (lowest free, for the run's life) but kept it out of the status. The
merge keeps one number: `Get-FreeWorkerSeat` gives the run its integer `seat` (in the status), and
the board's `PAGENTOS_TEAM_SEAT` is `worker-<seat>` (empty above 9, as before); other roles keep
their role as the board seat and carry no `seat`.

## What the owner sees now

When one worker finishes, the others stay on their seats; a new run sits on the lowest empty desk.
A task waiting for its turn sits at a desk reading `sırada` in grey, no warning sign; a warning sign
means something came back that a person must look at.
